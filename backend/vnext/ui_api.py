from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import default_knowledge_db, init_knowledge_db, init_project_db, put_tm, utcnow
from .normalize import normalize_source, source_key


def _count_map(rows, key: str, value: str = "n") -> dict[str, int]:
    return {str(row[key] or ""): int(row[value] or 0) for row in rows}


def _project_json(workspace: Path) -> dict[str, Any]:
    path = Path(workspace) / "project.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def dashboard(
    workspace: Path,
    project_db_path: Path | None = None,
    knowledge_db_path: Path | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    project_db_path = Path(project_db_path or (workspace / "vnext" / "project.sqlite3"))
    project = init_project_db(project_db_path)
    knowledge = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    legacy = _project_json(workspace)
    try:
        project_row = project.execute("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 1").fetchone()
        pid = str(project_row["project_id"] if project_row else "")
        if pid:
            unique_units = int(project.execute(
                "SELECT COUNT(DISTINCT unit_id) FROM occurrences WHERE project_id=? AND active=1", (pid,)
            ).fetchone()[0])
            occurrences = int(project.execute(
                "SELECT COUNT(*) FROM occurrences WHERE project_id=? AND active=1", (pid,)
            ).fetchone()[0])
            languages = _count_map(project.execute(
                """SELECT u.source_lang AS k,COUNT(DISTINCT u.unit_id) AS n
                   FROM translation_units u JOIN occurrences o ON o.unit_id=u.unit_id
                   WHERE o.project_id=? AND o.active=1 GROUP BY u.source_lang""", (pid,)
            ), "k")
            kinds = _count_map(project.execute(
                """SELECT u.unit_kind AS k,COUNT(DISTINCT u.unit_id) AS n
                   FROM translation_units u JOIN occurrences o ON o.unit_id=u.unit_id
                   WHERE o.project_id=? AND o.active=1 GROUP BY u.unit_kind""", (pid,)
            ), "k")
            target_status = _count_map(project.execute(
                """SELECT COALESCE(t.target_status,'untranslated') AS k,COUNT(DISTINCT o.unit_id) AS n
                   FROM occurrences o LEFT JOIN current_targets t ON t.unit_id=o.unit_id
                   WHERE o.project_id=? AND o.active=1 GROUP BY COALESCE(t.target_status,'untranslated')""", (pid,)
            ), "k")
            target_origin = _count_map(project.execute(
                """SELECT COALESCE(NULLIF(t.origin,''),'untranslated') AS k,COUNT(DISTINCT o.unit_id) AS n
                   FROM occurrences o LEFT JOIN current_targets t ON t.unit_id=o.unit_id
                   WHERE o.project_id=? AND o.active=1 GROUP BY COALESCE(NULLIF(t.origin,''),'untranslated')""", (pid,)
            ), "k")
            qa_status = _count_map(project.execute(
                """SELECT COALESCE(NULLIF(t.qa_status,''),'unchecked') AS k,COUNT(DISTINCT o.unit_id) AS n
                   FROM occurrences o LEFT JOIN current_targets t ON t.unit_id=o.unit_id
                   WHERE o.project_id=? AND o.active=1 GROUP BY COALESCE(NULLIF(t.qa_status,''),'unchecked')""", (pid,)
            ), "k")
        else:
            unique_units = occurrences = 0
            languages = {}
            kinds = {}
            target_status = {}
            target_origin = {}
            qa_status = {}

        translated = sum(
            count for status, count in target_status.items()
            if status not in ("untranslated", "rejected", "failed", "")
        )
        rejected = int(target_status.get("rejected", 0) + target_status.get("failed", 0))
        pending = max(0, unique_units - translated - rejected)

        review = _count_map(project.execute(
            "SELECT state AS k,COUNT(*) AS n FROM review_items GROUP BY state"
        ), "k")
        review_severity = _count_map(project.execute(
            "SELECT severity AS k,COUNT(*) AS n FROM review_items WHERE state='pending' GROUP BY severity"
        ), "k")
        latest_job = project.execute(
            "SELECT * FROM translation_jobs ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        latest_build = project.execute(
            "SELECT * FROM build_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        knowledge_counts = {
            "tm": int(knowledge.execute("SELECT COUNT(*) FROM translation_memory").fetchone()[0]),
            "tm_locked": int(knowledge.execute("SELECT COUNT(*) FROM translation_memory WHERE locked=1").fetchone()[0]),
            "glossary": int(knowledge.execute("SELECT COUNT(*) FROM glossary_terms").fetchone()[0]),
            "glossary_active": int(knowledge.execute(
                "SELECT COUNT(*) FROM glossary_terms WHERE locked=1 OR status IN ('approved','locked')"
            ).fetchone()[0]),
            "reference": int(knowledge.execute("SELECT COUNT(*) FROM canonical_reference").fetchone()[0]),
            "cache": int(knowledge.execute("SELECT COUNT(*) FROM model_cache WHERE qa_status='passed'").fetchone()[0]),
        }
        tm_quality = _count_map(knowledge.execute(
            "SELECT quality AS k,COUNT(*) AS n FROM translation_memory GROUP BY quality"
        ), "k")

        paks = []
        for item in legacy.get("paks", []) if isinstance(legacy.get("paks"), list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("pak") or "").strip()
            if name:
                paks.append({
                    "pak": name,
                    "path": str(item.get("path") or ""),
                    "extracted": str(item.get("extracted") or ""),
                })

        return {
            "workspace": str(workspace),
            "project": dict(project_row) if project_row else None,
            "has_legacy_project": bool(legacy),
            "has_records": (workspace / "localization" / "text_records.json").is_file(),
            "unique_units": unique_units,
            "active_occurrences": occurrences,
            "translated_unique": translated,
            "pending_unique": pending,
            "rejected_unique": rejected,
            "languages": languages,
            "kinds": kinds,
            "target_status": target_status,
            "target_origin": target_origin,
            "qa_status": qa_status,
            "review": review,
            "review_severity": review_severity,
            "knowledge": knowledge_counts,
            "tm_quality": tm_quality,
            "latest_job": dict(latest_job) if latest_job else None,
            "latest_build": dict(latest_build) if latest_build else None,
            "paks": paks,
        }
    finally:
        knowledge.close()
        project.close()


def knowledge_list(
    knowledge_db_path: Path | None,
    *,
    kind: str,
    query: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    db = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    q = f"%{str(query or '').strip()}%"
    limit = max(1, min(1000, int(limit)))
    offset = max(0, int(offset))
    try:
        if kind == "tm":
            where = "WHERE source_text LIKE ? OR target_text LIKE ?" if query else ""
            params = (q, q, limit, offset) if query else (limit, offset)
            rows = db.execute(
                f"""SELECT tm_id,source_text,target_text,quality,locked,provenance,usage_count,updated_at
                    FROM translation_memory {where}
                    ORDER BY locked DESC,usage_count DESC,updated_at DESC LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
            total = db.execute(
                f"SELECT COUNT(*) FROM translation_memory {where}", (q, q) if query else ()
            ).fetchone()[0]
        elif kind == "glossary":
            where = "WHERE source_text LIKE ? OR target_text LIKE ?" if query else ""
            params = (q, q, limit, offset) if query else (limit, offset)
            rows = db.execute(
                f"""SELECT term_id,source_text,target_text,term_type,scope,status,priority,locked,provenance,note,updated_at
                    FROM glossary_terms {where}
                    ORDER BY locked DESC,priority DESC,updated_at DESC LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
            total = db.execute(
                f"SELECT COUNT(*) FROM glossary_terms {where}", (q, q) if query else ()
            ).fetchone()[0]
        elif kind == "reference":
            where = "WHERE source_alias LIKE ? OR canonical_zh LIKE ?" if query else ""
            params = (q, q, limit, offset) if query else (limit, offset)
            rows = db.execute(
                f"""SELECT ref_id,source_alias,canonical_zh,entity_type,status,provenance,note,updated_at
                    FROM canonical_reference {where}
                    ORDER BY CASE status WHEN 'locked' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,updated_at DESC
                    LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
            total = db.execute(
                f"SELECT COUNT(*) FROM canonical_reference {where}", (q, q) if query else ()
            ).fetchone()[0]
        else:
            raise ValueError(f"Unsupported knowledge kind: {kind}")
        return {"kind": kind, "total": int(total), "items": [dict(row) for row in rows]}
    finally:
        db.close()


def save_glossary(
    knowledge_db_path: Path | None,
    *,
    source: str,
    target: str,
    term_type: str = "general",
    scope: str = "global",
    status: str = "approved",
    priority: int = 100,
    locked: bool = True,
    note: str = "",
) -> dict[str, Any]:
    source = normalize_source(source)
    target = str(target or "").strip()
    if not source or not target:
        raise ValueError("术语原文和中文译名不能为空")
    if status not in ("candidate", "approved", "locked", "disabled", "conflict"):
        raise ValueError("不支持的术语状态")
    db = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    now = utcnow()
    try:
        skey = source_key(source)
        db.execute(
            """INSERT INTO glossary_terms(
                 source_key,source_text,target_text,term_type,scope,status,priority,locked,
                 provenance,note,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key,scope) DO UPDATE SET
                 source_text=excluded.source_text,target_text=excluded.target_text,
                 term_type=excluded.term_type,status=excluded.status,priority=excluded.priority,
                 locked=excluded.locked,provenance=excluded.provenance,note=excluded.note,
                 updated_at=excluded.updated_at""",
            (
                skey, source, target, str(term_type or "general"), str(scope or "global"),
                status, int(priority), int(bool(locked)), "vnext-ui", str(note or ""), now, now,
            ),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM glossary_terms WHERE source_key=? AND scope=?", (skey, str(scope or "global"))
        ).fetchone()
        return {"ok": True, "item": dict(row)}
    finally:
        db.close()


def delete_glossary(knowledge_db_path: Path | None, term_id: int) -> dict[str, Any]:
    db = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    try:
        cur = db.execute("DELETE FROM glossary_terms WHERE term_id=?", (int(term_id),))
        db.commit()
        return {"ok": cur.rowcount > 0, "deleted": int(cur.rowcount)}
    finally:
        db.close()


def save_tm(
    knowledge_db_path: Path | None,
    *,
    source: str,
    target: str,
    locked: bool = True,
) -> dict[str, Any]:
    source = normalize_source(source)
    target = str(target or "").strip()
    if not source or not target:
        raise ValueError("TM 原文和中文译文不能为空")
    db = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    try:
        tm_id = put_tm(
            db,
            source_text=source,
            target_text=target,
            quality="manual",
            locked=bool(locked),
            provenance="vnext-ui",
        )
        db.commit()
        row = db.execute("SELECT * FROM translation_memory WHERE tm_id=?", (tm_id,)).fetchone()
        return {"ok": True, "item": dict(row)}
    finally:
        db.close()


def delete_tm(knowledge_db_path: Path | None, tm_id: int) -> dict[str, Any]:
    db = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    try:
        cur = db.execute("DELETE FROM translation_memory WHERE tm_id=?", (int(tm_id),))
        db.commit()
        return {"ok": cur.rowcount > 0, "deleted": int(cur.rowcount)}
    finally:
        db.close()
