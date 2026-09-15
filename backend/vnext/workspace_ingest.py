from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from localization_analyzer import is_structural_translation_payload

from .classify import classify_source
from .database import add_occurrence, ensure_project, init_project_db, upsert_unit
from .normalize import normalize_source, sha256_text
from .protection import split_runtime_text


def _record_source(record: dict[str, Any]) -> str:
    return str(record.get("source_original") or record.get("original") or "")


def _project_name(workspace: Path) -> str:
    project_json = Path(workspace) / "project.json"
    if project_json.is_file():
        try:
            project = json.loads(project_json.read_text(encoding="utf-8"))
            for key in ("name", "projectName", "title"):
                value = str(project.get(key) or "").strip()
                if value:
                    return value
        except Exception:
            pass
    return Path(workspace).name or "Studio vNext project"


def _skeleton_payload(source: str) -> tuple[list[dict[str, str]], list[tuple[int, str]]]:
    """Return an immutable runtime skeleton and translatable span positions.

    Whitespace-only pieces are treated as protected bytes/text. They are layout, not
    translation units, and must survive reconstruction exactly.
    """
    skeleton: list[dict[str, str]] = []
    spans: list[tuple[int, str]] = []
    for piece in split_runtime_text(source):
        if piece.kind == "protected" or not normalize_source(piece.value):
            skeleton.append({"kind": "protected", "value": piece.value})
            continue
        skeleton.append({"kind": "text", "source": piece.value})
        spans.append((len(skeleton) - 1, piece.value))
    return skeleton, spans


def ingest_workspace_records(
    workspace: Path,
    project_db_path: Path | None = None,
    *,
    project_name: str = "",
) -> dict[str, Any]:
    """Ingest the live Studio workspace as vNext translation units.

    The legacy record cache remains read-only. Protected syntax is kept out of model
    units and stored in each occurrence skeleton for deterministic reconstruction.
    """
    workspace = Path(workspace).resolve()
    records_path = workspace / "localization" / "text_records.json"
    if not records_path.is_file():
        raise FileNotFoundError(f"缺少工作区记录：{records_path}")
    records = json.loads(records_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("text_records.json 不是记录数组")

    db_path = Path(project_db_path or (workspace / "vnext" / "project.sqlite3"))
    db = init_project_db(db_path)
    pid = ensure_project(db, project_name or _project_name(workspace))
    counts: Counter[str] = Counter()
    db.execute("UPDATE occurrences SET active=0,updated_at=datetime('now') WHERE project_id=?", (pid,))

    try:
        for record_index, record in enumerate(records):
            if not isinstance(record, dict):
                counts["skipped:not_object"] += 1
                continue
            if record.get("_isPlayerVisible", True) is not True:
                counts["skipped:not_player_visible"] += 1
                continue
            pak = str(record.get("pak") or "").strip()
            source_file = str(record.get("source_file") or "").strip()
            record_id = str(record.get("id") or "").strip()
            source = _record_source(record)
            if not pak or not source_file or not record_id or not normalize_source(source):
                counts["skipped:missing_identity"] += 1
                continue
            if is_structural_translation_payload(source):
                counts["skipped:structural"] += 1
                continue

            skeleton, spans = _skeleton_payload(source)
            if not spans:
                counts["skipped:no_text_span"] += 1
                continue

            for text_order, (skeleton_index, span_text) in enumerate(spans):
                candidate = classify_source(span_text)
                if candidate.language.value in ("empty", "technical"):
                    counts[f"skipped:{candidate.language.value}"] += 1
                    continue
                upsert_unit(db, candidate)
                skeleton_with_units = [dict(item) for item in skeleton]
                # Resolve every same-source text piece to the same canonical unit id;
                # other text pieces remain source-only until their own occurrence pass.
                for item in skeleton_with_units:
                    if item.get("kind") == "text" and item.get("source") == span_text:
                        item["unit_id"] = candidate.unit_id
                fp = sha256_text(
                    "\0".join((pak, source_file, record_id, str(skeleton_index), span_text))
                )
                add_occurrence(
                    db,
                    project_id=pid,
                    unit_id=candidate.unit_id,
                    record_id=record_id,
                    pak_name=pak,
                    source_file=source_file,
                    source_fingerprint=fp,
                    locator={
                        "record_index": record_index,
                        "line": record.get("line"),
                        "column": record.get("column"),
                        "key": record.get("key"),
                        "span_index": skeleton_index,
                        "text_order": text_order,
                    },
                    skeleton=skeleton_with_units,
                )
                counts["occurrences"] += 1
                counts[f"language:{candidate.language.value}"] += 1
                counts[f"kind:{candidate.kind.value}"] += 1

        db.commit()
        active = db.execute(
            "SELECT COUNT(*) FROM occurrences WHERE project_id=? AND active=1", (pid,)
        ).fetchone()[0]
        units = db.execute(
            """SELECT COUNT(DISTINCT unit_id) FROM occurrences
               WHERE project_id=? AND active=1""", (pid,)
        ).fetchone()[0]
        stale = db.execute(
            "SELECT COUNT(*) FROM occurrences WHERE project_id=? AND active=0", (pid,)
        ).fetchone()[0]
        return {
            "project_id": pid,
            "records": len(records),
            "active_occurrences": int(active),
            "active_unique_units": int(units),
            "stale_occurrences": int(stale),
            "counts": dict(sorted(counts.items())),
            "project_db": str(db_path),
        }
    finally:
        db.close()
