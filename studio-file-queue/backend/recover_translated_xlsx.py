#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover an XLSX import after its numeric-ID mapping was regenerated.

Reconstructs the exact old deduplication order from the pre-import records
backup and the isolated fresh-source database, then writes translations to the
current records by stable record ID.  This avoids relying on today's shortened
``*_records_mapping.json``.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from export_untranslated_xlsx import should_include
from import_untranslated_xlsx import read_simple_xlsx, restore_template, validate_translation, validate_tokens
from tsv_localization import token_template


def reconstruct(old_records, clean_records, pak):
    clean_by_loc = {
        (r.get("pak"), r.get("source_file"), int(r.get("line") or 0), int(r.get("column") or 0)): r
        for r in clean_records
    }
    grouped = {}
    missing = []
    for r in old_records:
        if r.get("pak") != pak or not should_include(r):
            continue
        loc = (pak, r.get("source_file"), int(r.get("line") or 0), int(r.get("column") or 0))
        fresh = clean_by_loc.get(loc)
        if not fresh:
            missing.append(loc)
            continue
        source = str(fresh.get("original") or "")
        meta = token_template(source)
        export_text = str(meta.get("text") or source)
        item = grouped.setdefault(export_text, {"id": str(len(grouped) + 1), "entries": []})
        item["entries"].append({
            "id": str(r.get("id") or ""), "source": source,
            "tokens": meta.get("tokens") or [], "template": meta.get("template") or "{TEXT}",
            "export_text": export_text,
        })
    return grouped, missing


def run(workspace: Path, clean_workspace: Path, pak: str, old_records_path: Path,
        translated_xlsx: Path, apply: bool = False):
    current_path = workspace / "localization" / "text_records.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    old = json.loads(old_records_path.read_text(encoding="utf-8"))
    clean = json.loads((clean_workspace / "localization" / "text_records.json").read_text(encoding="utf-8"))
    grouped, missing = reconstruct(old, clean, pak)
    translations = {str(r["id"]): str(r.get("text") or "") for r in read_simple_xlsx(translated_xlsx)}
    by_id = {str(r.get("id") or ""): r for r in current}
    updated = rejected = empty = unmapped = 0
    reasons = Counter()
    prepared = []
    for item in grouped.values():
        translated = translations.get(item["id"], "")
        if not translated.strip():
            empty += len(item["entries"]); continue
        for meta in item["entries"]:
            rec = by_id.get(meta["id"])
            if not rec:
                unmapped += 1; continue
            restored, error = restore_template(translated, meta)
            if error or restored is None:
                rejected += 1; reasons[error or "占位符还原失败"] += 1; continue
            ok, reason = validate_translation(meta["source"], restored)
            tok, _, _ = validate_tokens(meta["source"], restored)
            if not ok or not tok:
                rejected += 1; reasons[reason if not ok else "保护标识符不一致"] += 1; continue
            prepared.append((rec, meta["source"], restored))
    updated = len(prepared)
    if apply:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = current_path.with_name(f"text_records.json.before_recovered_xlsx_{pak[:-4]}_{stamp}")
        shutil.copy2(current_path, backup)
        for rec, source, restored in prepared:
            rec["source_original"] = source
            rec["original"] = restored
            rec["translation"] = restored
            rec["language"] = "zh"
            rec["status"] = "已迁移"
            rec["note"] = (str(rec.get("note") or "") + " [恢复导入:中文XLSX]").strip()
        current_path.write_text(json.dumps(current, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    else:
        backup = None
    return {
        "pak": pak, "apply": apply, "translated_xlsx": str(translated_xlsx.resolve()),
        "reconstructed_unique_ids": len(grouped), "xlsx_rows": len(translations),
        "fresh_locator_missing": len(missing), "prepared_updates": updated,
        "rejected": rejected, "rejected_reasons": dict(reasons), "empty": empty,
        "current_record_missing": unmapped, "backup": str(backup) if backup else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", type=Path); ap.add_argument("clean_workspace", type=Path)
    ap.add_argument("pak"); ap.add_argument("old_records", type=Path); ap.add_argument("xlsx", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(args.workspace, args.clean_workspace, args.pak, args.old_records, args.xlsx, args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
