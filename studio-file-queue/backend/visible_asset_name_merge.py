#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path


CJK_RE = re.compile(r"[\u3400-\u9fff]")
VI_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]")
VISIBLE_NAME_HEADERS = {"名称", "名字", "Name", "ItemName", "WeaponName", "EquipName"}
VISIBLE_DESC_HEADERS = {"说明文字", "说明", "描述", "简介", "Intro", "Desc", "Description"}


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(str(text or "")))


def has_vi(text: str) -> bool:
    return bool(VI_RE.search(str(text or "")))


def clean_name(name: str) -> str:
    name = Path(str(name).replace("\\", "/")).stem
    name = re.sub(r"[_\-\s]*(?:无背景|icon|Icon|ICON|图标|小图|大图)$", "", name)
    return name.strip()


def visible_asset_name(path_text: str) -> str:
    name = clean_name(path_text)
    if len(name) >= 2 and has_cjk(name):
        return "#" + name if not name.startswith("#") else name
    return ""


def decode_cell(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "cp1258", "latin1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore").strip()


def asset_paths_by_file(workspace: Path) -> dict[tuple[str, int], str]:
    result = {}
    root = workspace / "extracted" / "updatefs"
    for path in root.glob("*.tsv"):
        for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
            if line_no == 1:
                continue
            cells = raw.split(b"\t")
            if len(cells) >= 5:
                result[(path.name, line_no)] = decode_cell(cells[4])
    return result


def index_headers(records: list[dict], pak: str) -> dict[tuple[str, int], str]:
    return {
        (str(r.get("source_file") or ""), int(r.get("column") or 1)): str(r.get("original") or "")
        for r in records
        if r.get("pak") == pak and int(r.get("line") or 0) == 1
    }


def locators(records: list[dict], pak: str) -> dict[tuple[str, int, int], dict]:
    return {
        (str(r.get("source_file") or ""), int(r.get("line") or 0), int(r.get("column") or 1)): r
        for r in records
        if r.get("pak") == pak
    }


def pc_asset_paths(pc_root: Path) -> dict[tuple[str, str, int], str]:
    result = {}
    for path in (pc_root / "extracted").rglob("*.tsv"):
        pak = f"{path.parent.name}.pak"
        for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
            if line_no == 1:
                continue
            cells = raw.split(b"\t")
            if len(cells) >= 5:
                result[(pak, path.name, line_no)] = decode_cell(cells[4])
    return result


def build_pc_desc_memory(pc_root: Path, pc_records: list[dict]) -> tuple[dict[str, str], dict]:
    headers_by_pak = {}
    for pak in {str(r.get("pak") or "") for r in pc_records}:
        headers_by_pak[pak] = index_headers(pc_records, pak)
    assets = pc_asset_paths(pc_root)
    candidates = {}
    counts = {}
    for r in pc_records:
        pak = str(r.get("pak") or "")
        if int(r.get("line") or 0) == 1:
            continue
        header = headers_by_pak.get(pak, {}).get((str(r.get("source_file") or ""), int(r.get("column") or 1)), "")
        if header not in VISIBLE_DESC_HEADERS:
            continue
        text = str(r.get("original") or "")
        if not has_cjk(text):
            continue
        key = clean_name(assets.get((pak, str(r.get("source_file") or ""), int(r.get("line") or 0)), "")).lower()
        if not key or not has_cjk(key):
            continue
        counts.setdefault(key, {})
        counts[key][text] = counts[key].get(text, 0) + 1
    selected = {}
    ambiguous = 0
    majority = 0
    for key, values in counts.items():
        ranked = sorted(values.items(), key=lambda item: (-item[1], item[0]))
        if len(ranked) == 1:
            selected[key] = ranked[0][0]
            continue
        total = sum(v for _text, v in ranked)
        if ranked[0][1] >= 3 and ranked[0][1] / total >= 0.80:
            selected[key] = ranked[0][0]
            majority += 1
        else:
            ambiguous += 1
    return selected, {"selected": len(selected), "majority": majority, "ambiguous": ambiguous, "raw_keys": len(counts)}


def merge_asset_names(pc_root: Path, workspace: Path, dry_run: bool) -> dict:
    records_path = workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    pc_records = json.loads((pc_root / "localization" / "text_records.json").read_text(encoding="utf-8"))
    headers = index_headers(records, "updatefs.pak")
    by = locators(records, "updatefs.pak")
    pc_desc, pc_desc_report = build_pc_desc_memory(pc_root, pc_records)
    assets = asset_paths_by_file(workspace)
    updated_name = 0
    updated_desc = 0
    files = {}
    for r in list(by.values()):
        file = str(r.get("source_file") or "")
        line = int(r.get("line") or 0)
        col = int(r.get("column") or 1)
        if line <= 1:
            continue
        header = headers.get((file, col), "")
        current = str(r.get("original") or "")
        if has_cjk(current) or not current.strip():
            continue
        image_text = assets.get((file, line), "")
        asset_name = visible_asset_name(image_text)
        target = ""
        kind = ""
        if header in VISIBLE_NAME_HEADERS and asset_name:
            target = asset_name
            kind = "name"
        elif header in VISIBLE_DESC_HEADERS and asset_name:
            desc = pc_desc.get(clean_name(image_text).lower())
            if desc:
                target = desc
                kind = "desc"
        if not target:
            continue
        r["source_original"] = str(r.get("source_original") or current)
        r["original"] = target
        r["translation"] = target
        r["language"] = "zh"
        r["status"] = "已迁移"
        r["note"] = (str(r.get("note") or "").strip() + f" [资源名可见迁移:{kind}]").strip()
        if kind == "name":
            updated_name += 1
        else:
            updated_desc += 1
        files[file] = files.get(file, 0) + 1
    report = {
        "dry_run": dry_run,
        "updated": updated_name + updated_desc,
        "updated_name": updated_name,
        "updated_desc": updated_desc,
        "pc_desc_memory": pc_desc_report,
        "top_files": dict(sorted(files.items(), key=lambda kv: -kv[1])[:30]),
    }
    out_dir = workspace / "build" / f"visible_asset_name_merge_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run and report["updated"]:
        backup = records_path.with_name(f"text_records.before_visible_asset_name_merge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        shutil.copy2(records_path, backup)
        tmp = records_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, records_path)
        report["backup"] = str(backup.resolve())
    report["report_path"] = str((out_dir / "visible_asset_name_merge_report.json").resolve())
    (out_dir / "visible_asset_name_merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill visible item names from Chinese asset filenames and safe PC descriptions.")
    parser.add_argument("--pc-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(merge_asset_names(args.pc_root, args.workspace, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
