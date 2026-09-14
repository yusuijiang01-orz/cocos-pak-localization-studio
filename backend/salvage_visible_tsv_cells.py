from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from localization_analyzer import (
    MOJIBAKE_HINTS,
    contains_cjk,
    decode_best,
    is_resource_reference,
    is_visible_tsv_column,
)
from tsv_localization import validate_translation


from localization_analyzer import FULLY_PROTECTED_TSV_FILES as FULLY_PROTECTED_TSV


def salvage_file(official: Path, localized: Path, output: Path) -> dict:
    official_bytes = official.read_bytes()
    localized_bytes = localized.read_bytes()
    official_lines = official_bytes.splitlines(keepends=True)
    localized_lines = localized_bytes.splitlines(keepends=True)
    if len(official_lines) != len(localized_lines):
        return {"file": official.name, "status": "quarantined", "reason": "line-count mismatch", "updated": 0}

    header = official_lines[0].rstrip(b"\r\n").split(b"\t")
    visible = {
        index
        for index, cell in enumerate(header)
        if is_visible_tsv_column(decode_best(cell)[0].strip(), official.name)
    }
    if not visible:
        return {"file": official.name, "status": "official", "reason": "no visible columns", "updated": 0}

    rebuilt = [official_lines[0]]
    updated = 0
    rejected = 0
    for official_line, localized_line in zip(official_lines[1:], localized_lines[1:]):
        ending = b"\r\n" if official_line.endswith(b"\r\n") else b"\n" if official_line.endswith(b"\n") else b"\r" if official_line.endswith(b"\r") else b""
        official_cells = official_line.rstrip(b"\r\n").split(b"\t")
        localized_cells = localized_line.rstrip(b"\r\n").split(b"\t")
        if len(official_cells) != len(localized_cells):
            rebuilt.append(official_line)
            rejected += 1
            continue
        for index in visible:
            if index >= len(official_cells) or official_cells[index] == localized_cells[index]:
                continue
            source = decode_best(official_cells[index])[0].strip()
            target = decode_best(localized_cells[index])[0].strip()
            if (
                not target
                or not contains_cjk(target)
                or "\ufffd" in target
                or any(marker in target for marker in MOJIBAKE_HINTS)
                or is_resource_reference(target)
            ):
                rejected += 1
                continue
            ok, _reason = validate_translation(source, target)
            if not ok:
                rejected += 1
                continue
            official_cells[index] = target.encode("utf-8")
            updated += 1
        rebuilt.append(b"\t".join(official_cells) + ending)

    if not updated:
        return {"file": official.name, "status": "official", "reason": "no safe translated cells", "updated": 0, "rejected": rejected}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"".join(rebuilt))
    return {"file": official.name, "status": "salvaged", "updated": updated, "rejected": rejected, "visible_columns": sorted(index + 1 for index in visible)}


def main() -> None:
    pak_root = ROOT / "pak"
    official_root = pak_root / "v587+" / "_raw_reference" / "updatefs"
    localized_root = pak_root / "new" / "_raw_reference" / "updatefs"
    output_root = pak_root / "new" / "recovery" / "visible_tsv_salvage"
    if output_root.exists():
        for path in output_root.glob("*.tsv"):
            path.unlink()

    results = []
    for official in sorted(official_root.glob("*.tsv")):
        if official.name.lower() in FULLY_PROTECTED_TSV:
            results.append({"file": official.name, "status": "quarantined", "reason": "model/resource key table", "updated": 0})
            continue
        localized = localized_root / official.name
        if not localized.is_file():
            results.append({"file": official.name, "status": "official", "reason": "localized file missing", "updated": 0})
            continue
        results.append(salvage_file(official, localized, output_root / official.name))

    report = {
        "official_root": str(official_root),
        "localized_root": str(localized_root),
        "output_root": str(output_root),
        "salvaged_files": sum(item["status"] == "salvaged" for item in results),
        "salvaged_cells": sum(item.get("updated", 0) for item in results),
        "rejected_cells": sum(item.get("rejected", 0) for item in results),
        "fully_protected": sorted(FULLY_PROTECTED_TSV),
        "files": results,
    }
    report_path = output_root.parent / "visible_tsv_salvage_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, ensure_ascii=False, indent=2))
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
