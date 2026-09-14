import argparse
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


UNSAFE_METHODS = {
    "hash_locator_unique",
    "cross_file_composite_key",
    "cross_file_composite_key_settings",
    "cross_file_primary_key",
    "cross_file_row_signature",
    "tsv_primary_key",
    "ini_section_key",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Revert low-confidence cross-version migration matches.")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--pak", default="updatefs.pak")
    args = parser.parse_args()

    records_path = args.workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.workspace / "_safe_merge_backups" / stamp / "text_records.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(records_path, backup)

    reverted = Counter()
    samples = []
    for record in records:
        if record.get("pak") != args.pak:
            continue
        note = str(record.get("note", ""))
        marker = "[PC迁移:"
        if marker not in note:
            continue
        method = note.split(marker, 1)[1].split("]", 1)[0]
        if method not in UNSAFE_METHODS:
            continue
        source = str(record.get("source_original", ""))
        current = str(record.get("original", ""))
        if not source or source == current:
            continue
        if len(samples) < 50:
            samples.append({
                "file": record.get("source_file"), "line": record.get("line"),
                "column": record.get("column"), "method": method,
                "removed": current, "restored": source,
            })
        record["original"] = source
        record["translation"] = ""
        record["language"] = "vi"
        record["status"] = "未翻译"
        record["note"] = note + " [已撤销:低可信跨版本定位]"
        reverted[method] += 1

    records_path.write_text(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    report = {
        "pak": args.pak, "reverted": sum(reverted.values()),
        "methods": dict(reverted.most_common()), "backup": str(backup), "samples": samples,
    }
    report_path = args.workspace / "build" / "revert_unsafe_pc_migrations_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "samples": samples[:3]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
