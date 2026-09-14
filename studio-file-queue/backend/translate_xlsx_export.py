#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_xlsx_export.py
========================
翻译 xlsx_export 目录下的完整 XLSX 文件（包含所有唯一行，含已翻译和未翻译）。
支持断点续跑，每 100 行一个 bucket，使用 Ollama qwen3:14b。

用法：
  # 冒烟测试（前 20 行）
  python backend/translate_xlsx_export.py --pak ui.pak --max-buckets 1

  # 完整翻译
  python backend/translate_xlsx_export.py --pak ui.pak

  # 从 checkpoint 续跑（默认）
  python backend/translate_xlsx_export.py --pak ui.pak

  # 强制重新开始（忽略 checkpoint）
  python backend/translate_xlsx_export.py --pak ui.pak --no-resume
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# 复用现有模块
import ollama_batch_translate as obt
import import_untranslated_xlsx as iux
import export_untranslated_xlsx as eux
import xlsx_localization as xlsx_loc

XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _clean_xml_control_chars(data: bytes) -> bytes:
    """移除 XML 不允许的控制字符（\x00-\x08, \x0b, \x0c, \x0e-\x1f）"""
    import re
    return re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]', b'', data)


def read_xlsx_export(xlsx_path: Path) -> list[dict]:
    """读取 xlsx_export 的 XLSX 文件，返回 [{id, text}, ...]
    
    自定义读取逻辑，先清理 XML 中的控制字符再解析。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    
    XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    
    with zipfile.ZipFile(xlsx_path, "r") as zf:
        # 读取并清理 sharedStrings
        shared = []
        try:
            ss_data = zf.read("xl/sharedStrings.xml")
            ss_data = _clean_xml_control_chars(ss_data)
            sroot = ET.fromstring(ss_data)
            ns = {"x": XML_NS}
            for si in sroot.findall("x:si", ns):
                texts = [n.text or "" for n in si.iter() if n.tag.endswith("}t")]
                shared.append("".join(texts))
        except KeyError:
            shared = None
        
        # 读取并清理 sheet1.xml
        sheet_data = zf.read("xl/worksheets/sheet1.xml")
        sheet_data = _clean_xml_control_chars(sheet_data)
        root = ET.fromstring(sheet_data)
    
    ns = {"x": XML_NS}
    
    def cell_value(cell) -> str:
        cell_type = cell.attrib.get("t", "")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.findall(".//x:t", ns))
        value = cell.find("x:v", ns)
        raw = value.text if value is not None else ""
        if cell_type == "s":
            try:
                return shared[int(raw)]
            except Exception:
                return ""
        return raw or ""
    
    table = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values = []
        for cell in row.findall("x:c", ns):
            values.append(cell_value(cell))
        table.append(values)
    
    if not table:
        return []
    
    headers = [str(x or "").strip().replace("\ufeff", "") for x in table[0]]
    rows = []
    for values in table[1:]:
        if not any(str(v or "").strip() for v in values):
            continue
        item = {}
        for i, header in enumerate(headers):
            if header:
                item[header] = values[i] if i < len(values) else ""
        rows.append(item)
    
    # 转换为 [{id, text}] 格式
    result = []
    for r in rows:
        rid = str(r.get("id") or r.get("编号") or r.get("ID") or "").strip()
        txt = str(r.get("text") or r.get("原文") or r.get("越南语") or "").strip()
        if rid:
            result.append({"id": rid, "text": txt})
    return result


def write_xlsx_export(xlsx_path: Path, rows: list[dict]) -> None:
    """写回翻译后的 XLSX（覆盖原文件，先备份）"""
    backup = xlsx_path.with_name(xlsx_path.name + ".bak")
    if not backup.exists():
        import shutil
        shutil.copy2(xlsx_path, backup)
    obt.overwrite_xlsx(xlsx_path, rows)


def run_translation(
    workspace: str,
    pak: str,
    max_buckets: int = 0,
    resume: bool = True,
    bucket_size: int = 100,
    ollama_base: str = None,
    xlsx: str = None,
):
    """翻译 xlsx_export 的 XLSX 文件"""
    ws = Path(workspace)
    base = re.sub(r"\.pak$", "", pak, flags=re.I)

    # 支持直接指定 XLSX 路径
    if xlsx:
        xlsx_path = Path(xlsx)
    else:
        # xlsx_export 路径：paks++/<pak>/xlsx_export/<base>/<base>_localization.xlsx
        xlsx_path = ws / base / "xlsx_export" / base / f"{base}_localization.xlsx"
    
    if not xlsx_path.exists():
        raise FileNotFoundError(f"未找到 XLSX：{xlsx_path}")

    # checkpoint 路径
    ckpt_path = xlsx_path.parent / "translate_checkpoint.json"

    # 翻译源优先使用 .bak（原始越南语），避免 .xlsx 被之前的冒烟测试污染
    bak_path = xlsx_path.with_name(xlsx_path.name + ".bak")
    source_path = bak_path if bak_path.exists() else xlsx_path
    print(f"[INFO] 读取翻译源：{source_path}")
    rows_in = read_xlsx_export(source_path)
    for row in rows_in:
        row["id"] = str(row.get("id", "")).strip()

    total_rows = len(rows_in)
    buckets = (total_rows + bucket_size - 1) // bucket_size

    print(f"[INFO] 总行数：{total_rows:,} | Bucket 大小：{bucket_size} | Buckets：{buckets}")

    # 加载翻译 profile
    profile = obt.load_translator_profile()
    model = profile.get("model") or "qwen3:14b"
    system_prompt = profile.get("prompt") or "你是翻译助手。"

    if ollama_base is None:
        ollama_base = str(profile.get("baseUrl") or obt.OLLAMA_BASE)
        ollama_base = re.sub(r"/v1/?$", "", ollama_base).rstrip("/") or obt.OLLAMA_BASE

    print(f"[INFO] 模型：{model} | Ollama：{ollama_base}")
    print(f"[INFO] 提示词长度：{len(system_prompt)} chars")

    # 加载 checkpoint
    done: set[int] = set()
    translations: dict[str, str] = {}

    if resume and ckpt_path.exists():
        try:
            ck = json.loads(ckpt_path.read_text(encoding="utf-8"))
            if ck.get("bucket_size") == bucket_size:
                done = set(int(x) for x in ck.get("done_buckets", []))
                translations = ck.get("translations", {})
                print(f"[INFO] 续跑：已完成 {len(done)}/{buckets} 桶，已译 {len(translations):,} 条")
            else:
                print("[INFO] Bucket 大小已变化，忽略旧 checkpoint")
        except Exception as e:
            print(f"[WARN] 读取 checkpoint 失败：{e}")

    started_at = time.time()
    failed_buckets: dict[int, str] = {}

    for b_idx in range(buckets):
        if b_idx in done:
            continue

        if max_buckets and len(done) >= max_buckets:
            print(f"[STOP] 达到 max-buckets={max_buckets}，退出")
            break

        start_row = b_idx * bucket_size
        end_row = min(start_row + bucket_size, total_rows)
        bucket_rows = rows_in[start_row:end_row]

        print(f"\n[BUCKET {b_idx + 1}/{buckets}] 行 {start_row + 1}-{end_row} ({len(bucket_rows)} 行)")

        try:
            # 调用 Ollama 翻译
            msgs = obt.build_bucket_prompt(system_prompt, bucket_rows)
            reply = obt.ollama_chat(msgs, model=model, base=ollama_base)

            # 解析 JSON
            arr = obt.parse_json_array_block(reply)

            # 提取译文（带占位符校验 + 越南语残留拦截）
            src_map = {r["id"]: r["text"] for r in bucket_rows}
            bucket_translations = {}
            for item in arr:
                rid = str(item.get("id", "")).strip()
                text = str(item.get("text", ""))
                if not (rid and text):
                    continue
                src = src_map.get(rid, "")
                ok, reason = iux.validate(src, text)
                if ok and not eux.has_natural_language_residual(text):
                    bucket_translations[rid] = text

            # 更新全局 translations
            translations.update(bucket_translations)
            done.add(b_idx)

            skipped = len(bucket_rows) - len(bucket_translations)
            print(f"[OK] 桶 {b_idx + 1} 完成，接受 {len(bucket_translations)}/{len(bucket_rows)} 条（跳过 {skipped} 条）")

        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            failed_buckets[b_idx] = err_msg
            print(f"[FAIL] 桶 {b_idx + 1} 失败：{err_msg}")

        # 每桶写一次 checkpoint
        ckpt_path.write_text(
            json.dumps(
                {
                    "pak": pak,
                    "model": model,
                    "bucket_size": bucket_size,
                    "done_buckets": sorted(int(x) for x in done),
                    "total_buckets": buckets,
                    "translations": translations,
                    "failed_buckets": {str(k): v for k, v in failed_buckets.items()},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # 写回 XLSX
    print(f"\n[WRITE] 写回翻译后的 XLSX...")
    new_rows = []
    for r in rows_in:
        rid = r["id"]
        zh = translations.get(rid, "")
        new_rows.append({"id": rid, "text": zh if zh.strip() else r["text"]})

    write_xlsx_export(xlsx_path, new_rows)

    elapsed = time.time() - started_at
    translated_count = sum(1 for r in rows_in if translations.get(r["id"], "").strip())

    report = {
        "pak": pak,
        "xlsx": str(xlsx_path),
        "model": model,
        "bucket_size": bucket_size,
        "total_rows": total_rows,
        "translated_rows": translated_count,
        "remaining_rows": total_rows - translated_count,
        "done_buckets": len(done),
        "failed_buckets": len(failed_buckets),
        "elapsed_seconds": round(elapsed, 1),
        "checkpoint": str(ckpt_path),
    }

    report_path = xlsx_path.parent / "translate_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"批量翻译完成")
    print(f"{'=' * 60}")
    for k, v in report.items():
        print(f"  {k}: {v}")

    if failed_buckets:
        print(f"\n[WARN] 失败桶 {len(failed_buckets)} 个（可重跑续翻）：")
        for k, v in list(failed_buckets.items())[:5]:
            print(f"  - 桶 {k + 1}: {v[:120]}")

    print(f"\n下一步：")
    print(f"  1. 检查翻译质量：{xlsx_path}")
    print(f"  2. 如需导入 Studio，运行：")
    print(f"     python backend/import_untranslated_xlsx.py --pak {pak}")

    return report


def main():
    ap = argparse.ArgumentParser(description="翻译 xlsx_export 目录下的 XLSX 文件")
    ap.add_argument("--workspace", default=r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++")
    ap.add_argument("--pak", required=True, help="ui.pak / settings.pak / updatefs.pak")
    ap.add_argument("--xlsx", default=None, help="直接指定 XLSX 文件路径（可选）")
    ap.add_argument("--max-buckets", type=int, default=0, help="0=全部，>0=只跑指定桶数（冒烟测试）")
    ap.add_argument("--no-resume", action="store_true", help="不从 checkpoint 续跑")
    ap.add_argument("--bucket-size", type=int, default=100, help="每桶行数（默认 100）")
    ap.add_argument("--ollama", default=None, help="Ollama 地址（默认从 profile 读取）")
    args = ap.parse_args()

    run_translation(
        workspace=args.workspace,
        pak=args.pak,
        max_buckets=args.max_buckets,
        resume=not args.no_resume,
        bucket_size=args.bucket_size,
        ollama_base=args.ollama,
        xlsx=args.xlsx,
    )


if __name__ == "__main__":
    main()
