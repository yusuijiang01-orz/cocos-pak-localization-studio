#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply a translated term glossary workbook to an exported localization XLSX.

This is intentionally deterministic and offline: it never calls an LLM.  It
rewrites only the editable `text` column of the selected exported workbook, so
existing Studio import/build validators remain the authority for PAK safety.

Do not depend on openpyxl here.  The Studio already ships a small XLSX reader /
writer in xlsx_localization.py; using that keeps this workflow usable in the
existing isolated Python environment.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
import unicodedata
from pathlib import Path
from typing import Any

from xlsx_localization import read_simple_xlsx, write_simple_xlsx

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PAIR_SPLIT_RE = re.compile(r"\s*(?:=>|=|：|:)\s*", re.UNICODE)


TEXT_HEADER_NAMES = {"text", "译文", "中文", "translation", "translated", "target", "textzh", "textcn"}
ID_HEADER_NAMES = {"id", "编号", "序号"}
SOURCE_HEADER_NAMES = {"source", "src", "原文", "源文", "术语", "term", "text", "viet", "vi", "越南文"}
TARGET_HEADER_NAMES = {"target", "dst", "translation", "translated", "zh", "cn", "中文", "译文", "textzh", "textcn"}


def emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value)).strip()


def norm_header(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", text_value(value).casefold())


def norm_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or "")).strip()).casefold()


def contains_cjk(value: str) -> bool:
    return bool(CJK_RE.search(value or ""))


def load_json(path: Path | None) -> dict[str, Any]:
    if not path or not str(path) or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def column_index(headers: list[Any], names: set[str], fallback: int | None = None) -> int | None:
    normalized = [norm_header(h) for h in headers]
    for index, header in enumerate(normalized, 1):
        if header in names:
            return index
    return fallback


def mapping_rows(mapping: dict[str, Any]) -> list[list[Any]]:
    rows = mapping.get("rows")
    return rows if isinstance(rows, list) else []


def load_table(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    rows = read_simple_xlsx(path)
    if not rows:
        return [], []
    headers = [key for key in rows[0].keys() if key != "_values"]
    return headers, rows


def find_text_column(headers: list[Any]) -> int:
    idx = column_index(headers, TEXT_HEADER_NAMES)
    if idx:
        return idx
    return max(1, len(headers))


def find_id_column(headers: list[Any]) -> int:
    return column_index(headers, ID_HEADER_NAMES, fallback=1) or 1


def row_cell(row: dict[str, Any], index: int) -> str:
    values = row.get("_values") or []
    if index >= 1 and index <= len(values):
        return text_value(values[index - 1])
    return ""


def row_as_output(row: dict[str, Any], headers: list[str]) -> dict[str, str]:
    values = row.get("_values") or []
    return {header: text_value(values[i] if i < len(values) else row.get(header, "")) for i, header in enumerate(headers)}


def load_glossary_from_pairs(rows: list[dict[str, Any]], headers: list[Any]) -> dict[str, str]:
    source_col = column_index(headers, SOURCE_HEADER_NAMES, fallback=1) or 1
    target_col = column_index(headers, TARGET_HEADER_NAMES)
    if not target_col and len(headers) >= 2:
        target_col = 2 if source_col != 2 else 1
    pairs: dict[str, str] = {}
    for row in rows:
        source = row_cell(row, source_col)
        target = row_cell(row, target_col) if target_col else ""
        if not target and source:
            parts = PAIR_SPLIT_RE.split(source, maxsplit=1)
            if len(parts) == 2:
                source, target = text_value(parts[0]), text_value(parts[1])
        if source and target and norm_key(source) != norm_key(target) and contains_cjk(target):
            pairs[source] = target
    return pairs


def load_glossary_from_mapped_single_column(rows: list[dict[str, Any]], mapping: dict[str, Any], headers: list[Any]) -> dict[str, str]:
    mapped_rows = mapping_rows(mapping)
    text_col = find_text_column(headers)
    pairs: dict[str, str] = {}
    for idx, row in enumerate(rows):
        if idx >= len(mapped_rows):
            break
        mapped = mapped_rows[idx]
        source = text_value(mapped[1] if isinstance(mapped, list) and len(mapped) > 1 else "")
        target = row_cell(row, text_col)
        if source and target and norm_key(source) != norm_key(target) and contains_cjk(target):
            pairs[source] = target
    return pairs


def load_glossary(glossary_xlsx: Path, glossary_mapping: Path | None) -> tuple[dict[str, str], dict[str, Any]]:
    mapping = load_json(glossary_mapping)
    headers, rows = load_table(glossary_xlsx)
    if not headers:
        raise ValueError("术语库 XLSX 为空或没有表头。")
    pairs = load_glossary_from_pairs(rows, headers)
    mapped_pairs: dict[str, str] = {}
    if mapping.get("mode") == "multi-pak-safe-term-glossary":
        mapped_pairs = load_glossary_from_mapped_single_column(rows, mapping, headers)
    # Explicit two-column pairs win; mapped single-column pairs make the current
    # exported one-column glossary usable after Google/Ollama translation.
    merged = {**mapped_pairs, **pairs}
    by_key: set[str] = set()
    deduped: dict[str, str] = {}
    for source, target in sorted(merged.items(), key=lambda item: len(item[0]), reverse=True):
        key = norm_key(source)
        if not key or key in by_key:
            continue
        by_key.add(key)
        deduped[source] = target
    report = {
        "glossary_rows": len(rows),
        "glossary_terms": len(deduped),
        "glossary_mapping": str(glossary_mapping or ""),
    }
    return deduped, report


def compile_terms(pairs: dict[str, str]) -> list[tuple[re.Pattern[str], str, str]]:
    terms: list[tuple[re.Pattern[str], str, str]] = []
    for source, target in sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True):
        source = text_value(source)
        target = text_value(target)
        if not source or not target:
            continue
        terms.append((re.compile(re.escape(source), re.IGNORECASE), source, target))
    return terms


def translate_cell(value: str, terms: list[tuple[re.Pattern[str], str, str]]) -> tuple[str, int, list[str]]:
    current = text_value(value)
    if not current:
        return current, 0, []
    applied: list[str] = []
    count = 0
    # Fast exact match before substring replacement.
    for _pattern, source, target in terms:
        if norm_key(current) == norm_key(source):
            return target, 1, [source]
    for pattern, source, target in terms:
        current, replacements = pattern.subn(target, current)
        if replacements:
            count += replacements
            applied.append(source)
    return current, count, applied


def mapped_export_text_by_id(mapping: dict[str, Any]) -> dict[str, str]:
    mode = str(mapping.get("mode") or "")
    result: dict[str, str] = {}
    for row in mapping_rows(mapping):
        if not isinstance(row, list) or len(row) < 2:
            continue
        row_id = text_value(row[0])
        if not row_id:
            continue
        # v7 multi-PAK mapping rows are [id, source, exported].
        # v6 compact rows are [id, source, cells].  The source text is the safe
        # fallback for v6; never stringify the cell metadata list into a text cell.
        if mode == "multi-pak-out-of-band-skeleton" and len(row) > 2:
            result[row_id] = text_value(row[2])
        else:
            result[row_id] = text_value(row[1])
    return result


def apply_glossary(source_xlsx: Path, source_mapping: Path | None,
                   glossary_xlsx: Path, glossary_mapping: Path | None,
                   output_xlsx: Path) -> dict[str, Any]:
    if not source_xlsx.is_file():
        raise FileNotFoundError(f"导出 XLSX 不存在：{source_xlsx}")
    if not glossary_xlsx.is_file():
        raise FileNotFoundError(f"术语库 XLSX 不存在：{glossary_xlsx}")

    glossary_pairs, glossary_report = load_glossary(glossary_xlsx, glossary_mapping)
    if not glossary_pairs:
        raise ValueError("术语库没有可用的 原文 → 中文 术语对。请确认术语库已翻译，或添加第二列中文译文。")
    terms = compile_terms(glossary_pairs)

    mapping = load_json(source_mapping)
    mapped_by_id = mapped_export_text_by_id(mapping)
    headers, rows = load_table(source_xlsx)
    if not headers:
        raise ValueError("导出 XLSX 为空或没有表头。")
    id_col = find_id_column(headers)
    text_col = find_text_column(headers)
    if text_col < 1 or text_col > len(headers):
        raise ValueError("找不到导出 XLSX 的 text/译文列。")

    output_rows: list[dict[str, str]] = []
    total_rows = changed_rows = unchanged_rows = blank_rows = 0
    matched_terms_total = 0
    examples: list[dict[str, Any]] = []
    denominator = max(1, len(rows))

    for row_number, row in enumerate(rows, 2):
        total_rows += 1
        out = row_as_output(row, headers)
        id_value = row_cell(row, id_col)
        current_text = row_cell(row, text_col)
        source_text = current_text or mapped_by_id.get(id_value, "")
        if not source_text:
            blank_rows += 1
            output_rows.append(out)
            continue
        translated, replacements, applied_terms = translate_cell(source_text, terms)
        if replacements and translated != current_text:
            out[headers[text_col - 1]] = translated
            changed_rows += 1
            matched_terms_total += replacements
            if len(examples) < 30:
                examples.append({
                    "row": row_number,
                    "id": id_value,
                    "before": source_text,
                    "after": translated,
                    "terms": applied_terms[:8],
                })
        else:
            unchanged_rows += 1
        output_rows.append(out)
        if total_rows % 1000 == 0:
            emit({
                "event": "progress",
                "phase": "glossary-translate",
                "percent": min(90, 10 + round(total_rows / denominator * 80, 1)),
                "message": f"正在应用术语库：{total_rows:,}/{len(rows):,}",
            })

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    write_simple_xlsx(output_xlsx, output_rows, headers=headers)
    return {
        "mode": "glossary-xlsx-translate",
        "source_xlsx": str(source_xlsx.resolve()),
        "source_mapping": str(source_mapping or ""),
        "glossary_xlsx": str(glossary_xlsx.resolve()),
        "output_xlsx": str(output_xlsx.resolve()),
        "mapping_mode": mapping.get("mode", ""),
        "mapping_version": mapping.get("version", ""),
        "paks": mapping.get("paks") or sorted({item[1] for item in mapping.get("records", []) if isinstance(item, list) and len(item) > 1}),
        "workbook_rows": total_rows,
        "translated_rows": changed_rows,
        "unchanged_rows": unchanged_rows,
        "blank_rows": blank_rows,
        "matched_terms": matched_terms_total,
        "examples": examples,
        **glossary_report,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        emit({"event": "error", "message": "Usage: glossary_xlsx_translate.py <source_xlsx> <source_mapping_json> <glossary_xlsx> <glossary_mapping_json_or_empty> <output_xlsx>"})
        return 2
    try:
        source_xlsx = Path(argv[1])
        source_mapping = Path(argv[2]) if argv[2] else None
        glossary_xlsx = Path(argv[3])
        glossary_mapping = Path(argv[4]) if argv[4] else None
        output_xlsx = Path(argv[5])
        emit({"event": "progress", "phase": "glossary-translate", "percent": 3, "message": "正在读取导出 XLSX 和术语库 XLSX…"})
        report = apply_glossary(source_xlsx, source_mapping, glossary_xlsx, glossary_mapping, output_xlsx)
        emit({"event": "done", "report": report})
        return 0
    except Exception as exc:
        emit({"event": "error", "message": str(exc), "trace": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
