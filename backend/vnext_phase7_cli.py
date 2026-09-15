#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vnext.phase7 import audit_xlsx_corpus, run_real_build_validation, workspace_readiness


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Studio vNext Phase-7 real-data validation")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit-xlsx", help="audit a real combined player-visible XLSX")
    p_audit.add_argument("--xlsx", required=True)
    p_audit.add_argument("--sample-limit", type=int, default=40)

    p_ready = sub.add_parser("readiness", help="check a real three-PAK Studio workspace")
    p_ready.add_argument("--workspace", required=True)
    p_ready.add_argument("--knowledge-db", default="")

    p_build = sub.add_parser("build", help="run verified three-PAK materialize/rebuild/re-extract validation")
    p_build.add_argument("--workspace", required=True)
    p_build.add_argument("--knowledge-db", default="")
    p_build.add_argument("--output-dir", default="")
    p_build.add_argument("--workers", type=int, default=1)
    p_build.add_argument("--require-translated", action="store_true")
    p_build.add_argument("--fail-on-warnings", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "audit-xlsx":
            report = audit_xlsx_corpus(Path(args.xlsx), sample_limit=max(0, args.sample_limit))
            emit(report)
            return 0 if report.get("ok") else 1
        if args.command == "readiness":
            knowledge = Path(args.knowledge_db).resolve() if args.knowledge_db else None
            report = workspace_readiness(Path(args.workspace), knowledge_db_path=knowledge)
            emit(report)
            return 0 if report.get("ok") else 1
        if args.command == "build":
            knowledge = Path(args.knowledge_db).resolve() if args.knowledge_db else None
            output = Path(args.output_dir).resolve() if args.output_dir else None
            report = run_real_build_validation(
                Path(args.workspace),
                knowledge_db_path=knowledge,
                output_dir=output,
                workers=max(1, args.workers),
                require_translated=args.require_translated,
                fail_on_warnings=args.fail_on_warnings,
            )
            emit(report)
            return 0 if report.get("ok") else 1
        return 2
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
