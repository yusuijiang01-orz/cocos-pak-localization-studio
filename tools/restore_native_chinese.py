import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from backend.pc_content_migrator import clean_existing_chinese


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore clean source Chinese overwritten by PC migration."
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--pak", default="updatefs.pak")
    args = parser.parse_args()

    records_path = args.workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.workspace / "_safe_merge_backups" / stamp / "text_records.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(records_path, backup)

    methods: Counter[str] = Counter()
    samples = []
    restored = 0
    for record in records:
        if record.get("pak") != args.pak or "[PC迁移:" not in str(record.get("note", "")):
            continue
        if not clean_existing_chinese(record):
            continue
        source = str(record.get("source_original", ""))
        current = str(record.get("translation") or record.get("original") or "")
        if source == current:
            continue

        note = str(record.get("note", ""))
        method = note.split("[PC迁移:", 1)[1].split("]", 1)[0]
        methods[method] += 1
        if len(samples) < 50:
            samples.append({
                "file": record.get("source_file"),
                "line": record.get("line"),
                "column": record.get("column"),
                "wrong": current,
                "restored": source,
                "method": method,
            })
        record["original"] = source
        record["translation"] = source
        record["language"] = "zh"
        record["status"] = "已迁移"
        record["note"] = note + " [恢复原生中文:防止跨版本ID错位]"
        restored += 1

    records_path.write_text(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    report_path = args.workspace / "build" / "restore_native_chinese_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "pak": args.pak,
        "restored": restored,
        "methods": dict(methods.most_common()),
        "backup": str(backup),
        "samples": samples,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report | {"samples": samples[:5]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
