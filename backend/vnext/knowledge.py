from __future__ import annotations

from collections import deque
import hashlib
import json
import sqlite3
from typing import Any, Iterable

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


def _term_payload(row: Any) -> dict[str, Any]:
    getter = row.__getitem__ if hasattr(row, "__getitem__") else None

    def get(name, default=""):
        try:
            return getter(name) if getter else row.get(name, default)
        except Exception:
            return row.get(name, default) if hasattr(row, "get") else default

    return {
        "source": str(get("source_text") or get("source") or "").strip(),
        "target": str(get("target_text") or get("target") or "").strip(),
        "term_type": str(get("term_type") or "general"),
        "scope": str(get("scope") or "global"),
        "priority": int(get("priority") or 0),
        "locked": bool(get("locked") or False),
    }


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


class GlossaryIndex:
    """Case-insensitive Aho-Corasick index for approved/locked source terms."""

    def __init__(self, rows: Iterable[Any]):
        self.terms: list[dict[str, Any]] = []
        self.keys: list[str] = []
        for row in rows:
            term = _term_payload(row)
            key = normalized_lookup_key(term["source"])
            if not key or not term["target"]:
                continue
            self.terms.append(term)
            self.keys.append(key)

        self.next: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[int]] = [[]]
        for idx, key in enumerate(self.keys):
            state = 0
            for ch in key:
                nxt = self.next[state].get(ch)
                if nxt is None:
                    nxt = len(self.next)
                    self.next[state][ch] = nxt
                    self.next.append({})
                    self.fail.append(0)
                    self.out.append([])
                state = nxt
            self.out[state].append(idx)

        queue: deque[int] = deque()
        for nxt in self.next[0].values():
            queue.append(nxt)
        while queue:
            state = queue.popleft()
            for ch, nxt in self.next[state].items():
                queue.append(nxt)
                fallback = self.fail[state]
                while fallback and ch not in self.next[fallback]:
                    fallback = self.fail[fallback]
                self.fail[nxt] = self.next[fallback].get(ch, 0)
                self.out[nxt].extend(self.out[self.fail[nxt]])

        self.fingerprint = terms_hash(self.terms)

    @classmethod
    def from_db(cls, db: sqlite3.Connection) -> "GlossaryIndex":
        return cls(active_glossary_rows(db))

    @staticmethod
    def _wordish(ch: str) -> bool:
        return bool(ch and (ch.isalnum() or ch == "_"))

    def find(self, source_text: str, *, limit: int = 24) -> list[dict[str, Any]]:
        haystack = normalized_lookup_key(source_text)
        if not haystack or not self.terms:
            return []
        state = 0
        hits: set[int] = set()
        for end, ch in enumerate(haystack):
            while state and ch not in self.next[state]:
                state = self.fail[state]
            state = self.next[state].get(ch, 0)
            for idx in self.out[state]:
                key = self.keys[idx]
                start = end - len(key) + 1
                if start < 0:
                    continue
                before = haystack[start - 1] if start > 0 else ""
                after = haystack[end + 1] if end + 1 < len(haystack) else ""
                if self._wordish(key[0]) and self._wordish(before):
                    continue
                if self._wordish(key[-1]) and self._wordish(after):
                    continue
                hits.add(idx)

        return [self.terms[idx] for idx in sorted(hits)[: max(1, int(limit))]]


def glossary_hash(db: sqlite3.Connection) -> str:
    return GlossaryIndex.from_db(db).fingerprint


def relevant_glossary_terms(
    db: sqlite3.Connection,
    source_text: str,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    # Compatibility helper. Hot translation paths should build one GlossaryIndex
    # and reuse it for all units.
    return GlossaryIndex.from_db(db).find(source_text, limit=limit)


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
