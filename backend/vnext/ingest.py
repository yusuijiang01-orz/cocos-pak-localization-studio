from __future__ import annotations

from collections import Counter
from pathlib import Path

from xlsx_localization import read_simple_xlsx

from .classify import classify_source
from .database import add_occurrence, ensure_project, init_project_db, upsert_unit
from .normalize import occurrence_fingerprint

REQUIRED_COLUMNS = ("id", "pak", "source_file", "text")


def ingest_full_xlsx(xlsx_path: Path, project_db_path: Path, *, project_name: str = "Imported localization") -> dict:
    rows = read_simple_xlsx(Path(xlsx_path))
    if not rows:
        raise ValueError("XLSX 没有数据行")
    missing = [name for name in REQUIRED_COLUMNS if name not in rows[0]]
    if missing:
        raise ValueError(f"XLSX 缺少列：{', '.join(missing)}")

    db = init_project_db(project_db_path)
    project_id = ensure_project(db, project_name)
    counts = Counter()
    unique_before = db.execute("SELECT COUNT(*) FROM translation_units").fetchone()[0]

    # A new full snapshot supersedes prior occurrence locations for this project.
    # Units/TM are deliberately retained; only stale source occurrences become inactive.
    db.execute("BEGIN")
    db.execute("UPDATE occurrences SET active=0,updated_at=datetime('now') WHERE project_id=?", (project_id,))
    for row_index, row in enumerate(rows, 2):
        text = str(row.get("text") or "")
        candidate = classify_source(text)
        upsert_unit(db, candidate)
        record_id = str(row.get("id") or "")
        pak = str(row.get("pak") or "")
        source_file = str(row.get("source_file") or "")
        add_occurrence(
            db,
            project_id=project_id,
            unit_id=candidate.unit_id,
            record_id=record_id,
            pak_name=pak,
            source_file=source_file,
            source_fingerprint=occurrence_fingerprint(pak, source_file, record_id, text),
            locator={"xlsx_row": row_index},
            skeleton=[{"kind": "text", "unit_id": candidate.unit_id}],
        )
        counts[f"language:{candidate.language.value}"] += 1
        counts[f"kind:{candidate.kind.value}"] += 1
        for flag in candidate.risk_flags:
            counts[f"risk:{flag}"] += 1
    db.commit()

    unit_count = db.execute("SELECT COUNT(*) FROM translation_units").fetchone()[0]
    occurrence_count = db.execute("SELECT COUNT(*) FROM occurrences WHERE project_id=? AND active=1", (project_id,)).fetchone()[0]
    stale_count = db.execute("SELECT COUNT(*) FROM occurrences WHERE project_id=? AND active=0", (project_id,)).fetchone()[0]
    db.close()
    return {
        "project_id": project_id,
        "rows": len(rows),
        "unique_units": unit_count,
        "new_unique_units": max(0, unit_count - unique_before),
        "active_occurrences": occurrence_count,
        "stale_occurrences": stale_count,
        "counts": dict(sorted(counts.items())),
    }


def project_stats(project_db_path: Path) -> dict:
    db = init_project_db(project_db_path)
    project = db.execute("SELECT project_id,name FROM projects ORDER BY created_at LIMIT 1").fetchone()
    if not project:
        db.close()
        return {"unique_units": 0, "active_occurrences": 0, "by_language": {}, "by_kind": {}, "risk_groups": {}}
    project_id = project["project_id"]
    units = db.execute(
        """SELECT COUNT(DISTINCT o.unit_id) FROM occurrences o
           WHERE o.project_id=? AND o.active=1""", (project_id,)
    ).fetchone()[0]
    occurrences = db.execute("SELECT COUNT(*) FROM occurrences WHERE project_id=? AND active=1", (project_id,)).fetchone()[0]
    by_language = {
        row["source_lang"]: row["n"] for row in db.execute(
            """SELECT u.source_lang,COUNT(DISTINCT u.unit_id) AS n
               FROM translation_units u JOIN occurrences o ON o.unit_id=u.unit_id
               WHERE o.project_id=? AND o.active=1 GROUP BY u.source_lang""", (project_id,)
        )
    }
    by_kind = {
        row["unit_kind"]: row["n"] for row in db.execute(
            """SELECT u.unit_kind,COUNT(DISTINCT u.unit_id) AS n
               FROM translation_units u JOIN occurrences o ON o.unit_id=u.unit_id
               WHERE o.project_id=? AND o.active=1 GROUP BY u.unit_kind""", (project_id,)
        )
    }
    risks = {
        row["risk_flags_json"]: row["n"] for row in db.execute(
            """SELECT u.risk_flags_json,COUNT(DISTINCT u.unit_id) AS n
               FROM translation_units u JOIN occurrences o ON o.unit_id=u.unit_id
               WHERE o.project_id=? AND o.active=1 AND u.risk_flags_json!='[]'
               GROUP BY u.risk_flags_json""", (project_id,)
        )
    }
    db.close()
    return {
        "project_id": project_id,
        "project_name": project["name"],
        "unique_units": units,
        "active_occurrences": occurrences,
        "by_language": by_language,
        "by_kind": by_kind,
        "risk_groups": risks,
    }