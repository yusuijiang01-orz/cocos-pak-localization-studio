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
    db.execute("BEGIN")
    for row_index, row in enumerate(rows, 2):
        text = str(row.get("text") or "")
        candidate = classify_source(text)
        upsert_unit(db, candidate)
        record_id = str(row.get("id") or "")
        pak = str(row.get("pak") or "")
        source_file = str(row.get("source_file") or "")
        add_occurrence(
            db,
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
    occurrence_count = db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]
    db.close()
    return {
        "project_id": project_id,
        "rows": len(rows),
        "unique_units": unit_count,
        "new_unique_units": max(0, unit_count - unique_before),
        "occurrences": occurrence_count,
        "counts": dict(sorted(counts.items())),
    }


def project_stats(project_db_path: Path) -> dict:
    db = init_project_db(project_db_path)
    units = db.execute("SELECT COUNT(*) FROM translation_units").fetchone()[0]
    occurrences = db.execute("SELECT COUNT(*) FROM occurrences WHERE active=1").fetchone()[0]
    by_language = {row["source_lang"]: row["n"] for row in db.execute("SELECT source_lang,COUNT(*) AS n FROM translation_units GROUP BY source_lang")}
    by_kind = {row["unit_kind"]: row["n"] for row in db.execute("SELECT unit_kind,COUNT(*) AS n FROM translation_units GROUP BY unit_kind")}
    risks = {row["risk_flags_json"]: row["n"] for row in db.execute("SELECT risk_flags_json,COUNT(*) AS n FROM translation_units WHERE risk_flags_json!='[]' GROUP BY risk_flags_json")}
    db.close()
    return {"unique_units": units, "active_occurrences": occurrences, "by_language": by_language, "by_kind": by_kind, "risk_groups": risks}
