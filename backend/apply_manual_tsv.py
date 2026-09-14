#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_manual_tsv.py — 将手动翻译的 TSV 应用到 text_records.json
================================================================
读取用户手动汉化的 TSV 文件，按 (hash, line, column) 匹配
text_records.json 中的记录，直接覆盖为中文。
同时提取越中对照对供 TM 复用。
"""
import json, csv, os, sys

# 手动翻译的 TSV 文件路径
TSV_FILES = {
    "06C163CB": r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++\tsv\0068_06C163CB.tsv",
    "5325A29A": r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++\tsv\0814_5325A29A.tsv",
}

RECORDS_PATH = r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++\localization\text_records.json"


def read_tsv(path):
    """Read TSV as list of rows, each row is list of string cells."""
    # Try different encodings
    for enc in ["utf-8-sig", "utf-8", "gbk", "gb18030"]:
        try:
            with open(path, "r", encoding=enc) as f:
                reader = csv.reader(f, delimiter="\t")
                return list(reader)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Cannot decode {path}")


def build_tsv_lookup(hash_id, tsv_path):
    """Build (hash, line, column) -> cell_value lookup from TSV file."""
    rows = read_tsv(tsv_path)
    lookup = {}
    for line_idx, row in enumerate(rows, 1):  # 1-based line numbers
        for col_idx, cell in enumerate(row, 1):  # 1-based column numbers
            cell = cell.strip()
            if cell:
                lookup[(hash_id, line_idx, col_idx)] = cell
    return lookup


def main():
    # Build TSV lookups
    tsv_lookups = {}
    for hash_id, path in TSV_FILES.items():
        if not os.path.exists(path):
            print(f"WARNING: {path} not found, skipping")
            continue
        lookup = build_tsv_lookup(hash_id, path)
        tsv_lookups.update(lookup)
        print(f"Loaded {hash_id}: {len(lookup)} cells")

    # Load text_records.json
    with open(RECORDS_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    # Apply
    updated = 0
    tm_pairs = {}  # vietnamese -> chinese (unique)
    skipped_zh = 0
    skipped_migrated = 0
    not_found = 0

    for r in records:
        hash_id = r.get("hash", "")
        if hash_id not in [h for h in TSV_FILES.keys()]:
            continue
        if r.get("status") == "已迁移" or r.get("language") == "zh":
            if r.get("language") == "zh":
                skipped_zh += 1
            else:
                skipped_migrated += 1
            continue

        key = (hash_id, r.get("line", 0), r.get("column", 0))
        chinese_text = tsv_lookups.get(key, "")
        if not chinese_text:
            not_found += 1
            continue

        # Check if it has CJK (sanity)
        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in chinese_text)
        if not has_cjk:
            not_found += 1
            continue

        old_source = r.get("source_original", r.get("original", ""))
        r["source_original"] = old_source
        r["original"] = chinese_text
        r["translation"] = chinese_text
        r["status"] = "已迁移"
        r["note"] = (str(r.get("note", "")).strip() + " [手动TSV]").strip()
        updated += 1

        # Extract TM pair
        if old_source and old_source not in tm_pairs:
            tm_pairs[old_source] = chinese_text

    # Save updated records
    backup_path = RECORDS_PATH + ".before_manual_tsv"
    if not os.path.exists(backup_path):
        import shutil
        shutil.copy2(RECORDS_PATH, backup_path)
        print(f"Backup: {backup_path}")

    with open(RECORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nApplied {updated} manual TSV translations")
    print(f"Skipped (already zh): {skipped_zh}")
    print(f"Skipped (already migrated): {skipped_migrated}")
    print(f"Not found in TSV: {not_found}")
    print(f"TM pairs extracted: {len(tm_pairs)}")

    # Save TM pairs for reuse
    tm_path = os.path.join(os.path.dirname(RECORDS_PATH), "manual_tsv_tm.json")
    with open(tm_path, "w", encoding="utf-8") as f:
        json.dump(tm_pairs, f, ensure_ascii=False, indent=1)
    print(f"TM pairs saved: {tm_path}")

    # Verify coverage
    total = len(records)
    zh = sum(1 for r in records if r.get("language") == "zh")
    migrated = sum(1 for r in records if r.get("status") == "已迁移")
    coverage = (zh + migrated) * 100 / total
    print(f"\nCoverage: {zh + migrated}/{total} = {coverage:.2f}%")

    # Show some TM samples
    print("\nTM samples:")
    for i, (vi, zh_text) in enumerate(tm_pairs.items()):
        if i >= 10:
            break
        print(f"  {vi[:50]} => {zh_text[:50]}")


if __name__ == "__main__":
    main()
