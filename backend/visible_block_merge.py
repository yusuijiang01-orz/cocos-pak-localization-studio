#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from localization_tm import exchange_text, validate_tokens
from tsv_localization import validate_translation


CJK_RE = re.compile(r"[\u3400-\u9fff]")
VI_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]"
)


DEFAULT_PAIRS = [
    ("0352_25818B6E.tsv", "serverlist.pak", "2513_C70FA219.tsv", 1, 1),
    ("1593_9E653F86.tsv", "serverlist.pak", "1147_5D54D951.tsv", 1, 1),
    ("1106_70ED2368.tsv", "serverlist.pak", "0299_19CC8A2C.tsv", 1, 1),
    ("2214_D709ED85.tsv", "serverlist.pak", "1326_6E0062B4.tsv", 1, 1),
    ("0432_2E2D8A7F.tsv", "serverlist.pak", "2867_E261F7B2.tsv", 1, 1),
    ("0688_473A085C.tsv", "serverlist.pak", "1077_58C1E3AF.tsv", 1, 1),
]


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(str(text or "")))


def has_vi(text: str) -> bool:
    return bool(VI_RE.search(str(text or "")))


def records_by_locator(records: list[dict], pak: str, file: str) -> dict[tuple[int, int], dict]:
    return {
        (int(r.get("line") or 0), int(r.get("column") or 1)): r
        for r in records
        if r.get("pak") == pak and r.get("source_file") == file
    }


def row_count(records: list[dict], pak: str, file: str) -> int:
    return max(
        [int(r.get("line") or 0) for r in records if r.get("pak") == pak and r.get("source_file") == file] or [0]
    )


def safe_update(mobile: dict, pc: dict) -> bool:
    source = str(mobile.get("source_original") or mobile.get("original") or "")
    target = str(pc.get("original") or "")
    if not target or not has_cjk(target):
        return False
    if has_cjk(str(mobile.get("original") or "")):
        return False
    if not has_vi(source) and str(mobile.get("original") or "") != source:
        return False
    if not validate_translation(exchange_text(source), exchange_text(target))[0]:
        return False
    if not validate_tokens(source, target)[0]:
        return False
    mobile["source_original"] = source
    mobile["original"] = target
    mobile["translation"] = target
    mobile["language"] = "zh"
    mobile["status"] = "已迁移"
    mobile["note"] = (str(mobile.get("note") or "").strip() + " [玩家可见块迁移]").strip()
    return True


def merge_blocks(pc_root: Path, workspace: Path, dry_run: bool) -> dict:
    records_path = workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    pc_records = json.loads((pc_root / "localization" / "text_records.json").read_text(encoding="utf-8"))
    changed = 0
    reports = []
    for mobile_file, pc_pak, pc_file, mobile_start, pc_start in DEFAULT_PAIRS:
        mobile_by = records_by_locator(records, "updatefs.pak", mobile_file)
        pc_by = records_by_locator(pc_records, pc_pak, pc_file)
        rows = min(row_count(records, "updatefs.pak", mobile_file) - mobile_start + 1, row_count(pc_records, pc_pak, pc_file) - pc_start + 1)
        updated = 0
        rejected = 0
        for offset in range(max(0, rows)):
            mobile_line = mobile_start + offset
            pc_line = pc_start + offset
            for col in (1, 9):
                mobile = mobile_by.get((mobile_line, col))
                pc = pc_by.get((pc_line, col))
                if not mobile or not pc:
                    continue
                if safe_update(mobile, pc):
                    updated += 1
                else:
                    rejected += 1
        changed += updated
        reports.append({
            "mobile_file": mobile_file,
            "pc": f"{pc_pak}:{pc_file}",
            "rows_considered": max(0, rows),
            "updated": updated,
            "rejected_or_skipped": rejected,
        })
    report = {"dry_run": dry_run, "updated": changed, "pairs": reports}
    out_dir = workspace / "build" / f"visible_block_merge_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run and changed:
        backup = records_path.with_name(f"text_records.before_visible_block_merge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        shutil.copy2(records_path, backup)
        tmp = records_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, records_path)
        report["backup"] = str(backup.resolve())
    report["report_path"] = str((out_dir / "visible_block_merge_report.json").resolve())
    (out_dir / "visible_block_merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply hand-audited visible row-block PC mappings.")
    parser.add_argument("--pc-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(merge_blocks(args.pc_root, args.workspace, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
