#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resumable/cancellable glossary XLSX translation.

This wraps the fast matcher from glossary_xlsx_translate.py and adds an atomic
checkpoint.  A graceful stop writes the partially translated workbook before
returning, so Electron can import the completed safe rows instead of discarding
work.  Selecting the same source/glossary pair later resumes from the saved row.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from glossary_xlsx_translate import (
    ID_HEADER_NAMES,
    TEXT_HEADER_NAMES,
    LiteralMatcher,
    _preview_context,
    emit,
    find_header,
    load_glossary,
    load_json,
    make_live_updates,
    mapping_rows,
    safe_chinese_segment,
    text_value,
    workbook_headers,
)
from xlsx_localization import read_simple_xlsx, write_simple_xlsx

CHECKPOINT_VERSION = 1
CHECKPOINT_INTERVAL = 500
CANCEL_POLL_INTERVAL = 50


def fingerprint(path: Path | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {"path": str(path or ""), "size": 0, "mtime_ns": 0}
    p = Path(path).resolve()
    st = p.stat()
    return {"path": str(p), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def session_signature(source_xlsx: Path, source_mapping: Path | None,
                      glossary_xlsx: Path, glossary_mapping: Path | None) -> str:
    payload = {
        "source": fingerprint(source_xlsx),
        "source_mapping": fingerprint(source_mapping),
        "glossary": fingerprint(glossary_xlsx),
        "glossary_mapping": fingerprint(glossary_mapping),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def read_checkpoint(path: Path | None, signature: str) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if data.get("version") != CHECKPOINT_VERSION or data.get("signature") != signature:
        return {}
    return data


def write_checkpoint(path: Path | None, data: dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def stop_requested(control_path: Path | None) -> bool:
    if not control_path or not control_path.is_file():
        return False
    try:
        return bool(json.loads(control_path.read_text(encoding="utf-8")).get("stop"))
    except Exception:
        return False


def build_checkpoint(signature: str, processed_rows: int, translated_by_index: dict[str, str],
                     changed_rows: int, partial_rows: int, unchanged_rows: int,
                     blank_rows: int, matched_terms: int, total_rows: int) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "signature": signature,
        "processed_rows": processed_rows,
        "total_rows": total_rows,
        "translated_by_index": translated_by_index,
        "changed_rows": changed_rows,
        "partial_rows": partial_rows,
        "unchanged_rows": unchanged_rows,
        "blank_rows": blank_rows,
        "matched_terms": matched_terms,
    }


def apply_resumable(source_xlsx: Path, source_mapping: Path | None,
                    glossary_xlsx: Path, glossary_mapping: Path | None,
                    output_xlsx: Path, records_path: Path | None,
                    checkpoint_path: Path | None, control_path: Path | None) -> dict[str, Any]:
    if not source_xlsx.is_file():
        raise FileNotFoundError(f"导出 XLSX 不存在：{source_xlsx}")
    if not glossary_xlsx.is_file():
        raise FileNotFoundError(f"术语库 XLSX 不存在：{glossary_xlsx}")

    glossary_pairs, glossary_report = load_glossary(glossary_xlsx, glossary_mapping)
    if not glossary_pairs:
        raise ValueError("术语库没有可用的 原文 → 中文 术语对。请确认术语库已翻译。")
    matcher = LiteralMatcher(glossary_pairs)

    mapping = load_json(source_mapping)
    mapped_rows = mapping_rows(mapping)
    mapped_by_id = {
        text_value(row[0]): text_value(row[2] if len(row) > 2 else row[1])
        for row in mapped_rows if isinstance(row, list) and row
    }
    rows = read_simple_xlsx(source_xlsx)
    headers = workbook_headers(rows, mapping, ["id", "text"])
    id_header = find_header(headers, ID_HEADER_NAMES, headers[0] if headers else "id")
    text_header = find_header(headers, TEXT_HEADER_NAMES, headers[-1] if headers else "text")
    if not text_header:
        raise ValueError("所选导出 XLSX 没有可识别的 text/译文列")

    total_rows = len(rows)
    signature = session_signature(source_xlsx, source_mapping, glossary_xlsx, glossary_mapping)
    checkpoint = read_checkpoint(checkpoint_path, signature)
    start_index = max(0, min(total_rows, int(checkpoint.get("processed_rows") or 0)))
    translated_by_index = {
        str(k): str(v) for k, v in (checkpoint.get("translated_by_index") or {}).items()
        if str(k).isdigit()
    }
    changed_rows = int(checkpoint.get("changed_rows") or 0)
    partial_rows = int(checkpoint.get("partial_rows") or 0)
    unchanged_rows = int(checkpoint.get("unchanged_rows") or 0)
    blank_rows = int(checkpoint.get("blank_rows") or 0)
    matched_terms_total = int(checkpoint.get("matched_terms") or 0)

    # Rebuild the in-memory workbook from the immutable source plus checkpointed
    # safe translations.  The checkpoint therefore survives app restarts and
    # does not depend on an intermediate XLSX being intact.
    translated_segments: dict[str, str] = {}
    for index_text, value in translated_by_index.items():
        index = int(index_text)
        if 1 <= index <= total_rows:
            rows[index - 1][text_header] = value
            row_id = text_value(rows[index - 1].get(id_header or "id", ""))
            if row_id:
                translated_segments[row_id] = value

    preview_ctx = _preview_context(mapping, records_path)
    batch_changed_ids: set[str] = set()
    examples: list[dict[str, Any]] = []

    if start_index:
        emit({
            "event": "progress", "phase": "glossary-translate",
            "percent": min(90, 5 + round(start_index / max(1, total_rows) * 85, 1)),
            "message": f"检测到断点：从 {start_index:,}/{total_rows:,} 继续术语库翻译",
            "completed_rows": start_index, "total_rows": total_rows,
            "translated_rows": changed_rows, "partial_rows": partial_rows,
            "resumed_from": start_index,
        })

    processed_rows = start_index
    canceled = False
    for index in range(start_index + 1, total_rows + 1):
        row = rows[index - 1]
        row_id = text_value(row.get(id_header or "id", ""))
        current_text = text_value(row.get(text_header, ""))
        source_text = current_text or mapped_by_id.get(row_id, "")
        if not source_text:
            blank_rows += 1
        else:
            translated, replacements, applied_terms = matcher.translate(source_text)
            if replacements:
                matched_terms_total += replacements
            if replacements and translated != current_text:
                if safe_chinese_segment(translated):
                    row[text_header] = translated
                    translated_by_index[str(index)] = translated
                    changed_rows += 1
                    if row_id:
                        translated_segments[row_id] = translated
                        batch_changed_ids.add(row_id)
                    if len(examples) < 30:
                        examples.append({"row": index + 1, "id": row_id, "before": source_text, "after": translated, "terms": applied_terms[:8]})
                else:
                    partial_rows += 1
            else:
                unchanged_rows += 1
        processed_rows = index

        should_checkpoint = (index % CHECKPOINT_INTERVAL == 0 or index == total_rows)
        should_poll_cancel = (index % CANCEL_POLL_INTERVAL == 0 or index == total_rows)
        if should_checkpoint:
            write_checkpoint(checkpoint_path, build_checkpoint(
                signature, processed_rows, translated_by_index, changed_rows,
                partial_rows, unchanged_rows, blank_rows, matched_terms_total, total_rows,
            ))
            updates = make_live_updates(preview_ctx, batch_changed_ids, translated_segments)
            batch_changed_ids.clear()
            emit({
                "event": "progress", "phase": "glossary-translate",
                "percent": min(90, 5 + round(index / max(1, total_rows) * 85, 1)),
                "message": f"正在应用术语库：{index:,}/{total_rows:,} · 已完成 {changed_rows:,} · 部分命中 {partial_rows:,}",
                "completed_rows": index, "total_rows": total_rows,
                "translated_rows": changed_rows, "partial_rows": partial_rows,
                "updates": updates,
                "samples": [item["after"] for item in examples[-3:]],
            })
        if should_poll_cancel and stop_requested(control_path):
            # Save immediately, including rows since the last periodic checkpoint.
            write_checkpoint(checkpoint_path, build_checkpoint(
                signature, processed_rows, translated_by_index, changed_rows,
                partial_rows, unchanged_rows, blank_rows, matched_terms_total, total_rows,
            ))
            updates = make_live_updates(preview_ctx, batch_changed_ids, translated_segments)
            batch_changed_ids.clear()
            emit({
                "event": "progress", "phase": "glossary-translate",
                "percent": min(90, 5 + round(processed_rows / max(1, total_rows) * 85, 1)),
                "message": f"已收到停止请求，正在保存 {processed_rows:,}/{total_rows:,} 的断点和已完成译文…",
                "completed_rows": processed_rows, "total_rows": total_rows,
                "translated_rows": changed_rows, "partial_rows": partial_rows,
                "updates": updates, "stopping": True,
            })
            canceled = True
            break

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    write_simple_xlsx(output_xlsx, rows, headers=headers, sheet_name="本土化")
    if canceled:
        write_checkpoint(checkpoint_path, build_checkpoint(
            signature, processed_rows, translated_by_index, changed_rows,
            partial_rows, unchanged_rows, blank_rows, matched_terms_total, total_rows,
        ))

    return {
        "mode": "glossary-xlsx-translate-resumable-v3",
        "source_xlsx": str(source_xlsx.resolve()),
        "source_mapping": str(source_mapping or ""),
        "glossary_xlsx": str(glossary_xlsx.resolve()),
        "output_xlsx": str(output_xlsx.resolve()),
        "checkpoint": str(checkpoint_path or ""),
        "canceled": canceled,
        "resumable": canceled,
        "resumed_from": start_index,
        "processed_rows": processed_rows,
        "workbook_rows": total_rows,
        "translated_rows": changed_rows,
        "partial_rows": partial_rows,
        "unchanged_rows": unchanged_rows,
        "blank_rows": blank_rows,
        "matched_terms": matched_terms_total,
        "mapping_mode": mapping.get("mode", ""),
        "mapping_version": mapping.get("version", ""),
        "paks": mapping.get("paks") or sorted({item[1] for item in mapping.get("records", []) if isinstance(item, list) and len(item) > 1}),
        "examples": examples,
        **glossary_report,
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (9,):
        emit({"event": "error", "message": "Usage: glossary_xlsx_translate_resumable.py <source_xlsx> <source_mapping_json> <glossary_xlsx> <glossary_mapping_json_or_empty> <output_xlsx> <records_json> <checkpoint_json> <control_json>"})
        return 2
    try:
        source_xlsx = Path(argv[1])
        source_mapping = Path(argv[2]) if argv[2] else None
        glossary_xlsx = Path(argv[3])
        glossary_mapping = Path(argv[4]) if argv[4] else None
        output_xlsx = Path(argv[5])
        records_path = Path(argv[6]) if argv[6] else None
        checkpoint_path = Path(argv[7]) if argv[7] else None
        control_path = Path(argv[8]) if argv[8] else None
        emit({"event": "progress", "phase": "glossary-translate", "percent": 2, "message": "正在建立高速术语索引并检查断点…"})
        report = apply_resumable(
            source_xlsx, source_mapping, glossary_xlsx, glossary_mapping,
            output_xlsx, records_path, checkpoint_path, control_path,
        )
        emit({"event": "done", "report": report})
        return 0
    except Exception as exc:
        emit({"event": "error", "message": str(exc), "trace": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
