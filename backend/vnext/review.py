from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import default_knowledge_db, init_knowledge_db, init_project_db, put_tm, utcnow
from .knowledge import relevant_glossary_terms
from .normalize import normalize_source, sha256_text
from .qa import evaluate_translation, finding_codes, highest_severity, is_build_safe, severity_rank


REVIEW_STATES = ("pending", "approved", "rejected", "deferred", "resolved")
RESOLVED_TARGET_STATUSES = ("manual", "approved", "tm", "reference")


def _target_fingerprint(target: str) -> str:
    value = normalize_source(target)
    return sha256_text(value) if value else ""


def is_target_blocked(db: sqlite3.Connection, unit_id: str, target: str) -> bool:
    fingerprint = _target_fingerprint(target)
    if not fingerprint:
        return False
    return db.execute(
        "SELECT 1 FROM blocked_targets WHERE unit_id=? AND target_fingerprint=?",
        (unit_id, fingerprint),
    ).fetchone() is not None


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except Exception:
        return []


def _priority(severity: str, occurrence_count: int, unit_kind: str, reason_code: str) -> int:
    base = {"fatal": 1000, "error": 800, "warning": 500, "info": 100}.get(severity, 100)
    if unit_kind in ("corrupt_source", "mixed_source"):
        base += 160
    if unit_kind == "proper_noun":
        base += 80
    if reason_code in ("PROTECTED_TOKEN_MISMATCH", "NUMBER_MISMATCH", "ENCODING_REPLACEMENT_CHAR"):
        base += 120
    return base + min(200, max(0, int(occurrence_count)))


def _replace_unresolved_findings(db: sqlite3.Connection, unit_id: str, findings) -> None:
    now = utcnow()
    db.execute("UPDATE qa_findings SET resolved=1 WHERE unit_id=? AND resolved=0", (unit_id,))
    for item in findings:
        db.execute(
            """INSERT INTO qa_findings(unit_id,code,severity,message,detail_json,resolved,created_at)
               VALUES(?,?,?,?,?,0,?)""",
            (
                unit_id,
                item.code,
                item.severity.value,
                item.message,
                json.dumps(item.detail, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )


def _set_review_item(
    db: sqlite3.Connection,
    *,
    unit_id: str,
    state: str,
    priority: int,
    reason_code: str,
    severity: str,
    source: str,
    target: str,
    qa_codes: list[str],
    occurrence_count: int,
    note: str = "",
) -> None:
    if state not in REVIEW_STATES:
        raise ValueError(f"Unsupported review state: {state}")
    now = utcnow()
    db.execute(
        """INSERT INTO review_items(
             unit_id,state,priority,reason_code,severity,source_snapshot,target_snapshot,target_fingerprint,
             qa_codes_json,occurrence_count,note,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(unit_id) DO UPDATE SET
             state=excluded.state,priority=excluded.priority,reason_code=excluded.reason_code,
             severity=excluded.severity,source_snapshot=excluded.source_snapshot,
             target_snapshot=excluded.target_snapshot,target_fingerprint=excluded.target_fingerprint,
             qa_codes_json=excluded.qa_codes_json,occurrence_count=excluded.occurrence_count,
             note=excluded.note,updated_at=excluded.updated_at""",
        (
            unit_id,
            state,
            int(priority),
            reason_code,
            severity,
            source,
            target,
            _target_fingerprint(target),
            json.dumps(qa_codes, ensure_ascii=False, separators=(",", ":")),
            int(occurrence_count),
            note,
            now,
            now,
        ),
    )


def _log_action(
    db: sqlite3.Connection,
    unit_id: str,
    action: str,
    *,
    before_target: str = "",
    after_target: str = "",
    tm_id: int = 0,
    note: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    db.execute(
        """INSERT INTO review_actions(unit_id,action,before_target,after_target,tm_id,note,detail_json,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            unit_id,
            action,
            before_target,
            after_target,
            int(tm_id or 0),
            note,
            json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
            utcnow(),
        ),
    )


def sync_review_queue(
    project_db_path: Path,
    knowledge_db_path: Path | None = None,
) -> dict[str, Any]:
    """Refresh the risk queue without reopening an unchanged human-approved target."""
    project = init_project_db(Path(project_db_path))
    knowledge = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    stats = {"queued": 0, "pending": 0, "deferred": 0, "approved": 0, "rejected": 0, "resolved": 0}
    try:
        units = list(
            project.execute(
                """SELECT u.*,COUNT(o.occurrence_id) AS occurrence_count
                   FROM translation_units u
                   JOIN occurrences o ON o.unit_id=u.unit_id AND o.active=1
                   GROUP BY u.unit_id ORDER BY u.unit_id"""
            )
        )
        for unit in units:
            current = project.execute(
                "SELECT * FROM current_targets WHERE unit_id=?", (unit["unit_id"],)
            ).fetchone()
            existing = project.execute(
                "SELECT * FROM review_items WHERE unit_id=?", (unit["unit_id"],)
            ).fetchone()
            target = str(current["target_text"] if current else "")
            fingerprint = _target_fingerprint(target)

            if (
                existing
                and existing["state"] == "approved"
                and existing["target_fingerprint"] == fingerprint
                and current
                and bool(current["locked"])
                and current["target_status"] in RESOLVED_TARGET_STATUSES
            ):
                stats["approved"] += 1
                continue

            risk_flags = _json_list(unit["risk_flags_json"])
            findings = []
            if target:
                findings = evaluate_translation(
                    unit["source_text"],
                    target,
                    required_terms=relevant_glossary_terms(knowledge, unit["source_text"]),
                )
                _replace_unresolved_findings(project, unit["unit_id"], findings)

            reason_code = ""
            severity = "info"
            qa_codes = finding_codes(findings)
            if current and (current["qa_status"] in ("failed", "rejected") or current["target_status"] == "rejected"):
                reason_code, severity = "TARGET_REJECTED", "error"
            elif findings:
                worst = highest_severity(findings)
                warnings_or_worse = [f for f in findings if severity_rank(f.severity) >= 1]
                if warnings_or_worse:
                    chosen = max(warnings_or_worse, key=lambda item: severity_rank(item.severity))
                    reason_code, severity = chosen.code, worst.value
            if not reason_code and ("possible_mojibake" in risk_flags or unit["unit_kind"] == "corrupt_source"):
                reason_code, severity = "SOURCE_MOJIBAKE", "error"
                qa_codes = [*qa_codes, "SOURCE_MOJIBAKE"]
            if not reason_code and unit["unit_kind"] == "mixed_source":
                reason_code, severity = "MIXED_SOURCE_REVIEW", "warning"
                qa_codes = [*qa_codes, "MIXED_SOURCE_REVIEW"]

            if not reason_code:
                if existing and existing["state"] in ("pending", "deferred", "rejected"):
                    _set_review_item(
                        project,
                        unit_id=unit["unit_id"],
                        state="resolved",
                        priority=0,
                        reason_code="AUTO_RESOLVED",
                        severity="info",
                        source=unit["source_text"],
                        target=target,
                        qa_codes=[],
                        occurrence_count=unit["occurrence_count"],
                        note=existing["note"],
                    )
                    stats["resolved"] += 1
                continue

            state = "pending"
            note = ""
            if existing and existing["target_fingerprint"] == fingerprint and existing["state"] in (
                "approved", "rejected", "deferred"
            ):
                state = existing["state"]
                note = existing["note"]
            priority = _priority(severity, unit["occurrence_count"], unit["unit_kind"], reason_code)
            _set_review_item(
                project,
                unit_id=unit["unit_id"],
                state=state,
                priority=priority,
                reason_code=reason_code,
                severity=severity,
                source=unit["source_text"],
                target=target,
                qa_codes=qa_codes,
                occurrence_count=unit["occurrence_count"],
                note=note,
            )
            stats["queued"] += 1
            stats[state] = stats.get(state, 0) + 1
        project.commit()
        return stats
    finally:
        knowledge.close()
        project.close()


def list_review_items(
    project_db_path: Path,
    *,
    state: str = "pending",
    severity: str = "",
    limit: int = 100,
    offset: int = 0,
    query: str = "",
) -> list[dict[str, Any]]:
    db = init_project_db(Path(project_db_path))
    try:
        clauses = ["1=1"]
        params: list[Any] = []
        if state and state != "all":
            clauses.append("r.state=?")
            params.append(state)
        if severity:
            clauses.append("r.severity=?")
            params.append(severity)
        if query:
            clauses.append("(u.source_text LIKE ? OR r.target_snapshot LIKE ? OR r.reason_code LIKE ?)")
            needle = f"%{query}%"
            params.extend((needle, needle, needle))
        params.extend((max(1, int(limit)), max(0, int(offset))))
        rows = db.execute(
            f"""SELECT r.*,u.source_lang,u.unit_kind,u.word_count,u.risk_flags_json,
                       t.target_status,t.origin,t.qa_status,t.locked
                FROM review_items r
                JOIN translation_units u ON u.unit_id=r.unit_id
                LEFT JOIN current_targets t ON t.unit_id=r.unit_id
                WHERE {' AND '.join(clauses)}
                ORDER BY r.priority DESC,r.updated_at ASC
                LIMIT ? OFFSET ?""",
            params,
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["qa_codes"] = _json_list(item.pop("qa_codes_json", "[]"))
            item["risk_flags"] = _json_list(item.pop("risk_flags_json", "[]"))
            out.append(item)
        return out
    finally:
        db.close()


def review_detail(project_db_path: Path, unit_id: str) -> dict[str, Any]:
    db = init_project_db(Path(project_db_path))
    try:
        unit = db.execute("SELECT * FROM translation_units WHERE unit_id=?", (unit_id,)).fetchone()
        if not unit:
            raise KeyError(f"Translation unit not found: {unit_id}")
        target = db.execute("SELECT * FROM current_targets WHERE unit_id=?", (unit_id,)).fetchone()
        review = db.execute("SELECT * FROM review_items WHERE unit_id=?", (unit_id,)).fetchone()
        findings = [dict(row) for row in db.execute(
            "SELECT * FROM qa_findings WHERE unit_id=? AND resolved=0 ORDER BY finding_id", (unit_id,)
        )]
        occurrences = [dict(row) for row in db.execute(
            """SELECT pak_name,source_file,record_id,locator_json FROM occurrences
               WHERE unit_id=? AND active=1 ORDER BY pak_name,source_file,record_id LIMIT 50""", (unit_id,)
        )]
        actions = [dict(row) for row in db.execute(
            "SELECT * FROM review_actions WHERE unit_id=? ORDER BY action_id DESC LIMIT 50", (unit_id,)
        )]
        blocked = [dict(row) for row in db.execute(
            "SELECT * FROM blocked_targets WHERE unit_id=? ORDER BY created_at DESC", (unit_id,)
        )]
        return {
            "unit": dict(unit),
            "target": dict(target) if target else None,
            "review": dict(review) if review else None,
            "findings": findings,
            "occurrences": occurrences,
            "actions": actions,
            "blocked_targets": blocked,
        }
    finally:
        db.close()


def approve_review(
    project_db_path: Path,
    knowledge_db_path: Path | None,
    unit_id: str,
    *,
    target_text: str = "",
    note: str = "",
    lock: bool = True,
) -> dict[str, Any]:
    project = init_project_db(Path(project_db_path))
    knowledge = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    try:
        unit = project.execute("SELECT * FROM translation_units WHERE unit_id=?", (unit_id,)).fetchone()
        if not unit:
            raise KeyError(f"Translation unit not found: {unit_id}")
        current = project.execute("SELECT * FROM current_targets WHERE unit_id=?", (unit_id,)).fetchone()
        before = str(current["target_text"] if current else "")
        target = str(target_text if target_text else before).strip()
        if not target:
            raise ValueError("没有可审核的译文；请先提供人工译文")
        terms = relevant_glossary_terms(knowledge, unit["source_text"])
        findings = evaluate_translation(unit["source_text"], target, required_terms=terms)
        if not is_build_safe(findings):
            raise ValueError(
                "译文未通过安全 QA，不能批准：" + ", ".join(f.code for f in findings if severity_rank(f.severity) >= 2)
            )
        edited = bool(target_text and normalize_source(target) != normalize_source(before))
        quality = "manual" if edited or not before else "approved"
        tm_id = put_tm(
            knowledge,
            source_text=unit["source_text"],
            target_text=target,
            quality=quality,
            locked=lock,
            provenance=f"vnext-review:{unit_id}",
        )
        knowledge.commit()
        now = utcnow()
        project.execute(
            """INSERT INTO current_targets(unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at)
               VALUES(?,?,?,?,?,'passed',?,?)
               ON CONFLICT(unit_id) DO UPDATE SET target_text=excluded.target_text,target_status=excluded.target_status,
                 origin=excluded.origin,knowledge_ref=excluded.knowledge_ref,qa_status='passed',locked=excluded.locked,
                 updated_at=excluded.updated_at""",
            (unit_id, target, quality, f"review:{quality}", str(tm_id), int(bool(lock)), now),
        )
        project.execute(
            "DELETE FROM blocked_targets WHERE unit_id=? AND target_fingerprint=?",
            (unit_id, _target_fingerprint(target)),
        )
        project.execute("UPDATE qa_findings SET resolved=1 WHERE unit_id=? AND resolved=0", (unit_id,))
        warning_codes = [f.code for f in findings if severity_rank(f.severity) == 1]
        _set_review_item(
            project,
            unit_id=unit_id,
            state="approved",
            priority=0,
            reason_code="MANUAL_APPROVAL" if quality == "manual" else "REVIEW_APPROVAL",
            severity="info",
            source=unit["source_text"],
            target=target,
            qa_codes=warning_codes,
            occurrence_count=project.execute(
                "SELECT COUNT(*) FROM occurrences WHERE unit_id=? AND active=1", (unit_id,)
            ).fetchone()[0],
            note=note,
        )
        _log_action(
            project,
            unit_id,
            "approve",
            before_target=before,
            after_target=target,
            tm_id=tm_id,
            note=note,
            detail={"quality": quality, "locked": bool(lock), "warnings": warning_codes},
        )
        project.commit()
        return {
            "ok": True,
            "unit_id": unit_id,
            "target": target,
            "quality": quality,
            "locked": bool(lock),
            "tm_id": tm_id,
        }
    finally:
        knowledge.close()
        project.close()


def reject_review(
    project_db_path: Path,
    knowledge_db_path: Path | None,
    unit_id: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    project = init_project_db(Path(project_db_path))
    knowledge = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    try:
        unit = project.execute("SELECT * FROM translation_units WHERE unit_id=?", (unit_id,)).fetchone()
        if not unit:
            raise KeyError(f"Translation unit not found: {unit_id}")
        current = project.execute("SELECT * FROM current_targets WHERE unit_id=?", (unit_id,)).fetchone()
        before = str(current["target_text"] if current else "")
        knowledge_ref = str(current["knowledge_ref"] if current else "")
        origin = str(current["origin"] if current else "")
        if before:
            project.execute(
                """INSERT OR REPLACE INTO blocked_targets(
                     unit_id,target_fingerprint,target_text,origin,note,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (unit_id, _target_fingerprint(before), before, origin, note, utcnow()),
            )
        if current:
            project.execute(
                """UPDATE current_targets SET target_status='rejected',qa_status='rejected',locked=0,
                   origin='review:rejected',updated_at=? WHERE unit_id=?""",
                (utcnow(), unit_id),
            )
        if knowledge_ref:
            knowledge.execute(
                "UPDATE model_cache SET qa_status='rejected',updated_at=? WHERE cache_key=?",
                (utcnow(), knowledge_ref),
            )
            knowledge.commit()
        project.execute(
            """UPDATE job_items SET status='failed',last_error=?,updated_at=? WHERE unit_id=?""",
            ("人工审核拒绝；允许重新翻译，但禁止复用同一译文", utcnow(), unit_id),
        )
        _set_review_item(
            project,
            unit_id=unit_id,
            state="rejected",
            priority=900,
            reason_code="HUMAN_REJECTED",
            severity="error",
            source=unit["source_text"],
            target=before,
            qa_codes=[],
            occurrence_count=project.execute(
                "SELECT COUNT(*) FROM occurrences WHERE unit_id=? AND active=1", (unit_id,)
            ).fetchone()[0],
            note=note,
        )
        _log_action(
            project,
            unit_id,
            "reject",
            before_target=before,
            after_target=before,
            note=note,
            detail={"blocked_fingerprint": _target_fingerprint(before) if before else ""},
        )
        project.commit()
        return {"ok": True, "unit_id": unit_id, "state": "rejected", "blocked_target": bool(before)}
    finally:
        knowledge.close()
        project.close()


def set_review_state(project_db_path: Path, unit_id: str, state: str, *, note: str = "") -> dict[str, Any]:
    if state not in ("pending", "deferred"):
        raise ValueError("Only pending/deferred are allowed here")
    db = init_project_db(Path(project_db_path))
    try:
        row = db.execute("SELECT * FROM review_items WHERE unit_id=?", (unit_id,)).fetchone()
        if not row:
            raise KeyError(f"Review item not found: {unit_id}")
        db.execute("UPDATE review_items SET state=?,note=?,updated_at=? WHERE unit_id=?", (state, note, utcnow(), unit_id))
        _log_action(
            db,
            unit_id,
            state,
            before_target=row["target_snapshot"],
            after_target=row["target_snapshot"],
            note=note,
        )
        db.commit()
        return {"ok": True, "unit_id": unit_id, "state": state}
    finally:
        db.close()


def review_stats(project_db_path: Path) -> dict[str, Any]:
    db = init_project_db(Path(project_db_path))
    try:
        by_state = {row["state"]: row["n"] for row in db.execute(
            "SELECT state,COUNT(*) AS n FROM review_items GROUP BY state"
        )}
        by_severity = {row["severity"]: row["n"] for row in db.execute(
            "SELECT severity,COUNT(*) AS n FROM review_items WHERE state='pending' GROUP BY severity"
        )}
        by_reason = {row["reason_code"]: row["n"] for row in db.execute(
            "SELECT reason_code,COUNT(*) AS n FROM review_items WHERE state='pending' GROUP BY reason_code ORDER BY n DESC"
        )}
        unresolved_qa = db.execute("SELECT COUNT(*) FROM qa_findings WHERE resolved=0").fetchone()[0]
        rejected_targets = db.execute(
            "SELECT COUNT(*) FROM current_targets WHERE qa_status IN ('failed','rejected') OR target_status='rejected'"
        ).fetchone()[0]
        blocked_targets = db.execute("SELECT COUNT(*) FROM blocked_targets").fetchone()[0]
        return {
            "by_state": by_state,
            "pending_by_severity": by_severity,
            "pending_by_reason": by_reason,
            "unresolved_qa": unresolved_qa,
            "rejected_targets": rejected_targets,
            "blocked_targets": blocked_targets,
        }
    finally:
        db.close()
