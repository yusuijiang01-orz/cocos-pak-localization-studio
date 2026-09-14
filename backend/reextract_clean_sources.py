#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild untranslated XLSX files from an isolated, freshly unpacked source.

The current Studio database may contain translations in both ``original`` and
``source_original``.  This tool deliberately ignores both as translation input:
it matches the current records to freshly analyzed PAK records by the stable
physical locator (PAK, file, line, column), while retaining current record IDs
so the normal XLSX importer can write translations back safely.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from export_untranslated_xlsx import export_pak, should_include


def run(workspace: Path, clean_workspace: Path, paks: list[str]) -> dict:
    current_path = workspace / "localization" / "text_records.json"
    clean_path = clean_workspace / "localization" / "text_records.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    clean = json.loads(clean_path.read_text(encoding="utf-8"))

    clean_by_locator = {
        (str(r.get("pak")), str(r.get("source_file")), int(r.get("line") or 0), int(r.get("column") or 0)): r
        for r in clean
    }
    reports = []
    for pak in paks:
        candidates = [r for r in current if r.get("pak") == pak and should_include(r)]
        rebuilt = []
        matched = 0
        missing = []
        for r in candidates:
            locator = (pak, str(r.get("source_file")), int(r.get("line") or 0), int(r.get("column") or 0))
            fresh = clean_by_locator.get(locator)
            if fresh is None:
                missing.append({"id": r.get("id"), "file": locator[1], "line": locator[2], "column": locator[3]})
                continue
            source = str(fresh.get("original") or "")
            copy = dict(r)
            copy["original"] = source
            copy["source_original"] = source
            copy["language"] = fresh.get("language") or "vi"
            copy["encoding"] = fresh.get("encoding") or ""
            copy["status"] = "未翻译"
            rebuilt.append(copy)
            matched += 1

        export = export_pak(rebuilt, workspace, pak)
        export.update({
            "fresh_source_workspace": str(clean_workspace.resolve()),
            "current_residual_records": len(candidates),
            "fresh_locator_matches": matched,
            "fresh_locator_missing": len(missing),
            "missing_sample": missing[:50],
        })
        report_path = Path(export["output_dir"]) / "clean_reextract_report.json"
        report_path.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(export)
    return {"workspace": str(workspace.resolve()), "reports": reports}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", type=Path)
    ap.add_argument("clean_workspace", type=Path)
    ap.add_argument("paks", nargs="+")
    args = ap.parse_args()
    print(json.dumps(run(args.workspace, args.clean_workspace, args.paks), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
