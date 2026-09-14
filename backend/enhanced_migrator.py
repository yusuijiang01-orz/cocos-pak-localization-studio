#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enhanced_migrator.py — 增强版 PC→移动端中文迁移
===============================================
三阶段策略：
  Phase 1: 放宽位置匹配 (hash, line, column) — 跳过严格验证，只要PC文本有CJK
  Phase 2: SOURCE_OVERRIDE 术语表精确覆盖
  Phase 3: 导出剩余唯一越南语字符串供LLM批量翻译

用法:
  python enhanced_migrator.py <pc_root> <mobile_root> <output_dir>
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import sys
from pathlib import Path


def cjk_count(text: str) -> int:
    return sum("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def has_cjk(text) -> bool:
    return cjk_count(text) > 0


def has_vietnamese(text) -> bool:
    vi = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
    return any(c in vi for c in str(text or "").lower())


def load_records(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_pc_lookup(pc_records: list[dict]) -> dict:
    """Build (hash, line, column) -> original lookup from all PC records."""
    lookup = {}
    for r in pc_records:
        key = (r.get("hash", ""), r.get("line", 0), r.get("column", 0))
        orig = str(r.get("original", ""))
        if key not in lookup and has_cjk(orig) and "\ufffd" not in orig:
            lookup[key] = orig
    return lookup


def build_source_override():
    """Import SOURCE_OVERRIDE from polish_localizer."""
    # Inline the most important entries to avoid import issues
    # (polish_localizer.py has file-path dependencies)
    return {
        "Thập đại cao thủ": "十大高手", "cấp": "等级", "Thập đại phú hào": "十大富豪",
        "Thập đại sát thủ": "十大杀手", "Tài phú binh giáp": "太尉兵甲", "tài phú": "太尉",
        "Hoang dã cao thủ": "荒野高手", "Hoang dã phú hào": "荒野富豪", "Thập đại danh nhân": "十大名人",
        "Phúc duyên": "福缘", "phúc duyên": "福缘", "Thách thức thời gian": "时间挑战",
        "Môn phái cao thủ": "门派高手", "Thiếu Lâm": "少林", "Thiên Vương": "天王", "Đường Môn": "唐门",
        "Thúy Yên": "翠烟", "Thiên Nhẫn": "天忍", "Võ Đang": "武当", "Côn Lôn": "昆仑",
        "Môn phái phú hào": "门派富豪", "Thập đại bang hội": "十大帮会", "Đẳng cấp": "等级",
        "Thành viên": "成员", "người": "人", "Cống hiến": "贡献", "cống hiến": "贡献",
    }


def migrate(pc_root: Path, mobile_root: Path, output_dir: Path) -> dict:
    pc_records = load_records(pc_root / "localization" / "text_records.json")
    mobile_records = load_records(mobile_root / "localization" / "text_records.json")

    pc_lookup = build_pc_lookup(pc_records)
    source_override = build_source_override()

    # Stats
    phase1_hits = 0
    phase2_hits = 0
    phase1_by_pak = collections.Counter()
    phase2_by_pak = collections.Counter()
    remaining_by_pak = collections.Counter()
    remaining_unique = {}  # source_original -> list of record ids
    rows = []

    for idx, r in enumerate(mobile_records):
        if r.get("status") == "已迁移" or r.get("language") == "zh":
            continue  # Already has Chinese

        source = str(r.get("source_original", r.get("original", "")))
        matched = False
        method = ""

        # Phase 1: (hash, line, column) positional match
        key = (r.get("hash", ""), r.get("line", 0), r.get("column", 0))
        pc_text = pc_lookup.get(key, "")
        if has_cjk(pc_text) and "\ufffd" not in pc_text:
            # Prefix handling: # $ = are file format markers, not content.
            # If mobile has prefix but PC doesn't, prepend mobile's prefix to PC text.
            # If both have prefix and they match, use PC text as-is.
            # If both have prefix and they DON'T match, skip (suspicious).
            src_prefix_m = re.match(r"^[#$=]+", source)
            tgt_prefix_m = re.match(r"^[#$=]+", pc_text)
            src_p = src_prefix_m.group(0) if src_prefix_m else ""
            tgt_p = tgt_prefix_m.group(0) if tgt_prefix_m else ""

            accept = False
            final_text = pc_text
            if src_p == tgt_p:
                accept = True
            elif src_p and not tgt_p:
                # Mobile has prefix, PC doesn't — prepend mobile prefix
                accept = True
                final_text = src_p + pc_text
            elif not src_p and not tgt_p:
                accept = True
            # else: both have different prefixes → skip

            if accept:
                r["source_original"] = source
                r["original"] = final_text
                r["translation"] = final_text
                r["status"] = "已迁移"
                r["note"] = (str(r.get("note", "")).strip() + " [增强迁移:positional]").strip()
                phase1_hits += 1
                phase1_by_pak[r.get("pak", "")] += 1
                rows.append({
                    "id": r["id"], "pak": r.get("pak", ""), "hash": r.get("hash", ""),
                    "source_file": r.get("source_file", ""), "line": r.get("line", ""),
                    "column": r.get("column", ""), "method": "positional",
                    "mobile_text": source, "pc_text": final_text,
                })
                matched = True
                method = "positional"

        # Phase 2: SOURCE_OVERRIDE exact match
        if not matched and source.strip() in source_override:
            zh = source_override[source.strip()]
            r["source_original"] = source
            r["original"] = zh
            r["translation"] = zh
            r["status"] = "已迁移"
            r["note"] = (str(r.get("note", "")).strip() + " [增强迁移:override]").strip()
            phase2_hits += 1
            phase2_by_pak[r.get("pak", "")] += 1
            rows.append({
                "id": r["id"], "pak": r.get("pak", ""), "hash": r.get("hash", ""),
                "source_file": r.get("source_file", ""), "line": r.get("line", ""),
                "column": r.get("column", ""), "method": "override",
                "mobile_text": source, "pc_text": zh,
            })
            matched = True
            method = "override"

        if not matched:
            remaining_by_pak[r.get("pak", "")] += 1
            so = source.strip()
            if so and so not in remaining_unique:
                remaining_unique[so] = []
            if so:
                remaining_unique[so].append(r["id"])

    # Write output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Full updated text_records.json
    records_path = output_dir / "text_records.enhanced.json"
    records_path.write_text(
        json.dumps(mobile_records, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )

    # Migration rows CSV
    if rows:
        with (output_dir / "enhanced_migration_rows.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    # Remaining unique strings for LLM translation
    llm_path = output_dir / "remaining_for_llm.json"
    # Deduplicate and sort by length (shorter first for batching efficiency)
    remaining_sorted = sorted(remaining_unique.keys(), key=lambda s: (len(s), s))
    llm_data = {
        "total_unique": len(remaining_sorted),
        "total_records": sum(len(v) for v in remaining_unique.values()),
        "by_pak": dict(remaining_by_pak),
        "strings": [{"id": i + 1, "source": s, "record_ids": remaining_unique[s]} for i, s in enumerate(remaining_sorted)],
    }
    llm_path.write_text(json.dumps(llm_data, ensure_ascii=False, indent=1), encoding="utf-8")

    # Report
    total_before = len(mobile_records)
    already_zh = sum(1 for r in mobile_records if r.get("language") == "zh")
    already_migrated = sum(1 for r in mobile_records if r.get("status") == "已迁移" and "[增强" not in str(r.get("note", "")))
    new_migrated = phase1_hits + phase2_hits
    total_chinese = already_zh + already_migrated + new_migrated

    report = {
        "pc_root": str(pc_root.resolve()),
        "mobile_root": str(mobile_root.resolve()),
        "total_mobile_records": total_before,
        "already_chinese": already_zh,
        "previously_migrated": already_migrated,
        "phase1_positional": phase1_hits,
        "phase2_override": phase2_hits,
        "new_migrated_total": new_migrated,
        "total_chinese_coverage": total_chinese,
        "coverage_percent": round(total_chinese * 100 / total_before, 2),
        "remaining_for_llm": len(remaining_sorted),
        "remaining_records": sum(len(v) for v in remaining_unique.values()),
        "phase1_by_pak": dict(phase1_by_pak),
        "phase2_by_pak": dict(phase2_by_pak),
        "remaining_by_pak": dict(remaining_by_pak),
        "outputs": {
            "records": str(records_path),
            "migration_rows": str((output_dir / "enhanced_migration_rows.csv").resolve()),
            "llm_input": str(llm_path.resolve()),
        },
    }
    (output_dir / "enhanced_migration_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="Enhanced PC→mobile Chinese migration")
    parser.add_argument("pc_root", type=Path)
    parser.add_argument("mobile_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    report = migrate(args.pc_root, args.mobile_root, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
