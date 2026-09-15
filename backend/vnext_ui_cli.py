#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vnext.database import default_knowledge_db
from vnext.ui_api import (
    dashboard,
    delete_glossary,
    delete_tm,
    knowledge_list,
    save_glossary,
    save_tm,
)


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def knowledge_path(value: str) -> Path:
    return Path(value).resolve() if value else default_knowledge_db()


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio vNext Phase-5 UI API")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dashboard = sub.add_parser("dashboard")
    p_dashboard.add_argument("--workspace", required=True)
    p_dashboard.add_argument("--knowledge-db", default="")

    p_list = sub.add_parser("knowledge-list")
    p_list.add_argument("--kind", required=True, choices=["tm", "glossary", "reference"])
    p_list.add_argument("--knowledge-db", default="")
    p_list.add_argument("--query", default="")
    p_list.add_argument("--limit", type=int, default=200)
    p_list.add_argument("--offset", type=int, default=0)

    p_glossary_save = sub.add_parser("glossary-save")
    p_glossary_save.add_argument("--knowledge-db", default="")
    p_glossary_save.add_argument("--source", required=True)
    p_glossary_save.add_argument("--target", required=True)
    p_glossary_save.add_argument("--term-type", default="general")
    p_glossary_save.add_argument("--scope", default="global")
    p_glossary_save.add_argument("--status", default="approved")
    p_glossary_save.add_argument("--priority", type=int, default=100)
    p_glossary_save.add_argument("--locked", action="store_true")
    p_glossary_save.add_argument("--note", default="")

    p_glossary_delete = sub.add_parser("glossary-delete")
    p_glossary_delete.add_argument("--knowledge-db", default="")
    p_glossary_delete.add_argument("--term-id", type=int, required=True)

    p_tm_save = sub.add_parser("tm-save")
    p_tm_save.add_argument("--knowledge-db", default="")
    p_tm_save.add_argument("--source", required=True)
    p_tm_save.add_argument("--target", required=True)
    p_tm_save.add_argument("--locked", action="store_true")

    p_tm_delete = sub.add_parser("tm-delete")
    p_tm_delete.add_argument("--knowledge-db", default="")
    p_tm_delete.add_argument("--tm-id", type=int, required=True)

    args = parser.parse_args()
    try:
        if args.command == "dashboard":
            workspace = Path(args.workspace).resolve()
            emit({"ok": True, "report": dashboard(
                workspace,
                workspace / "vnext" / "project.sqlite3",
                knowledge_path(args.knowledge_db),
            )})
            return 0
        if args.command == "knowledge-list":
            emit({"ok": True, "report": knowledge_list(
                knowledge_path(args.knowledge_db),
                kind=args.kind,
                query=args.query,
                limit=args.limit,
                offset=args.offset,
            )})
            return 0
        if args.command == "glossary-save":
            emit(save_glossary(
                knowledge_path(args.knowledge_db),
                source=args.source,
                target=args.target,
                term_type=args.term_type,
                scope=args.scope,
                status=args.status,
                priority=args.priority,
                locked=args.locked,
                note=args.note,
            ))
            return 0
        if args.command == "glossary-delete":
            report = delete_glossary(knowledge_path(args.knowledge_db), args.term_id)
            emit(report)
            return 0 if report.get("ok") else 1
        if args.command == "tm-save":
            emit(save_tm(
                knowledge_path(args.knowledge_db),
                source=args.source,
                target=args.target,
                locked=args.locked,
            ))
            return 0
        if args.command == "tm-delete":
            report = delete_tm(knowledge_path(args.knowledge_db), args.tm_id)
            emit(report)
            return 0 if report.get("ok") else 1
        return 2
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
