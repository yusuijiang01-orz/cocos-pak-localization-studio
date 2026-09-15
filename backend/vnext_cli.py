#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vnext.build import build_history, build_verified_paks, preflight_build
from vnext.database import default_knowledge_db, init_knowledge_db, init_project_db, migrate_legacy_tm
from vnext.ingest import ingest_full_xlsx, project_stats
from vnext.jobs import job_summary, latest_job
from vnext.ollama_engine import OllamaConfig, OllamaEngine
from vnext.pipeline import run_pipeline, stop_latest_job
from vnext.review import (
    approve_review,
    list_review_items,
    reject_review,
    review_detail,
    review_stats,
    set_review_state,
    sync_review_queue,
)
from vnext.workspace_ingest import ingest_workspace_records


def emit(value, *, compact: bool = False):
    if compact:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def _knowledge_path(value: str) -> Path:
    return Path(value).resolve() if value else default_knowledge_db()


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio vNext core tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="initialize side-by-side vNext databases")
    p_init.add_argument("--workspace", required=True)
    p_init.add_argument("--knowledge-db", default="")

    p_ingest = sub.add_parser("ingest-xlsx", help="ingest the full multi-PAK XLSX as immutable source units")
    p_ingest.add_argument("--workspace", required=True)
    p_ingest.add_argument("--xlsx", required=True)
    p_ingest.add_argument("--project-name", default="Imported localization")

    p_ingest_ws = sub.add_parser("ingest-workspace", help="ingest live Studio records with out-of-band protected skeletons")
    p_ingest_ws.add_argument("--workspace", required=True)
    p_ingest_ws.add_argument("--project-name", default="")

    p_stats = sub.add_parser("stats", help="show vNext project unit statistics")
    p_stats.add_argument("--workspace", required=True)

    p_migrate = sub.add_parser("migrate-legacy-tm", help="copy legacy TM/glossary into vNext knowledge DB")
    p_migrate.add_argument("--legacy-db", required=True)
    p_migrate.add_argument("--knowledge-db", default="")

    p_translate = sub.add_parser("translate", help="run TM/reference/cache/Ollama translation pipeline")
    p_translate.add_argument("--workspace", required=True)
    p_translate.add_argument("--knowledge-db", default="")
    p_translate.add_argument("--model", default="qwen3:14b")
    p_translate.add_argument("--ollama-base", default="http://127.0.0.1:11435")
    p_translate.add_argument("--batch-size", type=int, default=16)
    p_translate.add_argument("--max-units", type=int, default=0)
    p_translate.add_argument("--timeout", type=int, default=600)
    p_translate.add_argument("--temperature", type=float, default=0.1)

    p_stop = sub.add_parser("stop-job", help="request cooperative stop and keep committed progress")
    p_stop.add_argument("--workspace", required=True)
    p_stop.add_argument("--job-id", default="")

    p_job = sub.add_parser("job-status", help="show latest or selected vNext translation job")
    p_job.add_argument("--workspace", required=True)
    p_job.add_argument("--job-id", default="")

    p_review_sync = sub.add_parser("review-sync", help="refresh Phase-3 QA/manual-review queue")
    p_review_sync.add_argument("--workspace", required=True)
    p_review_sync.add_argument("--knowledge-db", default="")

    p_review_list = sub.add_parser("review-list", help="list queued QA/manual-review items")
    p_review_list.add_argument("--workspace", required=True)
    p_review_list.add_argument("--knowledge-db", default="")
    p_review_list.add_argument("--state", default="pending", choices=["pending", "approved", "rejected", "deferred", "resolved", "all"])
    p_review_list.add_argument("--severity", default="", choices=["", "info", "warning", "error", "fatal"])
    p_review_list.add_argument("--limit", type=int, default=100)
    p_review_list.add_argument("--offset", type=int, default=0)
    p_review_list.add_argument("--query", default="")
    p_review_list.add_argument("--no-sync", action="store_true")

    p_review_show = sub.add_parser("review-show", help="show one review item with occurrences and QA findings")
    p_review_show.add_argument("--workspace", required=True)
    p_review_show.add_argument("--unit-id", required=True)

    p_review_approve = sub.add_parser("review-approve", help="approve/edit a translation and promote it into trusted TM")
    p_review_approve.add_argument("--workspace", required=True)
    p_review_approve.add_argument("--knowledge-db", default="")
    p_review_approve.add_argument("--unit-id", required=True)
    p_review_approve.add_argument("--target", default="")
    p_review_approve.add_argument("--note", default="")
    p_review_approve.add_argument("--no-lock", action="store_true")

    p_review_reject = sub.add_parser("review-reject", help="reject the current suggestion and block the same target in this project")
    p_review_reject.add_argument("--workspace", required=True)
    p_review_reject.add_argument("--knowledge-db", default="")
    p_review_reject.add_argument("--unit-id", required=True)
    p_review_reject.add_argument("--note", default="")

    p_review_defer = sub.add_parser("review-defer", help="defer one review item")
    p_review_defer.add_argument("--workspace", required=True)
    p_review_defer.add_argument("--unit-id", required=True)
    p_review_defer.add_argument("--note", default="")

    p_review_reopen = sub.add_parser("review-reopen", help="move a deferred/rejected item back to pending")
    p_review_reopen.add_argument("--workspace", required=True)
    p_review_reopen.add_argument("--unit-id", required=True)
    p_review_reopen.add_argument("--note", default="")

    p_review_stats = sub.add_parser("review-stats", help="show Phase-3 review/QA statistics")
    p_review_stats.add_argument("--workspace", required=True)
    p_review_stats.add_argument("--knowledge-db", default="")
    p_review_stats.add_argument("--sync", action="store_true")

    p_build_preflight = sub.add_parser("build-preflight", help="run final vNext QA/review gate without touching resources")
    p_build_preflight.add_argument("--workspace", required=True)
    p_build_preflight.add_argument("--knowledge-db", default="")
    p_build_preflight.add_argument("--require-translated", action="store_true")
    p_build_preflight.add_argument("--fail-on-warnings", action="store_true")

    p_build = sub.add_parser("build-paks", help="materialize, rebuild and re-extract-verify selected PAKs")
    p_build.add_argument("--workspace", required=True)
    p_build.add_argument("--knowledge-db", default="")
    p_build.add_argument("--output-dir", default="")
    p_build.add_argument("--pak", action="append", default=[])
    p_build.add_argument("--workers", type=int, default=1)
    p_build.add_argument("--require-translated", action="store_true")
    p_build.add_argument("--fail-on-warnings", action="store_true")

    p_build_history = sub.add_parser("build-history", help="show vNext verified/failed build snapshots")
    p_build_history.add_argument("--workspace", required=True)
    p_build_history.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    workspace = Path(getattr(args, "workspace", ".")).resolve()
    project_db = workspace / "vnext" / "project.sqlite3"

    try:
        if args.command == "init":
            knowledge_db = _knowledge_path(args.knowledge_db)
            init_project_db(project_db).close()
            init_knowledge_db(knowledge_db).close()
            emit({"ok": True, "project_db": str(project_db), "knowledge_db": str(knowledge_db)})
            return 0

        if args.command == "ingest-xlsx":
            report = ingest_full_xlsx(Path(args.xlsx).resolve(), project_db, project_name=args.project_name)
            emit({"ok": True, "project_db": str(project_db), "report": report})
            return 0

        if args.command == "ingest-workspace":
            report = ingest_workspace_records(workspace, project_db, project_name=args.project_name)
            emit({"ok": True, "report": report})
            return 0

        if args.command == "stats":
            emit({"ok": True, "project_db": str(project_db), "report": project_stats(project_db)})
            return 0

        if args.command == "migrate-legacy-tm":
            knowledge_db = _knowledge_path(args.knowledge_db)
            report = migrate_legacy_tm(Path(args.legacy_db).resolve(), knowledge_db)
            emit({"ok": True, "knowledge_db": str(knowledge_db), "report": report})
            return 0

        if args.command == "translate":
            knowledge_db = _knowledge_path(args.knowledge_db)
            engine = OllamaEngine(
                OllamaConfig(
                    model=args.model,
                    base_url=args.ollama_base,
                    timeout=max(1, args.timeout),
                    temperature=args.temperature,
                )
            )

            def progress_event(value):
                emit(value, compact=True)

            report = run_pipeline(
                project_db,
                knowledge_db_path=knowledge_db,
                translator=engine,
                batch_size=max(1, args.batch_size),
                max_units=max(0, args.max_units),
                event=progress_event,
            )
            emit({"ok": report.get("status") in ("completed", "stopped"), "report": report}, compact=True)
            return 0 if report.get("status") in ("completed", "stopped") else 1

        if args.command == "stop-job":
            report = stop_latest_job(project_db, args.job_id)
            emit(report)
            return 0 if report.get("ok") else 1

        if args.command == "job-status":
            db = init_project_db(project_db)
            try:
                if args.job_id:
                    row = db.execute("SELECT job_id FROM translation_jobs WHERE job_id=?", (args.job_id,)).fetchone()
                else:
                    row = latest_job(db)
                if not row:
                    emit({"ok": False, "error": "没有 vNext 翻译任务"})
                    return 1
                emit({"ok": True, "report": job_summary(db, row["job_id"])})
                return 0
            finally:
                db.close()

        if args.command == "review-sync":
            report = sync_review_queue(project_db, _knowledge_path(args.knowledge_db))
            emit({"ok": True, "report": report})
            return 0

        if args.command == "review-list":
            knowledge_db = _knowledge_path(args.knowledge_db)
            sync_report = None
            if not args.no_sync:
                sync_report = sync_review_queue(project_db, knowledge_db)
            items = list_review_items(
                project_db,
                state=args.state,
                severity=args.severity,
                limit=max(1, args.limit),
                offset=max(0, args.offset),
                query=args.query,
            )
            emit({"ok": True, "sync": sync_report, "items": items, "count": len(items)})
            return 0

        if args.command == "review-show":
            emit({"ok": True, "item": review_detail(project_db, args.unit_id)})
            return 0

        if args.command == "review-approve":
            report = approve_review(
                project_db,
                _knowledge_path(args.knowledge_db),
                args.unit_id,
                target_text=args.target,
                note=args.note,
                lock=not args.no_lock,
            )
            emit(report)
            return 0

        if args.command == "review-reject":
            report = reject_review(
                project_db,
                _knowledge_path(args.knowledge_db),
                args.unit_id,
                note=args.note,
            )
            emit(report)
            return 0

        if args.command == "review-defer":
            emit(set_review_state(project_db, args.unit_id, "deferred", note=args.note))
            return 0

        if args.command == "review-reopen":
            emit(set_review_state(project_db, args.unit_id, "pending", note=args.note))
            return 0

        if args.command == "review-stats":
            if args.sync:
                sync_review_queue(project_db, _knowledge_path(args.knowledge_db))
            emit({"ok": True, "report": review_stats(project_db)})
            return 0

        if args.command == "build-preflight":
            report = preflight_build(
                project_db,
                _knowledge_path(args.knowledge_db),
                require_translated=args.require_translated,
                fail_on_warnings=args.fail_on_warnings,
            )
            emit({"ok": report.get("ok", False), "report": report})
            return 0 if report.get("ok") else 1

        if args.command == "build-paks":
            output_dir = Path(args.output_dir).resolve() if args.output_dir else None
            report = build_verified_paks(
                workspace,
                project_db_path=project_db,
                knowledge_db_path=_knowledge_path(args.knowledge_db),
                output_dir=output_dir,
                pak_names=args.pak or None,
                workers=max(1, args.workers),
                require_translated=args.require_translated,
                fail_on_warnings=args.fail_on_warnings,
            )
            emit(report)
            return 0

        if args.command == "build-history":
            emit({"ok": True, "items": build_history(project_db, max(1, args.limit))})
            return 0

        return 2
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
