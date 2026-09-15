from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .database import utcnow
from .normalize import normalize_source, normalized_lookup_key, source_key


TRUSTED_TM_QUALITIES = ("manual", "approved", "reviewed", "reference", "seed")


def lookup_trusted_tm(db: sqlite3.Connection, source_text: str, target_lang: str = "zh-CN"):
    skey = source_key(source_text, "vi", target_lang)
    marks = ",".join("?" for _ in TRUSTED_TM_QUALITIES)
    row = db.execute(
        f"""SELECT * FROM translation_memory
            WHERE source_key=? AND target_lang=? AND quality IN ({marks})
            ORDER BY locked DESC,
              CASE quality
                WHEN 'manual' THEN 6 WHEN 'approved' THEN 5 WHEN 'reference' THEN 4
                WHEN 'reviewed' THEN 3 WHEN 'seed' THEN 2 ELSE 1 END DESC
            LIMIT 1""",
        (skey, target_lang, *TRUSTED_TM_QUALITIES),
    ).fetchone()
    if row:
        db.execute(
            "UPDATE translation_memory SET usage_count=usage_count+1,updated_at=? WHERE tm_id=?",
            (utcnow(), row["tm_id"]),
        )
        db.commit()
    return row


def lookup_reference(db: sqlite3.Connection, source_text: str):
    skey = source_key(source_text)
    return db.execute(
        """SELECT * FROM canonical_reference
           WHERE source_alias_key=? AND status IN ('approved','locked')
           ORDER BY CASE status WHEN 'locked' THEN 2 ELSE 1 END DESC, ref_id ASC
           LIMIT 1""",
        (skey,),
    ).fetchone()


def active_glossary_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        db.execute(
            """SELECT * FROM glossary_terms
               WHERE (locked=1 OR status IN ('approved','locked'))
                 AND length(trim(source_text))>0 AND length(trim(target_text))>0
               ORDER BY locked DESC, priority DESC, length(source_text) DESC, term_id ASC"""
        )
    )


def glossary_hash(db: sqlite3.Connection) -> str:
    payload = [
        {
            "source": normalize_source(row["source_text"]),
            "target": str(row["target_text"]).strip(),
            "type": row["term_type"],
            "scope": row["scope"],
            "status": row["status"],
            "priority": int(row["priority"]),
            "locked": int(row["locked"]),
        }
        for row in active_glossary_rows(db)
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def relevant_glossary_terms(
    db: sqlite3.Connection,
    source_text: str,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    haystack = normalized_lookup_key(source_text)
    matches: list[dict[str, Any]] = []
    for row in active_glossary_rows(db):
        needle = normalized_lookup_key(row["source_text"])
        if not needle or needle not in haystack:
            continue
        matches.append(
            {
                "source": str(row["source_text"]).strip(),
                "target": str(row["target_text"]).strip(),
                "term_type": row["term_type"],
                "scope": row["scope"],
                "priority": int(row["priority"]),
                "locked": bool(row["locked"]),
            }
        )
        if len(matches) >= max(1, int(limit)):
            break
    return matches


def terms_hash(terms: list[dict[str, Any]]) -> str:
    payload = [
        {
            "source": normalize_source(term.get("source", "")),
            "target": str(term.get("target") or "").strip(),
            "type": str(term.get("term_type") or ""),
            "scope": str(term.get("scope") or ""),
            "priority": int(term.get("priority") or 0),
            "locked": bool(term.get("locked")),
        }
        for term in terms
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def context_hash(context: Any) -> str:
    raw = json.dumps(context or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def model_cache_key(
    source_text: str,
    *,
    model: str,
    prompt_hash: str,
    glossary_hash_value: str,
    context_hash_value: str,
) -> str:
    payload = "\0".join(
        (
            source_key(source_text),
            str(model or ""),
            str(prompt_hash or ""),
            str(glossary_hash_value or ""),
            str(context_hash_value or ""),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lookup_model_cache(
    db: sqlite3.Connection,
    source_text: str,
    *,
    model: str,
    prompt_hash: str,
    glossary_hash_value: str,
    context_hash_value: str,
):
    key = model_cache_key(
        source_text,
        model=model,
        prompt_hash=prompt_hash,
        glossary_hash_value=glossary_hash_value,
        context_hash_value=context_hash_value,
    )
    return db.execute(
        "SELECT * FROM model_cache WHERE cache_key=? AND qa_status='passed'",
        (key,),
    ).fetchone()


def put_model_cache(
    db: sqlite3.Connection,
    source_text: str,
    target_text: str,
    *,
    model: str,
    prompt_hash: str,
    glossary_hash_value: str,
    context_hash_value: str,
    qa_status: str = "passed",
) -> str:
    key = model_cache_key(
        source_text,
        model=model,
        prompt_hash=prompt_hash,
        glossary_hash_value=glossary_hash_value,
        context_hash_value=context_hash_value,
    )
    now = utcnow()
    db.execute(
        """INSERT INTO model_cache(
             cache_key,source_key,source_text,target_text,model,prompt_hash,glossary_hash,
             context_hash,qa_status,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(cache_key) DO UPDATE SET
             target_text=excluded.target_text,qa_status=excluded.qa_status,updated_at=excluded.updated_at""",
        (
            key,
            source_key(source_text),
            normalize_source(source_text),
            str(target_text or "").strip(),
            model,
            prompt_hash,
            glossary_hash_value,
            context_hash_value,
            qa_status,
            now,
            now,
        ),
    )
    db.commit()
    return key
