#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vnext.compatibility import adopt_legacy_targets, workspace_sync_status
from vnext.database import default_knowledge_db


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio vNext Phase-6 legacy compatibility tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--workspace", required=True)

    p_adopt = sub.add_parser("adopt-legacy")
    p_adopt.add_argument("--workspace", required=True)
    p_adopt.add_argument("--knowledge-db", default="")
    p_adopt.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()
    try:
        workspace = Path(args.workspace).resolve()
        project_db = workspace / "vnext" / "project.sqlite3"
        if args.command == "status":
            emit({"ok": True, "report": workspace_sync_status(workspace, project_db)})
            return 0
        if args.command == "adopt-legacy":
            knowledge = Path(args.knowledge_db).resolve() if args.knowledge_db else default_knowledge_db()
            report = adopt_legacy_targets(
                workspace,
                project_db,
                knowledge,
                overwrite=bool(args.overwrite),
            )
            emit(report)
            return 0
        return 2
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
