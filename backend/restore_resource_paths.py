from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from localization_tm import RESOURCE_EXTENSIONS


EXT_RE = re.compile(rb"\." + rb"(?:" + RESOURCE_EXTENSIONS.encode("ascii") + rb")\b", re.I)
PATH_DELIMITERS = set(b" \t\r\n<>\"'|,;()[]{}=")
ENTRY_RE = re.compile(r"^\d+_([0-9a-f]{8})(\.[^.]+)$", re.I)


def entry_key(path: Path) -> tuple[str, str] | None:
    match = ENTRY_RE.match(path.name)
    return (match.group(1).upper(), match.group(2).lower()) if match else None


def path_spans(value: bytes) -> list[tuple[int, int, bytes]]:
    spans = []
    cursor = 0
    while True:
        match = EXT_RE.search(value, cursor)
        if match is None:
            break
        start = match.start()
        while start > 0 and value[start - 1] not in PATH_DELIMITERS:
            start -= 1
        end = match.end()
        candidate = value[start:end]
        if b"\\" in candidate or b"/" in candidate:
            spans.append((start, end, candidate))
        cursor = max(end, match.start() + 1)
    return spans


def replace_paths(reference: bytes, target: bytes) -> tuple[bytes, int, str | None]:
    reference_lines = reference.splitlines(keepends=True)
    target_lines = target.splitlines(keepends=True)
    if len(reference_lines) != len(target_lines):
        return target, 0, "line-count mismatch"

    changed = 0
    output: list[bytes] = []
    for reference_line, target_line in zip(reference_lines, target_lines):
        reference_matches = path_spans(reference_line)
        target_matches = path_spans(target_line)
        reference_paths = [item[2] for item in reference_matches]
        if not reference_paths and not target_matches:
            output.append(target_line)
            continue
        if len(reference_paths) != len(target_matches):
            return target, 0, "path-count mismatch"
        rebuilt = bytearray()
        cursor = 0
        for source_path, target_match in zip(reference_paths, target_matches):
            rebuilt.extend(target_line[cursor:target_match[0]])
            rebuilt.extend(source_path)
            if target_match[2] != source_path:
                changed += 1
            cursor = target_match[1]
        rebuilt.extend(target_line[cursor:])
        output.append(bytes(rebuilt))
    return b"".join(output), changed, None


def repair_tree(reference_root: Path, target_root: Path, backup_root: Path) -> dict:
    reference_files = {
        key: path
        for path in reference_root.iterdir()
        if path.is_file() and (key := entry_key(path)) is not None
    }
    repaired_files = 0
    repaired_paths = 0
    skipped: list[dict] = []
    for target in sorted(target_root.iterdir()) if target_root.is_dir() else []:
        if not target.is_file():
            continue
        key = entry_key(target)
        reference = reference_files.get(key) if key else None
        if reference is None:
            continue
        before = target.read_bytes()
        after, count, reason = replace_paths(reference.read_bytes(), before)
        if reason:
            if path_spans(reference.read_bytes()) or path_spans(before):
                skipped.append({"file": target.name, "reason": reason})
            continue
        if not count or after == before:
            continue
        relative = target.relative_to(target_root)
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        target.write_bytes(after)
        repaired_files += 1
        repaired_paths += count
    return {
        "target": str(target_root.resolve()),
        "repaired_files": repaired_files,
        "repaired_paths": repaired_paths,
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore resource path bytes without replacing localized text")
    parser.add_argument("reference_workspace", type=Path)
    parser.add_argument("target_workspace", type=Path)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = args.target_workspace / "recovery" / f"path_restore_backup_{stamp}"
    reports = []
    for pak in ("settings", "ui", "updatefs"):
        reference_root = args.reference_workspace / "_raw_reference" / pak
        if not reference_root.is_dir():
            continue
        for tree_name in ("_raw_reference", "extracted", "modified"):
            target_root = args.target_workspace / tree_name / pak
            if target_root.is_dir():
                reports.append(
                    {"pak": pak, "tree": tree_name, **repair_tree(reference_root, target_root, backup_root / tree_name / pak)}
                )
    report = {
        "reference_workspace": str(args.reference_workspace.resolve()),
        "target_workspace": str(args.target_workspace.resolve()),
        "backup_root": str(backup_root.resolve()),
        "repaired_files": sum(item["repaired_files"] for item in reports),
        "repaired_paths": sum(item["repaired_paths"] for item in reports),
        "trees": reports,
    }
    report_path = args.target_workspace / "recovery" / f"path_restore_report_{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "report": str(report_path.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
