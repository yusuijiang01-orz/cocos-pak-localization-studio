#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
from pathlib import Path

from localization_analyzer import decode_best
from localization_tm import validate_tokens
from localization_tm import add_tm, init_db, normalize_key
from pak_builder import materialize_records_to_modified_dir
from pc_content_migrator import migrate
from tsv_localization import validate_translation


def _reference_base_candidates(workspace: Path, pc_root: Path) -> list[Path]:
    bases: list[Path] = []

    def add(path: Path):
        if path not in bases:
            bases.append(path)

    if workspace.name in {"解包", "unpack", "extracted"}:
        add(workspace.parent)
        add(workspace)
    else:
        add(workspace)
        add(workspace.parent)
    if pc_root.name.lower() == "paks+":
        add(pc_root.parent)

    for ancestor in workspace.parents:
        if (ancestor / "_pc_reference").exists() or (ancestor / "paks+").exists():
            add(ancestor)

    return bases


def _ensure_pc_reference(pc_root: Path, workspace: Path, progress=None) -> Path:
    """Cache the complete PC workspace once so the external paks+ may be removed."""
    # A caller-supplied analyzed workspace is authoritative. Only the historical
    # external `paks+` directory needs to be copied into the persistent cache.
    supplied_records = pc_root / "localization" / "text_records.json"
    if supplied_records.is_file() and pc_root.name.lower() != "paks+":
        return pc_root
    candidates = [(base / "_pc_reference" / "paks+") for base in _reference_base_candidates(workspace, pc_root)]
    for reference in candidates:
        if (reference / "localization" / "text_records.json").is_file():
            return reference

    source_candidates = [pc_root]
    for base in _reference_base_candidates(workspace, pc_root):
        source = base / "paks+"
        if source not in source_candidates:
            source_candidates.append(source)
    source_root = next(
        (source for source in source_candidates if (source / "localization" / "text_records.json").is_file()),
        None,
    )
    if source_root is None:
        checked = " / ".join(str(path) for path in [*source_candidates, *candidates])
        raise FileNotFoundError(f"国际版源与安全缓存都不存在：{checked}")
    if progress:
        progress({"percent": 1, "message": "首次运行：正在把完整 PC 参考工作区复制到安全区域…"})
    reference = candidates[0]
    for candidate in candidates:
        if candidate.parent.exists() or candidate.parent.parent.exists():
            reference = candidate
            break
    records = reference / "localization" / "text_records.json"
    reference.parent.mkdir(parents=True, exist_ok=True)
    staging = reference.parent / f"paks+.copying-{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    shutil.copytree(source_root, staging)
    os.replace(staging, reference)
    manifest = {
        "source": str(source_root.resolve()), "cached_at": dt.datetime.now().isoformat(timespec="seconds"),
        "records": str(records.resolve()), "mode": "complete-pc-workspace-copy",
    }
    (reference.parent / "reference_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return reference


def _learn_trusted_translations(records: list[dict], changed_ids: set[str], db_path: Path) -> dict:
    candidates = {}
    conflicts = set()
    for record in records:
        note = str(record.get("note", ""))
        previously_trusted = "[PC迁移:" in note or "[人工TSV合并]" in note or record.get("status") in ("已审核", "人工确认")
        if str(record.get("id")) not in changed_ids and not previously_trusted:
            continue
        source = str(record.get("source_original", "")).strip()
        target = str(record.get("original", "")).strip()
        if not source or not target or source == target:
            continue
        key = normalize_key(source)
        old = candidates.get(key)
        if old and normalize_key(old[1]) != normalize_key(target):
            conflicts.add(key)
        else:
            candidates[key] = (source, target)
    db = init_db(Path(db_path))
    learned, rejected = 0, 0
    for key, (source, target) in candidates.items():
        if key in conflicts:
            rejected += 1
            continue
        try:
            add_tm(db, source, target, "approved")
            learned += 1
        except ValueError:
            rejected += 1
    db.commit()
    db.close()
    return {"learned": learned, "rejected": rejected, "conflicts": len(conflicts), "db": str(Path(db_path).resolve())}


def _manual_tsv_updates(records: list[dict], workspace: Path, paths: list[Path]):
    by_file = {}
    for record in records:
        by_file.setdefault((record.get("pak"), record.get("source_file")), []).append(record)
    changed_ids, reports = set(), []
    cache_dir = workspace / "manual_zh_overrides" / "updatefs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for supplied in paths:
        source = Path(supplied)
        if not source.is_file():
            reports.append({"file": str(source), "status": "missing", "updated": 0})
            continue
        cached = cache_dir / source.name
        if source.resolve() != cached.resolve():
            shutil.copy2(source, cached)
        target_records = by_file.get(("updatefs.pak", source.name), [])
        extracted = workspace / "extracted" / "updatefs" / source.name
        if not extracted.is_file() or not target_records:
            reports.append({"file": str(source), "status": "not_in_updatefs", "updated": 0})
            continue
        manual_lines = cached.read_bytes().splitlines()
        original_lines = extracted.read_bytes().splitlines()
        if len(manual_lines) != len(original_lines):
            reports.append({"file": str(source), "status": "line_count_mismatch", "updated": 0,
                            "manual_lines": len(manual_lines), "original_lines": len(original_lines)})
            continue
        if any(a.count(b"\t") != b.count(b"\t") for a, b in zip(manual_lines, original_lines)):
            reports.append({"file": str(source), "status": "column_count_mismatch", "updated": 0})
            continue
        locators = {(int(r.get("line") or 0), int(r.get("column") or 1)): r for r in target_records}
        updated, rejected = 0, 0
        for row_no, raw in enumerate(manual_lines, 1):
            if extracted.suffix.lower() == ".tsv" and row_no == 1:
                continue
            original_cells = original_lines[row_no - 1].split(b"\t")
            for col_no, cell in enumerate(raw.split(b"\t"), 1):
                record = locators.get((row_no, col_no))
                if not record:
                    continue
                text, _encoding, _language, _score = decode_best(cell)
                text = str(text or "").strip()
                if not text or not any("\u3400" <= ch <= "\u9fff" for ch in text):
                    continue
                # Always anchor source_original to the untouched extracted PAK.
                # A previous merge may already have copied Chinese into both
                # `original` and `source_original`; in that state materialization
                # incorrectly sees no delta and silently rebuilds Vietnamese.
                source_text, _src_encoding, _src_language, _src_score = decode_best(
                    original_cells[col_no - 1]
                )
                source_text = str(source_text or "").strip()
                if not validate_translation(source_text, text)[0] or not validate_tokens(source_text, text)[0]:
                    rejected += 1
                    continue
                if str(record.get("original", "")) == text and source_text == text:
                    continue
                record["source_original"] = source_text
                record["original"] = text
                record["translation"] = text
                record["language"] = "zh"
                record["status"] = "已翻译"
                record["note"] = (str(record.get("note", "")).strip() + " [人工TSV合并]").strip()
                changed_ids.add(str(record.get("id")))
                updated += 1
        reports.append({"file": str(source), "cached": str(cached), "status": "merged",
                        "updated": updated, "rejected": rejected})
    return changed_ids, reports


def run(pc_root: Path, workspace: Path, db_path: Path, pak_scope: str,
        manual_paths: list[Path], progress=None) -> dict:
    pc_root, workspace = Path(pc_root), Path(workspace)
    pc_root = _ensure_pc_reference(pc_root, workspace, progress)
    records_path = workspace / "localization" / "text_records.json"
    if not (pc_root / "localization" / "text_records.json").is_file():
        raise FileNotFoundError(f"国际版记录不存在：{pc_root / 'localization' / 'text_records.json'}")
    if not records_path.is_file():
        raise FileNotFoundError(f"越南版记录不存在：{records_path}")
    before_bytes = records_path.read_bytes()
    before = json.loads(before_bytes.decode("utf-8"))
    before_by_id = {str(r.get("id")): r for r in before}
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = workspace / "_safe_merge_backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "text_records.json").write_bytes(before_bytes)
    if progress:
        progress({"percent": 5, "message": "正在计算 PC 国际版安全结构匹配…"})
    with tempfile.TemporaryDirectory(prefix="pakloc_pc_merge_") as temp:
        def semantic_progress(event):
            if not progress:
                return
            done, total = int(event.get("done", 0)), max(1, int(event.get("total", 1)))
            progress({"percent": 5 + round(done / total * 45, 1),
                      "message": event.get("message", "正在进行国际版语义补漏…")})
        migration = migrate(pc_root, workspace, Path(temp), semantic=False,
                            progress=semantic_progress, pak_scope=pak_scope)
        merged = json.loads(Path(migration["records_output"]).read_text(encoding="utf-8"))
        alignment_dir = workspace / "build" / f"pc_alignment_{Path(pak_scope).stem}_{stamp}"
        alignment_dir.mkdir(parents=True, exist_ok=True)
        for name in ("migration_rows.csv", "file_relations.csv", "migration_conflicts.csv", "migration_report.json"):
            source = Path(temp) / name
            if source.is_file():
                shutil.copy2(source, alignment_dir / name)
    if pak_scope:
        merged = [
            record if record.get("pak") == pak_scope else dict(before_by_id.get(str(record.get("id")), record))
            for record in merged
        ]
    pc_changed = {
        str(r.get("id")) for r in merged
        if str(r.get("original", "")) != str(before_by_id.get(str(r.get("id")), {}).get("original", ""))
    }
    if progress:
        progress({"percent": 55, "message": f"PC 安全中文已匹配 {len(pc_changed):,} 条；正在合并人工 TSV…"})
    selected_manual = manual_paths if pak_scope == "updatefs.pak" else []
    manual_changed, manual_reports = _manual_tsv_updates(merged, workspace, selected_manual)
    changed_ids = pc_changed | manual_changed
    changed_paks = sorted({str(r.get("pak")) for r in merged if str(r.get("id")) in changed_ids and r.get("pak")})
    temp_records = records_path.with_suffix(".json.tmp")
    temp_records.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp_records, records_path)
    tm_report = _learn_trusted_translations(merged, changed_ids, Path(db_path))
    materialized = []
    for index, pak in enumerate(changed_paks, 1):
        base = Path(pak).stem
        extracted = workspace / "extracted" / base
        if not extracted.is_dir():
            materialized.append({"pak": pak, "status": "missing_extracted"})
            continue
        if progress:
            progress({"percent": 60 + round(index / max(1, len(changed_paks)) * 35, 1),
                      "message": f"正在生成可构建资源：{pak}（{index}/{len(changed_paks)}）"})
        report = materialize_records_to_modified_dir(extracted, records_path, pak, workspace / "modified" / base)
        materialized.append({"pak": pak, "status": "ready", **report})
    report = {
        "pc_root": str(pc_root.resolve()), "workspace": str(workspace.resolve()), "pak_scope": pak_scope,
        "backup": str((backup_dir / "text_records.json").resolve()),
        "pc_safe_merged": len(pc_changed), "manual_merged": len(manual_changed),
        "total_changed": len(changed_ids), "changed_paks": changed_paks,
        "manual_files": manual_reports, "materialized": materialized,
        "translation_memory": tm_report,
        "migration_methods": migration.get("methods", {}),
        "semantic_match": migration.get("semantic", {}),
        "coverage_before": migration.get("coverage_before", {}),
        "coverage_after": migration.get("coverage_after", {}),
        "cross_file_relations": migration.get("cross_file_relations", 0),
        "conflicts_skipped": migration.get("conflicts_skipped", 0),
        "alignment_report_dir": str(alignment_dir.resolve()),
    }
    report_path = workspace / "safe_pc_merge_report.json"
    report["report"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress({"percent": 100, "message": f"安全中文合并完成：共更新 {len(changed_ids):,} 条"})
    return report
