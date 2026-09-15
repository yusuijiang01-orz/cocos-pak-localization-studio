#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vnext.database import default_knowledge_db, init_knowledge_db, init_project_db, migrate_legacy_tm
from vnext.ingest import ingest_full_xlsx, project_stats
from vnext.jobs import job_summary, latest_job
from vnext.ollama_engine import OllamaConfig, OllamaEngine
from vnext.pipeline import run_pipeline, stop_latest_job


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

    p_stats = sub.add_parser("stats", help="show vNext project unit statistics")
    p_stats.add_argument("--workspace", required=True)

    p_migrate = sub.add_parser("migrate-legacy-tm", help="copy legacy TM/glossary into vNext knowledge DB")
    p_migrate.add_argument("--legacy-db", required=True)
    p_migrate.add_argument("--knowledge-db", default="")

    p_translate = sub.add_parser("translate", help="run Phase-2 TM/reference/cache/Ollama translation pipeline")
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

    args = parser.parse_args()
    workspace = Path(getattr(args, "workspace", ".")).resolve()
    project_db = workspace / "vnext" / "project.sqlite3"

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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
