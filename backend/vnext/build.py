from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from pak_builder import build_from_modified_dir, materialize_records_to_modified_dir, sha256

from .database import default_knowledge_db, init_knowledge_db, init_project_db, utcnow
from .knowledge import relevant_glossary_terms
from .normalize import normalize_source, sha256_text, stable_id
from .qa import evaluate_translation, is_build_safe, severity_rank


TRANSLATABLE_KINDS = {"ui_short", "sentence", "proper_noun", "mixed_source", "unknown"}


def _target_fingerprint(text: str) -> str:
    value = normalize_source(text)
    return sha256_text(value) if value else ""


def _active_units(db):
    return list(
        db.execute(
            """SELECT u.*,COUNT(o.occurrence_id) AS occurrence_count
               FROM translation_units u
               JOIN occurrences o ON o.unit_id=u.unit_id AND o.active=1
               GROUP BY u.unit_id ORDER BY u.unit_id"""
        )
    )


def preflight_build(
    project_db_path: Path,
    knowledge_db_path: Path | None = None,
    *,
    require_translated: bool = False,
    fail_on_warnings: bool = False,
) -> dict[str, Any]:
    """Run the final vNext QA gate before any resource bytes are touched."""
    project = init_project_db(Path(project_db_path))
    knowledge = init_knowledge_db(Path(knowledge_db_path or default_knowledge_db()))
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    translated = 0
    untranslated = 0
    try:
        for unit in _active_units(project):
            target = project.execute(
                "SELECT * FROM current_targets WHERE unit_id=?", (unit["unit_id"],)
            ).fetchone()
            if not target or not str(target["target_text"] or "").strip():
                if unit["unit_kind"] in TRANSLATABLE_KINDS:
                    untranslated += 1
                    if require_translated:
                        blockers.append(
                            {
                                "unit_id": unit["unit_id"],
                                "code": "UNTRANSLATED",
                                "source": unit["source_text"],
                                "occurrence_count": unit["occurrence_count"],
                            }
                        )
                continue

            target_text = str(target["target_text"] or "")
            translated += 1
            if target["qa_status"] != "passed" or target["target_status"] == "rejected":
                blockers.append(
                    {
                        "unit_id": unit["unit_id"],
                        "code": "TARGET_NOT_APPROVED_BY_QA",
                        "source": unit["source_text"],
                        "target": target_text,
                        "qa_status": target["qa_status"],
                        "target_status": target["target_status"],
                    }
                )
                continue

            blocked = project.execute(
                "SELECT reason FROM blocked_targets WHERE unit_id=? AND target_fingerprint=?",
                (unit["unit_id"], _target_fingerprint(target_text)),
            ).fetchone()
            if blocked:
                blockers.append(
                    {
                        "unit_id": unit["unit_id"],
                        "code": "HUMAN_BLOCKED_TARGET",
                        "source": unit["source_text"],
                        "target": target_text,
                        "reason": blocked["reason"],
                    }
                )
                continue

            findings = evaluate_translation(
                unit["source_text"],
                target_text,
                required_terms=relevant_glossary_terms(knowledge, unit["source_text"]),
            )
            if not is_build_safe(findings):
                blockers.append(
                    {
                        "unit_id": unit["unit_id"],
                        "code": "FINAL_QA_FAILED",
                        "source": unit["source_text"],
                        "target": target_text,
                        "findings": [f.code for f in findings if severity_rank(f.severity) >= 2],
                    }
                )
            for finding in findings:
                if severity_rank(finding.severity) == 1:
                    warnings.append(
                        {
                            "unit_id": unit["unit_id"],
                            "code": finding.code,
                            "source": unit["source_text"],
                            "target": target_text,
                            "message": finding.message,
                        }
                    )

        # Pending human review with error/fatal severity is always a build blocker.
        for row in project.execute(
            """SELECT unit_id,state,severity,reason_code,target_snapshot
               FROM review_items
               WHERE state IN ('pending','deferred','rejected')
                 AND severity IN ('error','fatal')
               ORDER BY priority DESC"""
        ):
            blockers.append(
                {
                    "unit_id": row["unit_id"],
                    "code": "UNRESOLVED_REVIEW",
                    "state": row["state"],
                    "severity": row["severity"],
                    "reason": row["reason_code"],
                    "target": row["target_snapshot"],
                }
            )
        for row in project.execute(
            """SELECT unit_id,state,severity,reason_code,target_snapshot
               FROM review_items
               WHERE state IN ('pending','deferred') AND severity='warning'
               ORDER BY priority DESC"""
        ):
            warnings.append(
                {
                    "unit_id": row["unit_id"],
                    "code": "PENDING_REVIEW_WARNING",
                    "state": row["state"],
                    "reason": row["reason_code"],
                    "target": row["target_snapshot"],
                }
            )

        # Deduplicate blocker fingerprints because one bad target may also be in review queue.
        deduped: list[dict[str, Any]] = []
        seen = set()
        for item in blockers:
            key = (item.get("unit_id"), item.get("code"), item.get("reason"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        blockers = deduped
        ok = not blockers and (not fail_on_warnings or not warnings)
        return {
            "ok": ok,
            "translated_unique": translated,
            "untranslated_unique": untranslated,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "blockers": blockers[:200],
            "warnings": warnings[:200],
            "require_translated": bool(require_translated),
            "fail_on_warnings": bool(fail_on_warnings),
        }
    finally:
        knowledge.close()
        project.close()


def _source_from_skeleton(skeleton: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for item in skeleton:
        if item.get("kind") == "protected":
            out.append(str(item.get("value") or ""))
        elif item.get("kind") == "text":
            out.append(str(item.get("source") or ""))
    return "".join(out)


def _reconstruct_record(
    skeleton: list[dict[str, Any]],
    span_units: dict[int, str],
    targets: dict[str, str],
) -> tuple[str, int, int]:
    out: list[str] = []
    translated = 0
    text_spans = 0
    for index, item in enumerate(skeleton):
        kind = item.get("kind")
        if kind == "protected":
            out.append(str(item.get("value") or ""))
            continue
        if kind != "text":
            raise ValueError(f"未知 skeleton 类型：{kind!r}")
        text_spans += 1
        unit_id = span_units.get(index) or str(item.get("unit_id") or "")
        source = str(item.get("source") or "")
        target = targets.get(unit_id) if unit_id else None
        if target is not None:
            out.append(target)
            translated += 1
        else:
            out.append(source)
    return "".join(out), translated, text_spans


def prepare_legacy_records(
    workspace: Path,
    project_db_path: Path,
    output_records_path: Path,
) -> dict[str, Any]:
    """Create a temporary legacy record cache from vNext targets.

    The user's legacy `text_records.json` is never modified. The resulting file is only
    used by the existing byte-safe materializer inside the isolated build staging area.
    """
    workspace = Path(workspace).resolve()
    records_path = workspace / "localization" / "text_records.json"
    if not records_path.is_file():
        raise FileNotFoundError(records_path)
    records = json.loads(records_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("text_records.json 不是记录数组")

    db = init_project_db(Path(project_db_path))
    try:
        targets = {
            row["unit_id"]: str(row["target_text"])
            for row in db.execute(
                """SELECT unit_id,target_text FROM current_targets
                   WHERE qa_status='passed' AND target_status!='rejected'"""
            )
        }
        groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
        for row in db.execute(
            """SELECT o.*,u.source_text FROM occurrences o
               JOIN translation_units u ON u.unit_id=o.unit_id
               WHERE o.active=1 ORDER BY o.pak_name,o.source_file,o.record_id,o.occurrence_id"""
        ):
            groups[(row["pak_name"], row["source_file"], row["record_id"])].append(row)

        record_index = {
            (str(r.get("pak") or ""), str(r.get("source_file") or ""), str(r.get("id") or "")): i
            for i, r in enumerate(records)
            if isinstance(r, dict)
        }
        changed = 0
        mapped = 0
        untranslated_spans = 0
        errors: list[dict[str, Any]] = []

        for key, occurrence_rows in groups.items():
            idx = record_index.get(key)
            if idx is None:
                errors.append({"code": "LEGACY_RECORD_MISSING", "pak": key[0], "file": key[1], "record_id": key[2]})
                continue
            record = records[idx]
            mapped += 1
            try:
                skeleton = json.loads(occurrence_rows[0]["skeleton_json"] or "[]")
            except Exception as exc:
                errors.append({"code": "BAD_SKELETON_JSON", "record_id": key[2], "error": str(exc)})
                continue
            if not isinstance(skeleton, list) or not skeleton:
                errors.append({"code": "EMPTY_SKELETON", "record_id": key[2]})
                continue
            span_units: dict[int, str] = {}
            for occ in occurrence_rows:
                try:
                    locator = json.loads(occ["locator_json"] or "{}")
                    span_index = int(locator.get("span_index", 0))
                except Exception:
                    span_index = 0
                span_units[span_index] = occ["unit_id"]
            source_from_skeleton = _source_from_skeleton(skeleton)
            legacy_source = str(record.get("source_original") or record.get("original") or "")
            if normalize_source(source_from_skeleton) != normalize_source(legacy_source):
                errors.append(
                    {
                        "code": "STALE_SOURCE_MAPPING",
                        "record_id": key[2],
                        "expected": source_from_skeleton[:200],
                        "actual": legacy_source[:200],
                    }
                )
                continue
            rebuilt, translated_count, text_span_count = _reconstruct_record(skeleton, span_units, targets)
            untranslated_spans += max(0, text_span_count - translated_count)
            if rebuilt != legacy_source:
                record["original"] = rebuilt
                record["status"] = "已翻译"
                changed += 1

        if errors:
            raise ValueError(
                "vNext 与旧工作区记录映射不一致，已阻止构建："
                + json.dumps(errors[:20], ensure_ascii=False)
            )
        output_records_path = Path(output_records_path)
        output_records_path.parent.mkdir(parents=True, exist_ok=True)
        output_records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "records_total": len(records),
            "mapped_records": mapped,
            "changed_records": changed,
            "untranslated_spans": untranslated_spans,
            "output_records": str(output_records_path),
        }
    finally:
        db.close()


def _atomic_publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".vnext.tmp", dir=destination.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)
    finally:
        if tmp.exists():
            tmp.unlink()


def _record_snapshot(db, build_id: str, source_manifest: dict[str, Any], target_manifest: dict[str, Any], verification: dict[str, Any], status: str) -> None:
    db.execute(
        """INSERT OR REPLACE INTO build_snapshots(
             build_id,created_at,source_manifest_json,target_manifest_json,verification_json,status
           ) VALUES(?,?,?,?,?,?)""",
        (
            build_id,
            utcnow(),
            json.dumps(source_manifest, ensure_ascii=False, separators=(",", ":")),
            json.dumps(target_manifest, ensure_ascii=False, separators=(",", ":")),
            json.dumps(verification, ensure_ascii=False, separators=(",", ":")),
            status,
        ),
    )
    db.commit()


def build_verified_paks(
    workspace: Path,
    *,
    project_db_path: Path | None = None,
    knowledge_db_path: Path | None = None,
    output_dir: Path | None = None,
    pak_names: list[str] | None = None,
    workers: int = 1,
    require_translated: bool = False,
    fail_on_warnings: bool = False,
) -> dict[str, Any]:
    """Materialize and publish PAKs only after every selected archive verifies.

    All archives are first built into an isolated staging directory. Nothing reaches the
    final output directory unless every selected changed PAK passes legacy structural,
    encoding, compression, re-extraction and byte-roundtrip gates.
    """
    workspace = Path(workspace).resolve()
    project_db_path = Path(project_db_path or (workspace / "vnext" / "project.sqlite3"))
    output_dir = Path(output_dir or (workspace / "build-vnext")).resolve()
    preflight = preflight_build(
        project_db_path,
        knowledge_db_path,
        require_translated=require_translated,
        fail_on_warnings=fail_on_warnings,
    )
    if not preflight["ok"]:
        raise ValueError("vNext 构建前 QA 未通过：" + json.dumps(preflight["blockers"][:20], ensure_ascii=False))

    project_json = workspace / "project.json"
    if not project_json.is_file():
        raise FileNotFoundError(project_json)
    project = json.loads(project_json.read_text(encoding="utf-8"))
    paks = list(project.get("paks") or [])
    if pak_names:
        wanted = {str(x) for x in pak_names}
        paks = [item for item in paks if str(item.get("pak") or "") in wanted or Path(str(item.get("path") or "")).name in wanted]
        missing = wanted - {str(item.get("pak") or "") for item in paks} - {Path(str(item.get("path") or "")).name for item in paks}
        if missing:
            raise ValueError("项目中找不到 PAK：" + ", ".join(sorted(missing)))
    if not paks:
        raise ValueError("没有可构建的 PAK")

    build_id = stable_id("b_", utcnow(), str(workspace), ",".join(sorted(str(x.get("pak") or "") for x in paks)))
    db = init_project_db(project_db_path)
    source_manifest: dict[str, Any] = {"workspace": str(workspace), "paks": []}
    target_manifest: dict[str, Any] = {"output_dir": str(output_dir), "paks": []}
    verification: dict[str, Any] = {"preflight": preflight, "paks": []}

    stage_parent = workspace / "vnext" / "build_staging"
    stage_parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=f"{build_id}_", dir=stage_parent) as td:
            stage = Path(td)
            temp_records = stage / "text_records.json"
            record_report = prepare_legacy_records(workspace, project_db_path, temp_records)
            verification["record_materialization"] = record_report
            candidates: list[tuple[Path, Path, Path | None]] = []

            for item in paks:
                pak_name = str(item.get("pak") or Path(str(item.get("path") or "")).name)
                original = Path(str(item.get("path") or "")).resolve()
                extracted = Path(str(item.get("extracted") or "")).resolve()
                if not original.is_file() or not extracted.is_dir():
                    raise ValueError(f"PAK 基线缺失：{pak_name}")
                original_sha = sha256(original)
                source_manifest["paks"].append(
                    {"pak": pak_name, "path": str(original), "sha256": original_sha, "size": original.stat().st_size}
                )

                modified = stage / "modified" / Path(pak_name).stem
                materialize_report = materialize_records_to_modified_dir(extracted, temp_records, pak_name, modified)
                if int(materialize_report.get("skipped_count") or 0) or int(materialize_report.get("safe_fallback_count") or 0):
                    raise ValueError(
                        f"{pak_name} 有译文未能严格写回，已停止构建："
                        + json.dumps(
                            {
                                "skipped": materialize_report.get("skipped", [])[:10],
                                "fallbacks": materialize_report.get("safe_fallbacks", [])[:10],
                            },
                            ensure_ascii=False,
                        )
                    )
                if materialize_report.get("no_safe_changes") or materialize_report.get("no_changes"):
                    verification["paks"].append(
                        {"pak": pak_name, "status": "no_changes", "materialize": materialize_report}
                    )
                    continue

                candidate = stage / "candidate" / Path(original).name
                candidate.parent.mkdir(parents=True, exist_ok=True)
                report = build_from_modified_dir(
                    original,
                    extracted,
                    modified,
                    candidate,
                    workers=max(1, int(workers)),
                    verify=True,
                )
                if report.get("roundtrip") != "pass" or int(report.get("verify_failed") or 0) != 0:
                    raise ValueError(f"{pak_name} 构建后重解包验证失败")
                candidate_sha = sha256(candidate)
                final_path = output_dir / Path(original).name
                candidates.append((candidate, final_path, candidate.with_suffix(candidate.suffix + ".build.json")))
                target_manifest["paks"].append(
                    {"pak": pak_name, "path": str(final_path), "sha256": candidate_sha, "size": candidate.stat().st_size}
                )
                verification["paks"].append(
                    {"pak": pak_name, "status": "verified", "materialize": materialize_report, "build": report}
                )

            # Publish only after all selected archives have verified successfully.
            for candidate, final_path, report_path in candidates:
                _atomic_publish(candidate, final_path)
                if sha256(final_path) != sha256(candidate):
                    raise ValueError(f"发布后 SHA-256 不一致：{final_path.name}")
                if report_path and report_path.is_file():
                    _atomic_publish(report_path, final_path.with_suffix(final_path.suffix + ".build.json"))

            status = "verified" if candidates else "verified_no_changes"
            _record_snapshot(db, build_id, source_manifest, target_manifest, verification, status)
            return {
                "ok": True,
                "build_id": build_id,
                "status": status,
                "output_dir": str(output_dir),
                "published_paks": len(candidates),
                "source_manifest": source_manifest,
                "target_manifest": target_manifest,
                "verification": verification,
            }
    except Exception as exc:
        verification["error"] = str(exc)
        _record_snapshot(db, build_id, source_manifest, target_manifest, verification, "failed")
        raise
    finally:
        db.close()


def build_history(project_db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    db = init_project_db(Path(project_db_path))
    try:
        rows = db.execute(
            "SELECT * FROM build_snapshots ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for field in ("source_manifest_json", "target_manifest_json", "verification_json"):
                try:
                    item[field[:-5]] = json.loads(item.pop(field) or "{}")
                except Exception:
                    item[field[:-5]] = {}
            out.append(item)
        return out
    finally:
        db.close()
