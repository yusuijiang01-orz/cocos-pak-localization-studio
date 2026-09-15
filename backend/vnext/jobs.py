from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from .database import utcnow
from .normalize import stable_id


TERMINAL_SUCCESS = ("tm", "reference", "cache", "model", "skipped")
TERMINAL_FAILURE = ("rejected", "failed")


def ensure_job_schema(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(translation_jobs)")}
    if "stop_requested" not in columns:
        db.execute("ALTER TABLE translation_jobs ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0")
    if "last_error" not in columns:
        db.execute("ALTER TABLE translation_jobs ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
    if "completed_at" not in columns:
        db.execute("ALTER TABLE translation_jobs ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''")
    db.commit()


def job_id_for(engine: str, model: str, prompt_hash: str, glossary_hash: str) -> str:
    return stable_id("j_", engine, model, prompt_hash, glossary_hash)


def start_or_resume_job(
    db: sqlite3.Connection,
    unit_ids: Iterable[str],
    *,
    engine: str,
    model: str,
    prompt_hash: str,
    glossary_hash: str,
) -> str:
    ensure_job_schema(db)
    ids = list(dict.fromkeys(str(x) for x in unit_ids if x))
    job_id = job_id_for(engine, model, prompt_hash, glossary_hash)
    now = utcnow()
    db.execute(
        """INSERT INTO translation_jobs(
             job_id,engine,model,status,prompt_hash,glossary_hash,total_unique,
             completed_unique,failed_unique,checkpoint_json,started_at,updated_at,
             stop_requested,last_error,completed_at
           ) VALUES(?,?,?,?,?,?,?,0,0,'{}',?,?,0,'','')
           ON CONFLICT(job_id) DO UPDATE SET
             status='running',model=excluded.model,prompt_hash=excluded.prompt_hash,
             glossary_hash=excluded.glossary_hash,total_unique=excluded.total_unique,
             stop_requested=0,last_error='',updated_at=excluded.updated_at,completed_at=''""",
        (job_id, engine, model, "running", prompt_hash, glossary_hash, len(ids), now, now),
    )
    for unit_id in ids:
        db.execute(
            """INSERT INTO job_items(job_id,unit_id,status,attempts,last_error,updated_at)
               VALUES(?,?,'pending',0,'',?)
               ON CONFLICT(job_id,unit_id) DO NOTHING""",
            (job_id, unit_id, now),
        )
    db.commit()
    refresh_job_counts(db, job_id)
    return job_id


def request_stop(db: sqlite3.Connection, job_id: str) -> bool:
    ensure_job_schema(db)
    cur = db.execute(
        "UPDATE translation_jobs SET stop_requested=1,updated_at=? WHERE job_id=?",
        (utcnow(), job_id),
    )
    db.commit()
    return cur.rowcount > 0


def should_stop(db: sqlite3.Connection, job_id: str) -> bool:
    ensure_job_schema(db)
    row = db.execute("SELECT stop_requested FROM translation_jobs WHERE job_id=?", (job_id,)).fetchone()
    return bool(row and row["stop_requested"])


def mark_item(
    db: sqlite3.Connection,
    job_id: str,
    unit_id: str,
    status: str,
    *,
    error: str = "",
    increment_attempt: bool = False,
) -> None:
    now = utcnow()
    db.execute(
        """UPDATE job_items
           SET status=?,attempts=attempts+?,last_error=?,updated_at=?
           WHERE job_id=? AND unit_id=?""",
        (status, int(bool(increment_attempt)), str(error or "")[:4000], now, job_id, unit_id),
    )


def set_job_status(db: sqlite3.Connection, job_id: str, status: str, *, error: str = "") -> None:
    ensure_job_schema(db)
    completed_at = utcnow() if status == "completed" else ""
    db.execute(
        """UPDATE translation_jobs
           SET status=?,last_error=?,completed_at=?,updated_at=?
           WHERE job_id=?""",
        (status, str(error or "")[:4000], completed_at, utcnow(), job_id),
    )
    db.commit()


def refresh_job_counts(db: sqlite3.Connection, job_id: str) -> dict:
    success_marks = ",".join("?" for _ in TERMINAL_SUCCESS)
    failure_marks = ",".join("?" for _ in TERMINAL_FAILURE)
    completed = db.execute(
        f"SELECT COUNT(*) FROM job_items WHERE job_id=? AND status IN ({success_marks})",
        (job_id, *TERMINAL_SUCCESS),
    ).fetchone()[0]
    failed = db.execute(
        f"SELECT COUNT(*) FROM job_items WHERE job_id=? AND status IN ({failure_marks})",
        (job_id, *TERMINAL_FAILURE),
    ).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM job_items WHERE job_id=?", (job_id,)).fetchone()[0]
    checkpoint = {"completed": int(completed), "failed": int(failed), "total": int(total)}
    db.execute(
        """UPDATE translation_jobs
           SET total_unique=?,completed_unique=?,failed_unique=?,checkpoint_json=?,updated_at=?
           WHERE job_id=?""",
        (total, completed, failed, json.dumps(checkpoint, separators=(",", ":")), utcnow(), job_id),
    )
    db.commit()
    return checkpoint


def job_summary(db: sqlite3.Connection, job_id: str) -> dict:
    ensure_job_schema(db)
    row = db.execute("SELECT * FROM translation_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    counts = {
        item["status"]: item["n"]
        for item in db.execute(
            "SELECT status,COUNT(*) AS n FROM job_items WHERE job_id=? GROUP BY status",
            (job_id,),
        )
    }
    result = dict(row)
    result["item_status"] = counts
    return result


def latest_job(db: sqlite3.Connection):
    ensure_job_schema(db)
    return db.execute("SELECT * FROM translation_jobs ORDER BY updated_at DESC LIMIT 1").fetchone()
