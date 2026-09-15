from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from xlsx_localization import read_simple_xlsx

from .build import build_verified_paks, preflight_build
from .classify import classify_source
from .compatibility import workspace_sync_status
from .database import default_knowledge_db
from .normalize import normalized_lookup_key


EXPECTED_PAKS = ("settings.pak", "updatefs.pak", "ui.pak")


def _ext(name: str) -> str:
    suffix = Path(str(name or "")).suffix.lower()
    return suffix[1:].upper() if suffix else "OTHER"


def audit_xlsx_corpus(path: Path, *, sample_limit: int = 40) -> dict[str, Any]:
    """Audit the real exported player-visible corpus without changing it."""
    path = Path(path).resolve()
    rows = read_simple_xlsx(path)
    required = {"id", "pak", "source_file", "text"}
    headers = set(rows[0].keys()) if rows else set()
    missing_columns = sorted(required - headers)

    paks: Counter[str] = Counter()
    extensions: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    risk_flags: Counter[str] = Counter()
    normalized: set[str] = set()
    ids: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    risk_samples: list[dict[str, Any]] = []
    empty_text = 0
    missing_identity = 0

    for row_no, row in enumerate(rows, 2):
        rid = str(row.get("id") or "").strip()
        pak = str(row.get("pak") or "").strip()
        source_file = str(row.get("source_file") or "").strip()
        text = str(row.get("text") or "")
        if not rid or not pak or not source_file:
            missing_identity += 1
        if not text.strip():
            empty_text += 1
            continue
        paks[pak] += 1
        extensions[_ext(source_file)] += 1
        normalized.add(normalized_lookup_key(text))
        if rid:
            ids[rid].add((pak, source_file, text))
        candidate = classify_source(text)
        languages[candidate.language.value] += 1
        kinds[candidate.kind.value] += 1
        for flag in candidate.risk_flags:
            risk_flags[str(flag)] += 1
        if candidate.risk_flags and len(risk_samples) < max(0, int(sample_limit)):
            risk_samples.append(
                {
                    "row": row_no,
                    "id": rid,
                    "pak": pak,
                    "source_file": source_file,
                    "text": text[:240],
                    "language": candidate.language.value,
                    "kind": candidate.kind.value,
                    "risk_flags": list(candidate.risk_flags),
                }
            )

    id_duplicates = {key: values for key, values in ids.items() if len(values) > 1}
    present = set(paks)
    missing_paks = [name for name in EXPECTED_PAKS if name not in present]
    unexpected_paks = sorted(present - set(EXPECTED_PAKS))
    nonempty_rows = sum(paks.values())
    return {
        "ok": bool(rows) and not missing_columns and not missing_paks and not missing_identity,
        "path": str(path),
        "rows": len(rows),
        "nonempty_text_rows": nonempty_rows,
        "empty_text_rows": empty_text,
        "missing_identity_rows": missing_identity,
        "normalized_unique": len(normalized),
        "normalization_reuse": max(0, nonempty_rows - len(normalized)),
        "normalization_reuse_percent": round((max(0, nonempty_rows - len(normalized)) / nonempty_rows * 100), 3) if nonempty_rows else 0.0,
        "pak_counts": dict(sorted(paks.items())),
        "extension_counts": dict(sorted(extensions.items())),
        "language_counts": dict(sorted(languages.items())),
        "kind_counts": dict(sorted(kinds.items())),
        "risk_flag_counts": dict(sorted(risk_flags.items())),
        "duplicate_id_count": len(id_duplicates),
        "duplicate_id_samples": [
            {"id": key, "variants": [list(item) for item in sorted(values)[:5]]}
            for key, values in list(sorted(id_duplicates.items()))[:20]
        ],
        "missing_columns": missing_columns,
        "missing_expected_paks": missing_paks,
        "unexpected_paks": unexpected_paks,
        "risk_samples": risk_samples,
    }


def workspace_readiness(
    workspace: Path,
    *,
    knowledge_db_path: Path | None = None,
) -> dict[str, Any]:
    """Check whether a real three-PAK workspace is ready for the Phase-7 build run.

    This function is intentionally read-only: checking readiness must never create a
    missing project database or silently mark an unsynchronized workspace as valid.
    """
    workspace = Path(workspace).resolve()
    project_json = workspace / "project.json"
    records = workspace / "localization" / "text_records.json"
    project_db = workspace / "vnext" / "project.sqlite3"
    knowledge_db = Path(knowledge_db_path or default_knowledge_db())
    problems: list[str] = []
    pak_rows: list[dict[str, Any]] = []

    if not project_json.is_file():
        problems.append("缺少 project.json")
        project = {}
    else:
        try:
            project = json.loads(project_json.read_text(encoding="utf-8"))
        except Exception as exc:
            project = {}
            problems.append(f"project.json 无法解析：{exc}")
    if not records.is_file():
        problems.append("缺少 localization/text_records.json")
    has_project_db = project_db.is_file()
    if not has_project_db:
        problems.append("缺少 vnext/project.sqlite3；请先同步当前项目")

    by_name = {
        str(item.get("pak") or Path(str(item.get("path") or "")).name): item
        for item in (project.get("paks") or [])
        if isinstance(item, dict)
    }
    for pak_name in EXPECTED_PAKS:
        item = by_name.get(pak_name)
        if not item:
            problems.append(f"项目缺少 {pak_name}")
            pak_rows.append({"pak": pak_name, "ok": False, "error": "missing project entry"})
            continue
        original = Path(str(item.get("path") or ""))
        extracted = Path(str(item.get("extracted") or ""))
        manifest = extracted / "manifest.json"
        issues = []
        if not original.is_file():
            issues.append("original PAK missing")
        if not extracted.is_dir():
            issues.append("extracted directory missing")
        if not manifest.is_file():
            issues.append("manifest.json missing")
        if issues:
            problems.extend(f"{pak_name}: {issue}" for issue in issues)
        pak_rows.append(
            {
                "pak": pak_name,
                "ok": not issues,
                "original": str(original),
                "extracted": str(extracted),
                "original_size": original.stat().st_size if original.is_file() else 0,
                "manifest": str(manifest),
                "issues": issues,
            }
        )

    if not records.is_file():
        sync = {"ok": False, "synced": False, "state": "missing_records", "message": "缺少 text_records.json"}
    elif not has_project_db:
        sync = {"ok": True, "synced": False, "state": "never_synced", "message": "vNext 尚未同步当前工作区"}
    else:
        sync = workspace_sync_status(workspace, project_db)
    if not sync.get("synced"):
        problems.append(str(sync.get("message") or "vNext 工作区尚未同步"))

    preflight = None
    if has_project_db:
        try:
            preflight = preflight_build(project_db, knowledge_db, require_translated=False, fail_on_warnings=False)
            if not preflight.get("ok"):
                problems.append(f"vNext 构建前 QA 有 {preflight.get('blocker_count', 0)} 个 blocker")
        except Exception as exc:
            problems.append(f"构建前 QA 无法执行：{exc}")

    # Keep repeated messages out of the machine-readable report.
    problems = list(dict.fromkeys(problems))
    return {
        "ok": not problems,
        "workspace": str(workspace),
        "expected_paks": list(EXPECTED_PAKS),
        "paks": pak_rows,
        "sync": sync,
        "preflight": preflight,
        "problems": problems,
    }


def run_real_build_validation(
    workspace: Path,
    *,
    knowledge_db_path: Path | None = None,
    output_dir: Path | None = None,
    workers: int = 1,
    require_translated: bool = False,
    fail_on_warnings: bool = False,
) -> dict[str, Any]:
    """Run the real selected-three-PAK materialize/build/re-extract gate."""
    workspace = Path(workspace).resolve()
    readiness = workspace_readiness(workspace, knowledge_db_path=knowledge_db_path)
    if not readiness.get("ok"):
        return {"ok": False, "stage": "readiness", "readiness": readiness}
    report = build_verified_paks(
        workspace,
        project_db_path=workspace / "vnext" / "project.sqlite3",
        knowledge_db_path=Path(knowledge_db_path or default_knowledge_db()),
        output_dir=output_dir,
        pak_names=list(EXPECTED_PAKS),
        workers=max(1, int(workers)),
        require_translated=bool(require_translated),
        fail_on_warnings=bool(fail_on_warnings),
    )
    return {"ok": bool(report.get("ok")), "stage": "verified-build", "readiness": readiness, "build": report}
