from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .models import TranslationUnitCandidate
from .normalize import normalize_source, source_key, stable_id


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


PROJECT_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  project_id TEXT PRIMARY KEY,name TEXT NOT NULL,source_lang TEXT NOT NULL DEFAULT 'vi',
  target_lang TEXT NOT NULL DEFAULT 'zh-CN',created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_paks(
  pak_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  pak_name TEXT NOT NULL,original_path TEXT NOT NULL DEFAULT '',sha256 TEXT NOT NULL DEFAULT '',
  byte_size INTEGER NOT NULL DEFAULT 0,imported_at TEXT NOT NULL,UNIQUE(project_id,pak_name,sha256)
);
CREATE TABLE IF NOT EXISTS translation_units(
  unit_id TEXT PRIMARY KEY,source_key TEXT NOT NULL UNIQUE,source_text TEXT NOT NULL,
  normalized_source TEXT NOT NULL,source_lang TEXT NOT NULL,unit_kind TEXT NOT NULL,
  word_count INTEGER NOT NULL DEFAULT 0,risk_flags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS occurrences(
  occurrence_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  unit_id TEXT NOT NULL REFERENCES translation_units(unit_id) ON DELETE CASCADE,
  record_id TEXT NOT NULL DEFAULT '',pak_name TEXT NOT NULL,source_file TEXT NOT NULL,
  locator_json TEXT NOT NULL DEFAULT '{}',skeleton_json TEXT NOT NULL DEFAULT '[]',
  source_fingerprint TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_occ_project_active ON occurrences(project_id,active);
CREATE INDEX IF NOT EXISTS idx_occ_unit ON occurrences(unit_id);
CREATE INDEX IF NOT EXISTS idx_occ_pak_file ON occurrences(project_id,pak_name,source_file);
CREATE TABLE IF NOT EXISTS current_targets(
  unit_id TEXT PRIMARY KEY REFERENCES translation_units(unit_id) ON DELETE CASCADE,target_text TEXT NOT NULL,
  target_status TEXT NOT NULL,origin TEXT NOT NULL,knowledge_ref TEXT NOT NULL DEFAULT '',
  qa_status TEXT NOT NULL DEFAULT 'unchecked',locked INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translation_jobs(
  job_id TEXT PRIMARY KEY,engine TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,
  prompt_hash TEXT NOT NULL DEFAULT '',glossary_hash TEXT NOT NULL DEFAULT '',total_unique INTEGER NOT NULL DEFAULT 0,
  completed_unique INTEGER NOT NULL DEFAULT 0,failed_unique INTEGER NOT NULL DEFAULT 0,
  checkpoint_json TEXT NOT NULL DEFAULT '{}',started_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_items(
  job_id TEXT NOT NULL REFERENCES translation_jobs(job_id) ON DELETE CASCADE,
  unit_id TEXT NOT NULL REFERENCES translation_units(unit_id) ON DELETE CASCADE,status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
  PRIMARY KEY(job_id,unit_id)
);
CREATE INDEX IF NOT EXISTS idx_job_items_status ON job_items(job_id,status);
CREATE TABLE IF NOT EXISTS qa_findings(
  finding_id INTEGER PRIMARY KEY AUTOINCREMENT,unit_id TEXT NOT NULL REFERENCES translation_units(unit_id) ON DELETE CASCADE,
  code TEXT NOT NULL,severity TEXT NOT NULL,message TEXT NOT NULL,detail_json TEXT NOT NULL DEFAULT '{}',
  resolved INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_unit ON qa_findings(unit_id,resolved,severity);
CREATE TABLE IF NOT EXISTS review_items(
  unit_id TEXT PRIMARY KEY REFERENCES translation_units(unit_id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'pending',priority INTEGER NOT NULL DEFAULT 100,
  reason_code TEXT NOT NULL,severity TEXT NOT NULL DEFAULT 'warning',
  source_snapshot TEXT NOT NULL,target_snapshot TEXT NOT NULL DEFAULT '',
  target_fingerprint TEXT NOT NULL DEFAULT '',qa_codes_json TEXT NOT NULL DEFAULT '[]',
  occurrence_count INTEGER NOT NULL DEFAULT 0,note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_state_priority ON review_items(state,priority DESC,updated_at);
CREATE TABLE IF NOT EXISTS review_actions(
  action_id INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id TEXT NOT NULL REFERENCES translation_units(unit_id) ON DELETE CASCADE,
  action TEXT NOT NULL,before_target TEXT NOT NULL DEFAULT '',after_target TEXT NOT NULL DEFAULT '',
  tm_id INTEGER NOT NULL DEFAULT 0,note TEXT NOT NULL DEFAULT '',detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_actions_unit ON review_actions(unit_id,created_at DESC);
CREATE TABLE IF NOT EXISTS build_snapshots(
  build_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,source_manifest_json TEXT NOT NULL,
  target_manifest_json TEXT NOT NULL DEFAULT '{}',verification_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL
);
"""


KNOWLEDGE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS translation_memory(
  tm_id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL,source_text TEXT NOT NULL,target_text TEXT NOT NULL,
  source_lang TEXT NOT NULL DEFAULT 'vi',target_lang TEXT NOT NULL DEFAULT 'zh-CN',quality TEXT NOT NULL,
  locked INTEGER NOT NULL DEFAULT 0,provenance TEXT NOT NULL DEFAULT '',usage_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(source_key,target_lang)
);
CREATE INDEX IF NOT EXISTS idx_tm_quality ON translation_memory(quality,locked);
CREATE TABLE IF NOT EXISTS glossary_terms(
  term_id INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT NOT NULL,source_text TEXT NOT NULL,target_text TEXT NOT NULL,
  term_type TEXT NOT NULL DEFAULT 'general',scope TEXT NOT NULL DEFAULT 'global',status TEXT NOT NULL DEFAULT 'candidate',
  priority INTEGER NOT NULL DEFAULT 100,locked INTEGER NOT NULL DEFAULT 0,provenance TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(source_key,scope)
);
CREATE INDEX IF NOT EXISTS idx_glossary_status ON glossary_terms(status,locked,priority);
CREATE TABLE IF NOT EXISTS canonical_reference(
  ref_id INTEGER PRIMARY KEY AUTOINCREMENT,source_alias_key TEXT NOT NULL,source_alias TEXT NOT NULL,
  canonical_zh TEXT NOT NULL,entity_type TEXT NOT NULL DEFAULT 'unknown',status TEXT NOT NULL DEFAULT 'approved',
  provenance TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
  UNIQUE(source_alias_key,entity_type)
);
CREATE TABLE IF NOT EXISTS model_cache(
  cache_key TEXT PRIMARY KEY,source_key TEXT NOT NULL,source_text TEXT NOT NULL,target_text TEXT NOT NULL,
  model TEXT NOT NULL,prompt_hash TEXT NOT NULL,glossary_hash TEXT NOT NULL,context_hash TEXT NOT NULL DEFAULT '',
  qa_status TEXT NOT NULL DEFAULT 'unchecked',created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_cache_source ON model_cache(source_key);
"""


def init_project_db(path: Path) -> sqlite3.Connection:
    db = _connect(Path(path)); db.executescript(PROJECT_SCHEMA); now = utcnow()
    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('created_at',?)", (now,)); db.commit(); return db


def init_knowledge_db(path: Path) -> sqlite3.Connection:
    db = _connect(Path(path)); db.executescript(KNOWLEDGE_SCHEMA); now = utcnow()
    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('created_at',?)", (now,)); db.commit(); return db


def default_knowledge_db() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return root / "cocos-pak-localization-studio" / "vnext" / "knowledge.sqlite3"


def ensure_project(db: sqlite3.Connection, name: str, project_id: str = "") -> str:
    now = utcnow(); pid = project_id or stable_id("p_", name)
    db.execute("""INSERT INTO projects(project_id,name,created_at,updated_at) VALUES(?,?,?,?)
      ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at""", (pid,name,now,now))
    db.commit(); return pid


def upsert_unit(db: sqlite3.Connection, candidate: TranslationUnitCandidate) -> str:
    now = utcnow()
    db.execute("""INSERT INTO translation_units(unit_id,source_key,source_text,normalized_source,source_lang,unit_kind,
      word_count,risk_flags_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(source_key) DO UPDATE SET source_text=excluded.source_text,normalized_source=excluded.normalized_source,
      source_lang=excluded.source_lang,unit_kind=excluded.unit_kind,word_count=excluded.word_count,
      risk_flags_json=excluded.risk_flags_json,updated_at=excluded.updated_at""",
      (candidate.unit_id,candidate.source_key,candidate.source_text,normalize_source(candidate.source_text),
       candidate.language.value,candidate.kind.value,candidate.word_count,
       json.dumps(list(candidate.risk_flags),ensure_ascii=False),now,now))
    return candidate.unit_id


def add_occurrence(db: sqlite3.Connection, *, project_id: str, unit_id: str, record_id: str, pak_name: str,
                   source_file: str, source_fingerprint: str, locator: dict[str, Any] | None = None,
                   skeleton: list[dict[str, Any]] | None = None) -> str:
    now = utcnow(); oid = stable_id("o_",project_id,pak_name,source_file,record_id,source_fingerprint)
    db.execute("""INSERT INTO occurrences(occurrence_id,project_id,unit_id,record_id,pak_name,source_file,locator_json,
      skeleton_json,source_fingerprint,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)
      ON CONFLICT(occurrence_id) DO UPDATE SET unit_id=excluded.unit_id,locator_json=excluded.locator_json,
      skeleton_json=excluded.skeleton_json,source_fingerprint=excluded.source_fingerprint,active=1,updated_at=excluded.updated_at""",
      (oid,project_id,unit_id,record_id,pak_name,source_file,json.dumps(locator or {},ensure_ascii=False,separators=(",",":")),
       json.dumps(skeleton or [],ensure_ascii=False,separators=(",",":")),source_fingerprint,now,now))
    return oid


def put_tm(db: sqlite3.Connection, *, source_text: str, target_text: str, quality: str, locked: bool = False,
           provenance: str = "", source_lang: str = "vi", target_lang: str = "zh-CN") -> int:
    now = utcnow(); skey = source_key(source_text,source_lang,target_lang)
    db.execute("""INSERT INTO translation_memory(source_key,source_text,target_text,source_lang,target_lang,quality,locked,
      provenance,usage_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,?,?)
      ON CONFLICT(source_key,target_lang) DO UPDATE SET source_text=excluded.source_text,
      target_text=CASE WHEN translation_memory.locked=1 AND excluded.locked=0 THEN translation_memory.target_text ELSE excluded.target_text END,
      quality=CASE WHEN translation_memory.locked=1 AND excluded.locked=0 THEN translation_memory.quality ELSE excluded.quality END,
      locked=MAX(translation_memory.locked,excluded.locked),
      provenance=CASE WHEN translation_memory.locked=1 AND excluded.locked=0 THEN translation_memory.provenance ELSE excluded.provenance END,
      updated_at=excluded.updated_at""",
      (skey,normalize_source(source_text),str(target_text or "").strip(),source_lang,target_lang,quality,int(bool(locked)),provenance,now,now))
    row = db.execute("SELECT tm_id FROM translation_memory WHERE source_key=? AND target_lang=?",(skey,target_lang)).fetchone()
    return int(row["tm_id"])


def lookup_tm(db: sqlite3.Connection, source_text: str, *, target_lang: str = "zh-CN"):
    skey = source_key(source_text,"vi",target_lang)
    row = db.execute("""SELECT * FROM translation_memory WHERE source_key=? AND target_lang=?
      ORDER BY locked DESC,CASE quality WHEN 'manual' THEN 6 WHEN 'approved' THEN 5 WHEN 'reference' THEN 4
      WHEN 'reviewed' THEN 3 WHEN 'seed' THEN 2 ELSE 1 END DESC LIMIT 1""",(skey,target_lang)).fetchone()
    if row:
        db.execute('UPDATE translation_memory SET usage_count=usage_count+1,updated_at=? WHERE tm_id=?',(utcnow(),row['tm_id'])); db.commit()
    return row


def migrate_legacy_tm(legacy_db_path: Path, knowledge_db_path: Path) -> dict[str, int]:
    legacy_db_path = Path(legacy_db_path)
    if not legacy_db_path.is_file(): raise FileNotFoundError(legacy_db_path)
    legacy = sqlite3.connect(legacy_db_path); legacy.row_factory = sqlite3.Row
    target = init_knowledge_db(knowledge_db_path); stats = {"tm":0,"glossary":0,"skipped":0}
    tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "translation_memory" in tables:
        for row in legacy.execute("SELECT * FROM translation_memory"):
            quality = row["quality"] if "quality" in row.keys() else "legacy"
            try:
                put_tm(target,source_text=row["source_text"],target_text=row["target_text"],quality=quality or "legacy",
                       locked=(quality in ("manual","approved","reviewed")),provenance=f"legacy:{legacy_db_path.name}")
                stats["tm"] += 1
            except Exception: stats["skipped"] += 1
    if "glossary" in tables:
        now = utcnow()
        for row in legacy.execute("SELECT * FROM glossary"):
            source = str(row["source"] or "").strip(); target_text = str(row["target"] or "").strip()
            if not source or not target_text: stats["skipped"] += 1; continue
            skey = source_key(source)
            target.execute("""INSERT INTO glossary_terms(source_key,source_text,target_text,term_type,scope,status,priority,locked,
              provenance,note,created_at,updated_at) VALUES(?,?,?,'general','global','candidate',100,0,?,?,?,?)
              ON CONFLICT(source_key,scope) DO NOTHING""",
              (skey,source,target_text,f"legacy:{legacy_db_path.name}",str(row["note"] if "note" in row.keys() else ""),now,now))
            stats["glossary"] += 1
    target.commit(); legacy.close(); target.close(); return stats
