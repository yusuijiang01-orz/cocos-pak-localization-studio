#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from localization_tm import validate_tokens


CJK_RE = re.compile(r"[\u3400-\u9fff]")


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(str(text or "")))


def split_simple_wrappers(source: str) -> tuple[str, str] | None:
    tokens = validate_tokens.__globals__["protected_tokens"](source)
    if len(tokens) != 2:
        return None
    prefix, suffix = tokens
    if not source.startswith(prefix) or not source.endswith(suffix):
        return None
    if len(source) <= len(prefix) + len(suffix):
        return None
    return prefix, suffix


def fix_records(workspace: Path, pak: str, dry_run: bool) -> dict:
    records_path = workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    updated = 0
    by_file: dict[str, int] = {}
    samples = []
    for r in records:
        if r.get("pak") != pak:
            continue
        source = str(r.get("source_original") or "")
        target = str(r.get("original") or "")
        if not source or not target or not has_cjk(target):
            continue
        ok, source_tokens, target_tokens = validate_tokens(source, target)
        if ok or target_tokens:
            continue
        wrappers = split_simple_wrappers(source)
        if not wrappers:
            continue
        prefix, suffix = wrappers
        fixed = prefix + target + suffix
        ok, _source_tokens, _target_tokens = validate_tokens(source, fixed)
        if not ok:
            continue
        r["original"] = fixed
        r["translation"] = fixed
        r["language"] = "zh"
        r["status"] = "已修复"
        r["note"] = (str(r.get("note") or "").strip() + " [自动补全外层保护标记]").strip()
        updated += 1
        name = str(r.get("source_file") or "")
        by_file[name] = by_file.get(name, 0) + 1
        if len(samples) < 20:
            samples.append(
                {
                    "file": name,
                    "line": r.get("line"),
                    "column": r.get("column"),
                    "before": target,
                    "after": fixed,
                    "tokens": source_tokens,
                }
            )
    report = {
        "dry_run": dry_run,
        "pak": pak,
        "updated": updated,
        "top_files": dict(sorted(by_file.items(), key=lambda kv: -kv[1])[:30]),
        "samples": samples,
    }
    out_dir = workspace / "build" / f"fix_visible_protected_wrappers_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if updated and not dry_run:
        backup = records_path.with_name(
            f"text_records.before_fix_visible_protected_wrappers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        shutil.copy2(records_path, backup)
        tmp = records_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, records_path)
        report["backup"] = str(backup.resolve())
    report_path = out_dir / "fix_visible_protected_wrappers_report.json"
    report["report_path"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair translated visible records that lost simple outer protected tags.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--pak", default="updatefs.pak")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(fix_records(args.workspace, args.pak, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
