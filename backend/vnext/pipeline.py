from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .database import default_knowledge_db, init_knowledge_db, init_project_db, utcnow
from .jobs import (
    latest_job,
    mark_item,
    refresh_job_counts,
    request_stop,
    set_job_status,
    should_stop,
    start_or_resume_job,
)
from .knowledge import (
    context_hash,
    glossary_hash,
    lookup_model_cache,
    lookup_reference,
    lookup_trusted_tm,
    put_model_cache,
    relevant_glossary_terms,
)
from .qa import evaluate_translation, is_build_safe


EventCallback = Callable[[dict[str, Any]], None]
AUTO_KINDS = ("ui_short", "sentence", "proper_noun", "mixed_source", "unknown")


def _noop(_: dict[str, Any]) -> None:
    pass


def _candidate_units(db: sqlite3.Connection, max_units: int = 0) -> list[sqlite3.Row]:
    sql = f"""
      SELECT u.*,COUNT(o.occurrence_id) AS occurrence_count
      FROM translation_units u
      JOIN occurrences o ON o.unit_id=u.unit_id AND o.active=1
      WHERE u.unit_kind IN ({','.join('?' for _ in AUTO_KINDS)})
      GROUP BY u.unit_id
      ORDER BY
        CASE u.unit_kind
          WHEN 'ui_short' THEN 1 WHEN 'proper_noun' THEN 2 WHEN 'sentence' THEN 3
          WHEN 'mixed_source' THEN 4 ELSE 5 END,
        u.word_count ASC,u.unit_id ASC
    """
    params: list[Any] = list(AUTO_KINDS)
    if max_units and max_units > 0:
        sql += " LIMIT ?"
        params.append(int(max_units))
    return list(db.execute(sql, params))


def _context_for_unit(db: sqlite3.Connection, unit_id: str) -> dict[str, Any]:
    rows = list(
        db.execute(
            """SELECT pak_name,source_file,record_id,locator_json
               FROM occurrences WHERE unit_id=? AND active=1
               ORDER BY pak_name,source_file,record_id LIMIT 3""",
            (unit_id,),
        )
    )
    refs = []
    for row in rows:
        try:
            locator = json.loads(row["locator_json"] or "{}")
        except Exception:
            locator = {}
        refs.append(
            {
                "pak": row["pak_name"],
                "file": row["source_file"],
                "record_id": row["record_id"],
                "locator": locator,
            }
        )
    return {"occurrences": refs}


def _record_ids(db: sqlite3.Connection, unit_id: str) -> list[str]:
    return [
        str(row["record_id"])
        for row in db.execute(
            "SELECT record_id FROM occurrences WHERE unit_id=? AND active=1 ORDER BY occurrence_id",
            (unit_id,),
        )
        if row["record_id"]
    ]


def _set_target(
    db: sqlite3.Connection,
    unit_id: str,
    target_text: str,
    *,
    status: str,
    origin: str,
    knowledge_ref: str = "",
    qa_status: str = "passed",
    locked: bool = False,
) -> None:
    now = utcnow()
    db.execute(
        """INSERT INTO current_targets(
             unit_id,target_text,target_status,origin,knowledge_ref,qa_status,locked,updated_at
           ) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(unit_id) DO UPDATE SET
             target_text=CASE WHEN current_targets.locked=1 AND excluded.locked=0
                              THEN current_targets.target_text ELSE excluded.target_text END,
             target_status=CASE WHEN current_targets.locked=1 AND excluded.locked=0
                                THEN current_targets.target_status ELSE excluded.target_status END,
             origin=CASE WHEN current_targets.locked=1 AND excluded.locked=0
                         THEN current_targets.origin ELSE excluded.origin END,
             knowledge_ref=CASE WHEN current_targets.locked=1 AND excluded.locked=0
                                THEN current_targets.knowledge_ref ELSE excluded.knowledge_ref END,
             qa_status=CASE WHEN current_targets.locked=1 AND excluded.locked=0
                            THEN current_targets.qa_status ELSE excluded.qa_status END,
             locked=MAX(current_targets.locked,excluded.locked),updated_at=excluded.updated_at""",
        (
            unit_id,
            str(target_text or "").strip(),
            status,
            origin,
            knowledge_ref,
            qa_status,
            int(bool(locked)),
            now,
        ),
    )


def _save_findings(db: sqlite3.Connection, unit_id: str, findings) -> None:
    db.execute("DELETE FROM qa_findings WHERE unit_id=? AND resolved=0", (unit_id,))
    now = utcnow()
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


def _emit_target(
    event: EventCallback,
    db: sqlite3.Connection,
    unit: sqlite3.Row,
    target: str,
    *,
    origin: str,
    job_id: str,
) -> None:
    record_ids = _record_ids(db, unit["unit_id"])
    event(
        {
            "event": "translation",
            "phase": "vnext-translate",
            "job_id": job_id,
            "unit_id": unit["unit_id"],
            "source": unit["source_text"],
            "target": target,
            "origin": origin,
            "occurrence_count": len(record_ids),
            "record_ids": record_ids,
        }
    )


def _safe_apply(
    project_db: sqlite3.Connection,
    unit: sqlite3.Row,
    target: str,
    *,
    status: str,
    origin: str,
    knowledge_ref: str = "",
    locked: bool = False,
) -> bool:
    findings = evaluate_translation(unit["source_text"], target)
    _save_findings(project_db, unit["unit_id"], findings)
    safe = is_build_safe(findings)
    _set_target(
        project_db,
        unit["unit_id"],
        target,
        status=status if safe else "rejected",
        origin=origin,
        knowledge_ref=knowledge_ref,
        qa_status="passed" if safe else "failed",
        locked=locked if safe else False,
    )
    return safe


def run_pipeline(
    project_db_path: Path,
    *,
    knowledge_db_path: Path | None,
    translator,
    batch_size: int = 16,
    max_units: int = 0,
    event: EventCallback | None = None,
) -> dict[str, Any]:
    event = event or _noop
    project_db = init_project_db(Path(project_db_path))
    knowledge_db = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    units = _candidate_units(project_db, max_units=max_units)
    gh = glossary_hash(knowledge_db)
    job_id = start_or_resume_job(
        project_db,
        [row["unit_id"] for row in units],
        engine=translator.engine_name,
        model=translator.model,
        prompt_hash=translator.prompt_hash,
        glossary_hash=gh,
    )
    event(
        {
            "event": "start",
            "phase": "vnext-translate",
            "job_id": job_id,
            "unique_units": len(units),
            "model": translator.model,
            "glossary_hash": gh,
        }
    )

    try:
        model_queue: list[tuple[sqlite3.Row, dict[str, Any], str]] = []
        for unit in units:
            if should_stop(project_db, job_id):
                refresh_job_counts(project_db, job_id)
                set_job_status(project_db, job_id, "stopped")
                summary = _summary(project_db, job_id)
                event({"event": "stopped", "phase": "vnext-translate", **summary})
                return summary

            existing = project_db.execute(
                "SELECT * FROM current_targets WHERE unit_id=?",
                (unit["unit_id"],),
            ).fetchone()
            if existing and existing["qa_status"] == "passed":
                mark_item(project_db, job_id, unit["unit_id"], "skipped")
                continue

            tm = lookup_trusted_tm(knowledge_db, unit["source_text"])
            if tm:
                safe = _safe_apply(
                    project_db,
                    unit,
                    tm["target_text"],
                    status="tm",
                    origin=f"tm:{tm['quality']}",
                    knowledge_ref=str(tm["tm_id"]),
                    locked=bool(tm["locked"]),
                )
                mark_item(project_db, job_id, unit["unit_id"], "tm" if safe else "rejected")
                project_db.commit()
                if safe:
                    _emit_target(event, project_db, unit, tm["target_text"], origin="tm", job_id=job_id)
                continue

            ref = lookup_reference(knowledge_db, unit["source_text"])
            if ref:
                safe = _safe_apply(
                    project_db,
                    unit,
                    ref["canonical_zh"],
                    status="reference",
                    origin="reference",
                    knowledge_ref=str(ref["ref_id"]),
                    locked=True,
                )
                mark_item(project_db, job_id, unit["unit_id"], "reference" if safe else "rejected")
                project_db.commit()
                if safe:
                    _emit_target(event, project_db, unit, ref["canonical_zh"], origin="reference", job_id=job_id)
                continue

            ctx = _context_for_unit(project_db, unit["unit_id"])
            ctx_hash = context_hash(ctx)
            cached = lookup_model_cache(
                knowledge_db,
                unit["source_text"],
                model=translator.model,
                prompt_hash=translator.prompt_hash,
                glossary_hash_value=gh,
                context_hash_value=ctx_hash,
            )
            if cached:
                safe = _safe_apply(
                    project_db,
                    unit,
                    cached["target_text"],
                    status="model",
                    origin="model-cache",
                    knowledge_ref=cached["cache_key"],
                )
                mark_item(project_db, job_id, unit["unit_id"], "cache" if safe else "rejected")
                project_db.commit()
                if safe:
                    _emit_target(event, project_db, unit, cached["target_text"], origin="cache", job_id=job_id)
                continue

            item = project_db.execute(
                "SELECT status FROM job_items WHERE job_id=? AND unit_id=?",
                (job_id, unit["unit_id"]),
            ).fetchone()
            if item and item["status"] in ("tm", "reference", "cache", "model", "skipped"):
                continue
            model_queue.append((unit, ctx, ctx_hash))

        batch_size = max(1, int(batch_size))
        total_model = len(model_queue)
        for offset in range(0, total_model, batch_size):
            if should_stop(project_db, job_id):
                refresh_job_counts(project_db, job_id)
                set_job_status(project_db, job_id, "stopped")
                summary = _summary(project_db, job_id)
                event({"event": "stopped", "phase": "vnext-translate", **summary})
                return summary

            batch = model_queue[offset : offset + batch_size]
            merged_terms: dict[tuple[str, str], dict[str, Any]] = {}
            request_items: list[dict[str, Any]] = []
            for unit, ctx, _ctx_hash in batch:
                for term in relevant_glossary_terms(knowledge_db, unit["source_text"]):
                    merged_terms[(term["source"], term["target"])] = term
                request_items.append(
                    {"id": unit["unit_id"], "source": unit["source_text"], "context": ctx}
                )
                mark_item(project_db, job_id, unit["unit_id"], "pending", increment_attempt=True)
            project_db.commit()

            try:
                results = translator.translate_batch(request_items, list(merged_terms.values()))
            except Exception as exc:
                for unit, _ctx, _ctx_hash in batch:
                    mark_item(project_db, job_id, unit["unit_id"], "failed", error=str(exc))
                project_db.commit()
                refresh_job_counts(project_db, job_id)
                event(
                    {
                        "event": "batch-error",
                        "phase": "vnext-translate",
                        "job_id": job_id,
                        "message": str(exc),
                        "offset": offset,
                        "count": len(batch),
                    }
                )
                continue

            for unit, _ctx, ctx_hash in batch:
                target = str(results.get(unit["unit_id"]) or "").strip()
                if not target:
                    mark_item(project_db, job_id, unit["unit_id"], "failed", error="模型没有返回译文")
                    continue
                safe = _safe_apply(
                    project_db,
                    unit,
                    target,
                    status="model",
                    origin=f"model:{translator.model}",
                )
                if safe:
                    cache_key = put_model_cache(
                        knowledge_db,
                        unit["source_text"],
                        target,
                        model=translator.model,
                        prompt_hash=translator.prompt_hash,
                        glossary_hash_value=gh,
                        context_hash_value=ctx_hash,
                    )
                    _set_target(
                        project_db,
                        unit["unit_id"],
                        target,
                        status="model",
                        origin=f"model:{translator.model}",
                        knowledge_ref=cache_key,
                        qa_status="passed",
                    )
                    mark_item(project_db, job_id, unit["unit_id"], "model")
                    _emit_target(event, project_db, unit, target, origin="model", job_id=job_id)
                else:
                    mark_item(project_db, job_id, unit["unit_id"], "rejected", error="QA rejected model output")
            project_db.commit()
            checkpoint = refresh_job_counts(project_db, job_id)
            event(
                {
                    "event": "progress",
                    "phase": "vnext-translate",
                    "job_id": job_id,
                    "model_completed": min(offset + len(batch), total_model),
                    "model_total": total_model,
                    **checkpoint,
                }
            )

        refresh_job_counts(project_db, job_id)
        remaining = project_db.execute(
            "SELECT COUNT(*) FROM job_items WHERE job_id=? AND status IN ('pending','failed')",
            (job_id,),
        ).fetchone()[0]
        set_job_status(project_db, job_id, "completed" if remaining == 0 else "failed")
        summary = _summary(project_db, job_id)
        event({"event": "done", "phase": "vnext-translate", **summary})
        return summary
    except BaseException as exc:
        set_job_status(project_db, job_id, "failed", error=str(exc))
        raise
    finally:
        knowledge_db.close()
        project_db.close()


def _summary(db: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    checkpoint = refresh_job_counts(db, job_id)
    row = db.execute("SELECT status,last_error FROM translation_jobs WHERE job_id=?", (job_id,)).fetchone()
    counts = {
        r["status"]: r["n"]
        for r in db.execute(
            "SELECT status,COUNT(*) AS n FROM job_items WHERE job_id=? GROUP BY status",
            (job_id,),
        )
    }
    return {
        "job_id": job_id,
        "status": row["status"] if row else "unknown",
        "last_error": row["last_error"] if row else "",
        "item_status": counts,
        **checkpoint,
    }


def stop_latest_job(project_db_path: Path, job_id: str = "") -> dict[str, Any]:
    db = init_project_db(Path(project_db_path))
    try:
        if job_id:
            row = db.execute("SELECT job_id FROM translation_jobs WHERE job_id=?", (job_id,)).fetchone()
        else:
            row = latest_job(db)
        if not row:
            return {"ok": False, "error": "没有可停止的 vNext 翻译任务"}
        target = row["job_id"]
        return {"ok": request_stop(db, target), "job_id": target}
    finally:
        db.close()
