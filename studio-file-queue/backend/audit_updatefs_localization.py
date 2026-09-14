from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from localization_analyzer import (  # noqa: E402
    decode_best,
    has_editable_natural_text,
    is_resource_reference,
    is_visible_ini_key,
    is_visible_tsv_column,
)
from lua_localization import iter_lua_strings, iter_lua_text_parts  # noqa: E402


NUMERIC_RE = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?(?:\s*[,|;:]\s*[-+]?\d+(?:\.\d+)?)*\s*$")
TAG_RE = re.compile(r"<[^<>\r\n]{1,512}>")


def decoded(data: bytes) -> tuple[str, str]:
    text, _encoding, language, _score = decode_best(data)
    return text.strip(), language


def cell_kind(data: bytes) -> str:
    text, language = decoded(data)
    if not text:
        return "empty"
    if NUMERIC_RE.fullmatch(text):
        return "numeric"
    if is_resource_reference(text):
        return "resource"
    remainder = TAG_RE.sub("", text).strip()
    if not remainder:
        return "tag_only"
    if language in {"vi", "mixed", "zh"} and has_editable_natural_text(text):
        return language
    return "other"


def audit_tsv(root: Path) -> dict:
    headers: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[dict]] = defaultdict(list)
    files = []
    for path in sorted(root.glob("*.tsv")):
        lines = path.read_bytes().splitlines()
        if not lines:
            continue
        raw_headers = lines[0].split(b"\t")
        names = [decoded(value)[0] or f"<column-{index + 1}>" for index, value in enumerate(raw_headers)]
        file_stats = []
        for index, name in enumerate(names):
            counts = Counter()
            for row_no, line in enumerate(lines[1:], 2):
                cells = line.split(b"\t")
                if index >= len(cells):
                    continue
                kind = cell_kind(cells[index])
                counts[kind] += 1
                if kind in {"vi", "mixed", "zh"} and len(examples[name]) < 5:
                    examples[name].append({"file": path.name, "row": row_no, "text": decoded(cells[index])[0][:160]})
            headers[name].update(counts)
            file_stats.append({"header": name, "column": index + 1, "visible": is_visible_tsv_column(name, path.name), "counts": dict(counts)})
        files.append({"file": path.name, "columns": file_stats})
    summary = []
    for name, counts in headers.items():
        natural = sum(counts[key] for key in ("vi", "mixed", "zh"))
        dangerous = counts["resource"] + counts["numeric"] + counts["tag_only"]
        current = any(column["header"] == name and column["visible"] for item in files for column in item["columns"])
        if current:
            decision = "translatable"
        elif natural and dangerous == 0:
            decision = "review"
        else:
            decision = "protected"
        summary.append({"header": name, "decision": decision, "current_visible": current, "natural": natural, "dangerous": dangerous, "counts": dict(counts), "examples": examples[name]})
    summary.sort(key=lambda item: (-item["natural"], item["header"].lower()))
    return {"files": len(files), "headers": summary, "file_details": files}


def audit_non_tsv(root: Path) -> dict:
    result = {}
    for suffix in (".lua", ".ini", ".txt"):
        stats = Counter()
        examples = defaultdict(list)
        paths = sorted(root.glob(f"*{suffix}"))
        for path in paths:
            for row_no, line in enumerate(path.read_bytes().splitlines(), 1):
                if suffix == ".lua":
                    all_literals = list(iter_lua_strings(line))
                    parts = list(iter_lua_text_parts(line))
                    stats["quoted_literals"] += len(all_literals)
                    stats["editable_spans"] += len(parts)
                    stats["protected_tags"] += sum(len(TAG_RE.findall(decoded(literal.content)[0])) for literal in all_literals)
                    candidates = [part.content for part in parts]
                elif b"=" in line:
                    key, value = line.split(b"=", 1)
                    key_text = decoded(key)[0]
                    stats["visible_keys" if is_visible_ini_key(key_text) else "protected_keys"] += 1
                    candidates = [value] if is_visible_ini_key(key_text) else []
                else:
                    candidates = line.split(b"\t")
                for value in candidates:
                    kind = cell_kind(value)
                    stats[kind] += 1
                    if kind in {"vi", "mixed", "zh"} and len(examples[kind]) < 8:
                        examples[kind].append({"file": path.name, "row": row_no, "text": decoded(value)[0][:160]})
        result[suffix] = {"files": len(paths), "counts": dict(stats), "examples": dict(examples)}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit player-visible localization boundaries in updatefs.")
    parser.add_argument("root", nargs="?", type=Path, default=ROOT / "pak" / "v587+" / "_raw_reference" / "updatefs")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "updatefs_localization_audit.json")
    args = parser.parse_args()
    report = {"root": str(args.root.resolve()), "tsv": audit_tsv(args.root), "other": audit_non_tsv(args.root)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "tsv_files": report["tsv"]["files"], "headers": len(report["tsv"]["headers"]), "other": {key: value["files"] for key, value in report["other"].items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
