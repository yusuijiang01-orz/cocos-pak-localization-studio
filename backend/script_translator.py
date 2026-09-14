#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, importlib.util, json, re, shutil, sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from parallel_config import worker_count
from localization_tm import init_db, lookup_no_touch
from tsv_localization import token_template


PLACEHOLDER_RE = re.compile(r"\{P\d+\}")
VIETNAMESE_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊÒỎÕÓỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]")
LATIN_WORD_RE = re.compile(r"[A-Za-zÀ-ỹ]{2,}")
ALLOWED_LATIN_TOKENS = {"NPC", "PK", "PVP", "PVE", "VIP", "HP", "MP", "EXP", "ID", "GM", "FPS"}


def default_translate_script() -> Path:
    candidates = [
        Path.cwd() / "translate.py",
        Path(__file__).resolve().parent / "translate.py",
        Path.cwd() / "pak" / "updatefs" / "translate.py",
        Path(r"D:\App\Frida Android\pak\updatefs\translate.py"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def load_translate_function(script_path: Path):
    script_path = Path(script_path)
    if not script_path.exists():
        raise FileNotFoundError(f"翻译脚本不存在：{script_path}")
    spec = importlib.util.spec_from_file_location("external_translate_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载翻译脚本：{script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "translate_string", None)
    if not callable(fn):
        raise RuntimeError("翻译脚本必须提供 translate_string(text) 函数")
    return fn


def protect_placeholders(text: str):
    mapping = {}
    def repl(match):
        key = f"\x00PH{len(mapping) + 1}\x00"
        mapping[key] = match.group(0)
        return key
    return PLACEHOLDER_RE.sub(repl, text or ""), mapping


def restore_placeholders(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def translate_cell(text: str, translate_fn) -> str:
    protected, mapping = protect_placeholders(text)
    translated = translate_fn(protected)
    return restore_placeholders(translated, mapping)


def incomplete_translation(text: str) -> bool:
    """Reject dictionary fragments that still contain ordinary Vietnamese/Latin words."""
    if not text or "�" in text or VIETNAMESE_RE.search(text):
        return True
    if re.search(r"^\$[A-Za-z]{1,12}(?=[\u3400-\u9fff])", text):
        return True
    # Do not accept a result merely because it contains some Chinese.  Google
    # frequently translates only the tail of Sino-Vietnamese labels and leaves
    # a Latin fragment in front (for example ``$H董马赫罗`` or
    # ``T出来ng 备橙破军``).  Those mixed fragments are corruption, not valid
    # localized text, and must remain eligible for trusted PC/TM recovery.
    scrubbed = PLACEHOLDER_RE.sub(" ", text)
    scrubbed = re.sub(r"<[^>]*>|%[-+#0-9.]*[sdifouxX]|\\[^\s|]+", " ", scrubbed)
    words = LATIN_WORD_RE.findall(scrubbed)
    return any(word not in ALLOWED_LATIN_TOKENS for word in words)


def csv_text_from_tm_target(target: str, meta: dict) -> str:
    if not meta:
        return target
    if "{TEXT}" in (meta.get("template") or ""):
        return token_template(target)["text"]
    return target


def translate_csv_file(src: Path, dst: Path, script_path: Path, index: dict | None = None, db_path: Path | None = None) -> dict:
    translate_fn = load_translate_function(script_path)
    db = init_db(db_path) if db_path else None
    dst.parent.mkdir(parents=True, exist_ok=True)
    rows = changed = residual_vi = tm_hits = script_hits = rejected_partial = 0
    residual_samples = []
    try:
        with src.open("r", encoding="utf-8-sig", newline="") as fin, dst.open("w", encoding="utf-8-sig", newline="") as fout:
            reader = csv.DictReader(fin)
            if not reader.fieldnames or "id" not in reader.fieldnames or "text" not in reader.fieldnames:
                shutil.copy2(src, dst)
                return {"source": str(src), "output": str(dst), "rows": 0, "changed": 0, "skipped": "not localization csv"}
            writer = csv.DictWriter(fout, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                row_id = row.get("id", "")
                original = row.get("text", "")
                meta = (index or {}).get(row_id, {})
                source_for_tm = meta.get("source") or original
                translated = None
                if db is not None:
                    hit, _kind = lookup_no_touch(db, source_for_tm)
                    if hit:
                        translated = csv_text_from_tm_target(hit, meta)
                        tm_hits += 1
                if translated is None:
                    candidate = translate_cell(original, translate_fn)
                    if candidate != original and not incomplete_translation(candidate):
                        translated = candidate
                        script_hits += 1
                    else:
                        translated = original
                        if candidate != original:
                            rejected_partial += 1
                if translated != original:
                    changed += 1
                if VIETNAMESE_RE.search(translated):
                    residual_vi += 1
                    if len(residual_samples) < 10:
                        residual_samples.append({"id": row_id, "text": translated[:160]})
                row["text"] = translated
                writer.writerow(row)
                rows += 1
        return {"source": str(src), "output": str(dst), "rows": rows, "changed": changed, "tm_hits": tm_hits, "script_hits": script_hits, "rejected_partial": rejected_partial, "residual_vi": residual_vi, "residual_samples": residual_samples}
    finally:
        if db is not None:
            db.close()


def _translate_task(args):
    src_s, in_root_s, out_root_s, script_s, index, db_s = args
    src = Path(src_s)
    rel = src.relative_to(Path(in_root_s))
    return translate_csv_file(src, Path(out_root_s) / rel, Path(script_s), index, Path(db_s) if db_s else None)


def translate_csv_tree(input_dir: Path, output_dir: Path, script_path: Path | None = None, workers: int | None = None, db_path: Path | None = None):
    script_path = Path(script_path) if script_path else default_translate_script()
    files = sorted(p for p in input_dir.rglob("*.csv") if p.is_file() and not p.name.startswith("_"))
    wc = worker_count(workers)
    index_path = input_dir / "_tsv_localization_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    jobs = [(str(p), str(input_dir), str(output_dir), str(script_path), index, str(db_path) if db_path else "") for p in files]
    if wc <= 1 or len(jobs) < 4:
        results = [_translate_task(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=wc) as ex:
            results = list(ex.map(_translate_task, jobs, chunksize=1))
    output_dir.mkdir(parents=True, exist_ok=True)
    for meta_name in ("_tsv_localization_index.json", "_tsv_localization_export_report.json"):
        src_meta = input_dir / meta_name
        if src_meta.exists():
            shutil.copy2(src_meta, output_dir / meta_name)
    report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "script": str(script_path),
        "csv_files": len(results),
        "rows": sum(r.get("rows", 0) for r in results),
        "changed": sum(r.get("changed", 0) for r in results),
        "tm_hits": sum(r.get("tm_hits", 0) for r in results),
        "script_hits": sum(r.get("script_hits", 0) for r in results),
        "rejected_partial": sum(r.get("rejected_partial", 0) for r in results),
        "residual_vi": sum(r.get("residual_vi", 0) for r in results),
        "workers": wc,
        "files": results,
    }
    (output_dir / "_script_translation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv):
    if len(argv) not in (3, 4):
        print("Usage: script_translator.py <input_csv_dir> <output_csv_dir> [translate.py]")
        return 2
    script = Path(argv[3]) if len(argv) == 4 else None
    print(json.dumps(translate_csv_tree(Path(argv[1]), Path(argv[2]), script), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
