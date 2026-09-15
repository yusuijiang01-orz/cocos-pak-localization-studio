from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .database import default_knowledge_db, init_knowledge_db, init_project_db, utcnow
from .knowledge import relevant_glossary_terms
from .normalize import file_fingerprint, normalize_source
from .protection import protected_signature, split_runtime_text
from .qa import evaluate_translation, is_build_safe, severity_rank
from .workspace_ingest import ingest_workspace_records


def records_path(workspace: Path) -> Path:
    return Path(workspace).resolve() / "localization" / "text_records.json"


def records_sha256(workspace: Path) -> str:
    path = records_path(workspace)
    if not path.is_file():
        return ""
    return file_fingerprint(path.read_bytes())


def workspace_sync_status(workspace: Path, project_db_path: Path | None = None) -> dict[str, Any]:
    """Report whether vNext still describes the current legacy record cache.

    Legacy import/translation tools are allowed to keep working, but any mutation of
    text_records.json invalidates the vNext build baseline until the user explicitly
    synchronizes/adopts that state again.
    """
    workspace = Path(workspace).resolve()
    db_path = Path(project_db_path or (workspace / "vnext" / "project.sqlite3"))
    path = records_path(workspace)
    if not path.is_file():
        return {
            "ok": False,
            "synced": False,
            "state": "missing_records",
            "workspace": str(workspace),
            "records_path": str(path),
            "message": "缺少 localization/text_records.json",
        }

    current = records_sha256(workspace)
    db = init_project_db(db_path)
    try:
        row = db.execute("SELECT value FROM meta WHERE key='workspace_records_sha256'").fetchone()
        stored = str(row["value"] if row else "")
        at = db.execute("SELECT value FROM meta WHERE key='workspace_records_synced_at'").fetchone()
        synced_at = str(at["value"] if at else "")
    finally:
        db.close()

    if not stored:
        state = "never_synced"
        message = "vNext 尚未同步当前工作区"
        synced = False
    elif stored != current:
        state = "stale"
        message = "旧版工具或外部程序已修改 text_records.json；请先同步/吸收后再使用 vNext 构建"
        synced = False
    else:
        state = "synced"
        message = "vNext 与当前工作区一致"
        synced = True
    return {
        "ok": True,
        "synced": synced,
        "state": state,
        "workspace": str(workspace),
        "records_path": str(path),
        "stored_sha256": stored,
        "current_sha256": current,
        "synced_at": synced_at,
        "message": message,
    }


def require_workspace_synced(workspace: Path, project_db_path: Path | None = None) -> dict[str, Any]:
    status = workspace_sync_status(workspace, project_db_path)
    if not status.get("synced"):
        raise ValueError(status.get("message") or "vNext 工作区基线已过期")
    return status


def _runtime_parts(record: dict[str, Any]):
    source = str(record.get("source_original") or record.get("original") or "")
    current = str(record.get("original") or "")
    return source, current, split_runtime_text(source), split_runtime_text(current)


def adopt_legacy_targets(
    workspace: Path,
    project_db_path: Path | None = None,
    knowledge_db_path: Path | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Adopt safe current legacy translations as project-local vNext candidates.

    This deliberately does *not* promote legacy output into the global TM. Old Google,
    CSV, XLSX, script, glossary and file-Ollama results may be semantically poor even
    when structurally safe. Only a later human approval may promote them into trusted TM.
    """
    workspace = Path(workspace).resolve()
    project_db_path = Path(project_db_path or (workspace / "vnext" / "project.sqlite3"))
    knowledge_db_path = Path(knowledge_db_path or default_knowledge_db())

    ingest = ingest_workspace_records(workspace, project_db_path)
    path = records_path(workspace)
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("text_records.json 不是记录数组")
    by_id = {
        str(item.get("id") or ""): item
        for item in records
        if isinstance(item, dict) and str(item.get("id") or "")
    }

    project = init_project_db(project_db_path)
    knowledge = init_knowledge_db(knowledge_db_path)
    stats: Counter[str] = Counter()
    proposals: dict[str, set[str]] = defaultdict(set)
    proposal_sources: dict[str, str] = {}
    cache: dict[str, tuple[str, str, list[Any], list[Any]]] = {}
    try:
        rows = project.execute(
            """SELECT o.unit_id,o.record_id,o.locator_json,u.source_text
               FROM occurrences o JOIN translation_units u ON u.unit_id=o.unit_id
               WHERE o.active=1 ORDER BY o.record_id,o.occurrence_id"""
        ).fetchall()
        for row in rows:
            record_id = str(row["record_id"] or "")
            record = by_id.get(record_id)
            if not record:
                stats["missing_record"] += 1
                continue
            if record_id not in cache:
                cache[record_id] = _runtime_parts(record)
            source_runtime, current_runtime, source_pieces, current_pieces = cache[record_id]
            if normalize_source(source_runtime) == normalize_source(current_runtime):
                stats["unchanged"] += 1
                continue
            if len(source_pieces) != len(current_pieces) or protected_signature(source_pieces) != protected_signature(current_pieces):
                stats["structure_mismatch"] += 1
                continue
            try:
                locator = json.loads(row["locator_json"] or "{}")
                index = int(locator.get("span_index"))
            except Exception:
                stats["bad_locator"] += 1
                continue
            if index < 0 or index >= len(source_pieces) or index >= len(current_pieces):
                stats["bad_locator"] += 1
                continue
            source_piece = source_pieces[index]
            current_piece = current_pieces[index]
            if source_piece.kind != "text" or current_piece.kind != "text":
                stats["span_kind_mismatch"] += 1
                continue
            source_text = str(row["source_text"] or source_piece.value)
            target_text = str(current_piece.value or "").strip()
            if not target_text or normalize_source(target_text) == normalize_source(source_text):
                stats["unchanged_span"] += 1
                continue

            findings = evaluate_translation(
                source_text,
                target_text,
                required_terms=relevant_glossary_terms(knowledge, source_text),
            )
            if not is_build_safe(findings):
                stats["unsafe"] += 1
                continue
            # Warnings are kept visible in the report but are structurally build-safe.
            if any(severity_rank(item.severity) == 1 for item in findings):
                stats["warning"] += 1
            proposals[str(row["unit_id"])].add(target_text)
            proposal_sources[str(row["unit_id"])] = source_text
            stats["safe_proposals"] += 1

        now = utcnow()
        adopted = 0
        conflicts: list[dict[str, Any]] = []
        for unit_id, targets in proposals.items():
            if len(targets) != 1:
                stats["conflict"] += 1
                conflicts.append({
                    "unit_id": unit_id,
                    "source": proposal_sources.get(unit_id, ""),
                    "targets": sorted(targets)[:8],
                })
                continue
            target = next(iter(targets))
            existing = project.execute("SELECT * FROM current_targets WHERE unit_id=?", (unit_id,)).fetchone()
            if existing and str(existing["target_text"] or "").strip() and not overwrite:
                stats["kept_existing"] += 1
                continue
            if existing and int(existing["locked"] or 0):
                stats["kept_locked"] += 1
                continue
            project.execute(
                """INSERT INTO current_targets(
                     unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at
                   ) VALUES(?,?,?,'legacy:workspace','','passed',0,?)
                   ON CONFLICT(unit_id) DO UPDATE SET
                     target_text=excluded.target_text,target_status=excluded.target_status,
                     origin=excluded.origin,knowledge_ref='',qa_status='passed',locked=0,updated_at=excluded.updated_at""",
                (unit_id, target, "legacy_candidate", now),
            )
            adopted += 1
        project.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('legacy_targets_adopted_at',?)",
            (now,),
        )
        project.commit()
        stats["adopted"] = adopted
        return {
            "ok": True,
            "workspace": str(workspace),
            "ingest": ingest,
            "stats": dict(sorted(stats.items())),
            "conflicts": conflicts[:50],
            "note": "旧版译文只作为当前项目候选，不会自动写入全局 TM。人工批准后才会成为可信 TM。",
            "sync": workspace_sync_status(workspace, project_db_path),
        }
    finally:
        knowledge.close()
        project.close()
