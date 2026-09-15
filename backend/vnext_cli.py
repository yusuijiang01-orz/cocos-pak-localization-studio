#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vnext.database import default_knowledge_db, init_knowledge_db, init_project_db, migrate_legacy_tm
from vnext.ingest import ingest_full_xlsx, project_stats


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio vNext Phase-1 core tools")
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

    args = parser.parse_args()
    workspace = Path(getattr(args, "workspace", ".")).resolve()
    project_db = workspace / "vnext" / "project.sqlite3"

    if args.command == "init":
        knowledge_db = Path(args.knowledge_db).resolve() if args.knowledge_db else default_knowledge_db()
        init_project_db(project_db).close(); init_knowledge_db(knowledge_db).close()
        emit({"ok": True, "project_db": str(project_db), "knowledge_db": str(knowledge_db)})
        return 0
    if args.command == "ingest-xlsx":
        report = ingest_full_xlsx(Path(args.xlsx).resolve(), project_db, project_name=args.project_name)
        emit({"ok": True, "project_db": str(project_db), "report": report}); return 0
    if args.command == "stats":
        emit({"ok": True, "project_db": str(project_db), "report": project_stats(project_db)}); return 0
    if args.command == "migrate-legacy-tm":
        knowledge_db = Path(args.knowledge_db).resolve() if args.knowledge_db else default_knowledge_db()
        report = migrate_legacy_tm(Path(args.legacy_db).resolve(), knowledge_db)
        emit({"ok": True, "knowledge_db": str(knowledge_db), "report": report}); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
