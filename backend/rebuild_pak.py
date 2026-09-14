#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_pak.py — 基于 paks++/localization/text_records.json 的当前内容，
对指定单个 PAK 重新执行：
  1. materialize_records_to_modified_dir (可见文字 UTF-8，结构保持原始字节)
  2. build_from_modified_dir (生成 updatefs.pak / ui.pak / settings.pak / script.pak)

只处理实际修改过的文件 (delta)，降低 Init Read... Error 风险。

用法：
  python backend/rebuild_pak.py --pak updatefs.pak
  python backend/rebuild_pak.py --pak ui.pak
  python backend/rebuild_pak.py --pak settings.pak
  python backend/rebuild_pak.py --pak script.pak
  # 手动指定原始pak路径和输出路径
  python backend/rebuild_pak.py --pak updatefs.pak --original ./paks++/original_paks/updatefs.pak --out ./paks++/build/updatefs.pak
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
WORKSPACE = BACKEND.parent / "paks++"

sys.path.insert(0, str(BACKEND))
from pak_builder import materialize_records_to_modified_dir, build_from_modified_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", required=True, help="PAK名，如 updatefs.pak / ui.pak / settings.pak / script.pak")
    ap.add_argument("--workspace", default=str(WORKSPACE))
    ap.add_argument("--original", default="", help="原始 PAK 路径，默认用 workspace/original_paks/<pak>")
    ap.add_argument("--out", default="", help="输出 PAK 路径，默认 workspace/build/<pak>")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    ws = Path(args.workspace)
    base = args.pak.replace(".pak", "")
    extracted = ws / "extracted" / base
    records_path = ws / "localization" / "text_records.json"
    modified_dir = ws / "modified" / base
    original_pak = Path(args.original) if args.original else ws / "original_paks" / args.pak
    out_pak = Path(args.out) if args.out else ws / "build" / args.pak

    for must_exist, name in [(extracted, "extracted"), (records_path, "records"), (original_pak, "original_pak")]:
        if not must_exist.exists():
            raise SystemExit(f"[FAIL] {name} 不存在: {must_exist}")
    out_pak.parent.mkdir(parents=True, exist_ok=True)

    print(f"[{args.pak}] encoding = utf8-visible-cells/raw-structure-v1")
    print(f"  extracted   = {extracted}")
    print(f"  records     = {records_path}")
    print(f"  modified_dir= {modified_dir}")
    print(f"  original_pak= {original_pak}")
    print(f"  output_pak  = {out_pak}")

    # Step 1: materialize
    mat_report = materialize_records_to_modified_dir(extracted, records_path, args.pak, modified_dir)
    print()
    print("[1/2] materialize 完成:")
    print(f"  modified_records    : {mat_report['modified_records']:,}")
    print(f"  changed_files_count : {mat_report['changed_file_count']:,}")
    print(f"  skipped_count       : {mat_report['skipped_count']:,}")
    print(f"  encoding            : {mat_report['encoding']}")
    if mat_report['skipped'][:5]:
        print(f"  skipped sample:")
        for s in mat_report['skipped'][:5]:
            print(f"    {s}")

    # Step 2: build
    print()
    print("[2/2] 构建 PAK ...")
    info = build_from_modified_dir(
        original_pak=original_pak,
        original_dir=extracted,
        modified_dir=modified_dir,
        output=out_pak,
        workers=args.workers,
        verify=not args.no_verify,
    )
    print()
    print("==== 构建完成 ====")
    for k, v in info.items():
        if isinstance(v, int):
            print(f"  {k}: {v:,}")
        else:
            sv = str(v)
            print(f"  {k}: {sv[:160]}")
    print()
    print(f"输出 PAK: {out_pak}  ({out_pak.stat().st_size/1024/1024:.2f} MB)")
    print(f"下一步: 将 {out_pak.name} 拷贝到游戏 pak 目录覆盖原文件后重启游戏。")


if __name__ == "__main__":
    main()
