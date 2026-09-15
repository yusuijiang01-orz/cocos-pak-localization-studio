#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply a translated term glossary workbook to an exported localization XLSX.

The workflow is deterministic/offline and deliberately reuses Studio's own
lightweight XLSX reader/writer. Translation matching uses an Aho-Corasick
literal matcher, so runtime is close to O(total text length + matches) instead
of O(workbook rows * glossary terms).

For multi-PAK v7 exports the script can also read text_records.json and emit
live record updates while processing. The Electron UI consumes those events so
language/status counters visibly change during glossary translation. Only
segments that become safe Chinese-only text are written to the translated XLSX;
partial Chinese/Vietnamese substitutions are reported but not imported.
"""
from __future__ import annotations

from collections import deque
import json
import re
import sys
import traceback
import unicodedata
from pathlib import Path
from typing import Any

from xlsx_localization import read_simple_xlsx, write_simple_xlsx, _out_of_band_skeleton

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]")
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


def safe_chinese_segment(value: str) -> bool:
    value = text_value(value)
    return bool(value and contains_cjk(value) and not LATIN_RE.search(value))


def load_json(path: Path | None) -> Any:
    if not path or not str(path) or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def workbook_headers(rows: list[dict], mapping: dict[str, Any], fallback: list[str]) -> list[str]:
    mapped = mapping.get("headers")
    if isinstance(mapped, list) and mapped:
        return [str(x) for x in mapped]
    if rows:
        keys = [str(k) for k in rows[0].keys() if k != "_values"]
        if keys:
            return keys
    return list(fallback)


def find_header(headers: list[str], names: set[str], fallback: str | None = None) -> str | None:
    wanted = {norm_header(x) for x in names}
    for header in headers:
        if norm_header(header) in wanted:
            return header
    return fallback


def mapping_rows(mapping: dict[str, Any]) -> list[list[Any]]:
    rows = mapping.get("rows")
    return rows if isinstance(rows, list) else []


def load_glossary(glossary_xlsx: Path, glossary_mapping: Path | None) -> tuple[dict[str, str], dict[str, Any]]:
    mapping = load_json(glossary_mapping)
    rows = read_simple_xlsx(glossary_xlsx)
    headers = workbook_headers(rows, mapping, ["text"])
    source_header = find_header(headers, SOURCE_HEADER_NAMES, headers[0] if headers else "text")
    target_header = find_header(headers, TARGET_HEADER_NAMES)
    pairs: dict[str, str] = {}
    if source_header and target_header and source_header != target_header:
        for row in rows:
            source = text_value(row.get(source_header, ""))
            target = text_value(row.get(target_header, ""))
            if source and target and norm_key(source) != norm_key(target) and contains_cjk(target):
                pairs[source] = target
    if mapping.get("mode") == "multi-pak-safe-term-glossary":
        mapped = mapping_rows(mapping)
        text_header = find_header(headers, TEXT_HEADER_NAMES, headers[-1] if headers else "text")
        for index, row in enumerate(rows):
            if index >= len(mapped):
                break
            meta = mapped[index]
            source = text_value(meta[1] if len(meta) > 1 else "")
            target = text_value(row.get(text_header or "text", ""))
            if source and target and norm_key(source) != norm_key(target) and contains_cjk(target):
                pairs.setdefault(source, target)
    if not pairs and source_header:
        for row in rows:
            raw = text_value(row.get(source_header, ""))
            parts = PAIR_SPLIT_RE.split(raw, maxsplit=1)
            if len(parts) == 2:
                source, target = text_value(parts[0]), text_value(parts[1])
                if source and target and norm_key(source) != norm_key(target) and contains_cjk(target):
                    pairs[source] = target
    deduped: dict[str, str] = {}
    seen: set[str] = set()
    for source, target in sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True):
        key = norm_key(source)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped[source] = target
    return deduped, {"glossary_rows": len(rows), "glossary_terms": len(deduped), "glossary_mapping": str(glossary_mapping or "")}


class LiteralMatcher:
    def __init__(self, pairs: dict[str, str]):
        self.next: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[tuple[int, str, str]]] = [[]]
        self.exact = {norm_key(source): target for source, target in pairs.items()}
        for source, target in pairs.items():
            key = unicodedata.normalize("NFC", source).casefold()
            if not key:
                continue
            state = 0
            for ch in key:
                nxt = self.next[state].get(ch)
                if nxt is None:
                    nxt = len(self.next)
                    self.next[state][ch] = nxt
                    self.next.append({})
                    self.fail.append(0)
                    self.out.append([])
                state = nxt
            self.out[state].append((len(key), source, target))
        queue: deque[int] = deque(self.next[0].values())
        while queue:
            state = queue.popleft()
            for ch, nxt in self.next[state].items():
                queue.append(nxt)
                fallback = self.fail[state]
                while fallback and ch not in self.next[fallback]:
                    fallback = self.fail[fallback]
                self.fail[nxt] = self.next[fallback].get(ch, 0)
                self.out[nxt].extend(self.out[self.fail[nxt]])

    def translate(self, value: str) -> tuple[str, int, list[str]]:
        current = text_value(value)
        if not current:
            return current, 0, []
        exact = self.exact.get(norm_key(current))
        if exact is not None:
            return exact, 1, [current]
        folded = unicodedata.normalize("NFC", current).casefold()
        state = 0
        matches: list[tuple[int, int, str, str]] = []
        for end, ch in enumerate(folded):
            while state and ch not in self.next[state]:
                state = self.fail[state]
            state = self.next[state].get(ch, 0)
            for length, source, target in self.out[state]:
                start = end - length + 1
                if start >= 0:
                    matches.append((start, end + 1, source, target))
        if not matches:
            return current, 0, []
        matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
        chosen: list[tuple[int, int, str, str]] = []
        cursor = 0
        for match in matches:
            if match[0] < cursor:
                continue
            chosen.append(match)
            cursor = match[1]
        out: list[str] = []
        cursor = 0
        applied: list[str] = []
        for start, end, source, target in chosen:
            out.append(current[cursor:start])
            out.append(target)
            applied.append(source)
            cursor = end
        out.append(current[cursor:])
        return "".join(out), len(chosen), applied


def _preview_context(mapping: dict[str, Any], records_path: Path | None):
    if mapping.get("mode") != "multi-pak-out-of-band-skeleton" or int(mapping.get("version") or 0) != 7:
        return None
    if not records_path or not records_path.is_file():
        return None
    current_records = load_json(records_path)
    if not isinstance(current_records, list):
        return None
    current_by_id = {str(rec.get("id")): rec for rec in current_records}
    mapped_records = mapping.get("records") or []
    segment_to_records: dict[str, list[int]] = {}
    for idx, item in enumerate(mapped_records):
        if not isinstance(item, list) or len(item) < 7:
            continue
        for piece in item[6] or []:
            if isinstance(piece, (list, tuple)) and len(piece) >= 3 and piece[0] == "t":
                segment_to_records.setdefault(str(piece[1]), []).append(idx)
    return current_by_id, mapped_records, segment_to_records


def _record_preview(item: list[Any], current_by_id: dict[str, dict], translated_segments: dict[str, str]) -> dict[str, str] | None:
    if len(item) < 7:
        return None
    record_id = str(item[0])
    source = str(item[5] or "")
    skeleton = item[6] or []
    current = str((current_by_id.get(record_id) or {}).get("original") or source)
    current_skeleton = _out_of_band_skeleton(current)
    source_literals = [piece[1] for piece in skeleton if piece and piece[0] in ("k", "p")]
    current_literals = [piece[1] for piece in current_skeleton if piece and piece[0] in ("k", "p")]
    current_texts = [piece[2] for piece in current_skeleton if piece and piece[0] == "t"]
    source_text_count = sum(1 for piece in skeleton if piece and piece[0] == "t")
    preserve_current = source_literals == current_literals and len(current_texts) == source_text_count
    out: list[str] = []
    text_index = 0
    changed = False
    for piece in skeleton:
        if not piece:
            continue
        kind = piece[0]
        if kind in ("k", "p"):
            out.append(str(piece[1]))
            continue
        if kind != "t" or len(piece) < 3:
            continue
        segment_id = str(piece[1])
        source_segment = str(piece[2])
        previous = current_texts[text_index] if preserve_current else source_segment
        value = translated_segments.get(segment_id, previous)
        if segment_id in translated_segments and value != previous:
            changed = True
        out.append(value)
        text_index += 1
    target = "".join(out)
    if not changed or target == current:
        return None
    return {"id": record_id, "text": target}


def make_live_updates(preview_ctx, changed_segment_ids: set[str], translated_segments: dict[str, str]) -> list[dict[str, str]]:
    if not preview_ctx or not changed_segment_ids:
        return []
    current_by_id, mapped_records, segment_to_records = preview_ctx
    affected: set[int] = set()
    for segment_id in changed_segment_ids:
        affected.update(segment_to_records.get(segment_id, ()))
    updates: list[dict[str, str]] = []
    for idx in sorted(affected):
        update = _record_preview(mapped_records[idx], current_by_id, translated_segments)
        if update:
            updates.append(update)
    return updates


def apply_glossary(source_xlsx: Path, source_mapping: Path | None, glossary_xlsx: Path,
                   glossary_mapping: Path | None, output_xlsx: Path,
                   records_path: Path | None = None) -> dict[str, Any]:
    if not source_xlsx.is_file():
        raise FileNotFoundError(f"导出 XLSX 不存在：{source_xlsx}")
    if not glossary_xlsx.is_file():
        raise FileNotFoundError(f"术语库 XLSX 不存在：{glossary_xlsx}")
    glossary_pairs, glossary_report = load_glossary(glossary_xlsx, glossary_mapping)
    if not glossary_pairs:
        raise ValueError("术语库没有可用的 原文 → 中文 术语对。请确认术语库已翻译，或添加第二列中文译文。")
    matcher = LiteralMatcher(glossary_pairs)
    mapping = load_json(source_mapping)
    mapped_rows = mapping_rows(mapping)
    mapped_by_id = {text_value(row[0]): text_value(row[2] if len(row) > 2 else row[1]) for row in mapped_rows if isinstance(row, list) and row}
    rows = read_simple_xlsx(source_xlsx)
    headers = workbook_headers(rows, mapping, ["id", "text"])
    id_header = find_header(headers, ID_HEADER_NAMES, headers[0] if headers else "id")
    text_header = find_header(headers, TEXT_HEADER_NAMES, headers[-1] if headers else "text")
    if not text_header:
        raise ValueError("所选导出 XLSX 没有可识别的 text/译文列")
    preview_ctx = _preview_context(mapping, records_path)
    translated_segments: dict[str, str] = {}
    batch_changed_ids: set[str] = set()
    total_rows = len(rows)
    changed_rows = unchanged_rows = blank_rows = partial_rows = 0
    matched_terms_total = 0
    examples: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        row_id = text_value(row.get(id_header or "id", ""))
        current_text = text_value(row.get(text_header, ""))
        source_text = current_text or mapped_by_id.get(row_id, "")
        if not source_text:
            blank_rows += 1
            continue
        translated, replacements, applied_terms = matcher.translate(source_text)
        if replacements:
            matched_terms_total += replacements
        if replacements and translated != current_text:
            if safe_chinese_segment(translated):
                row[text_header] = translated
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
        if index % 1000 == 0 or index == total_rows:
            updates = make_live_updates(preview_ctx, batch_changed_ids, translated_segments)
            batch_changed_ids.clear()
            emit({"event": "progress", "phase": "glossary-translate", "percent": min(90, 5 + round(index / max(1, total_rows) * 85, 1)), "message": f"正在应用术语库：{index:,}/{total_rows:,} · 已完成 {changed_rows:,} · 部分命中 {partial_rows:,}", "completed_rows": index, "total_rows": total_rows, "translated_rows": changed_rows, "partial_rows": partial_rows, "updates": updates, "samples": [item["after"] for item in examples[-3:]]})
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    write_simple_xlsx(output_xlsx, rows, headers=headers, sheet_name="本土化")
    return {"mode": "glossary-xlsx-translate-fast-v2", "source_xlsx": str(source_xlsx.resolve()), "source_mapping": str(source_mapping or ""), "glossary_xlsx": str(glossary_xlsx.resolve()), "output_xlsx": str(output_xlsx.resolve()), "mapping_mode": mapping.get("mode", ""), "mapping_version": mapping.get("version", ""), "paks": mapping.get("paks") or sorted({item[1] for item in mapping.get("records", []) if isinstance(item, list) and len(item) > 1}), "workbook_rows": total_rows, "translated_rows": changed_rows, "partial_rows": partial_rows, "unchanged_rows": unchanged_rows, "blank_rows": blank_rows, "matched_terms": matched_terms_total, "examples": examples, **glossary_report}


def main(argv: list[str]) -> int:
    if len(argv) not in (6, 7):
        emit({"event": "error", "message": "Usage: glossary_xlsx_translate.py <source_xlsx> <source_mapping_json> <glossary_xlsx> <glossary_mapping_json_or_empty> <output_xlsx> [records_json]"})
        return 2
    try:
        source_xlsx = Path(argv[1])
        source_mapping = Path(argv[2]) if argv[2] else None
        glossary_xlsx = Path(argv[3])
        glossary_mapping = Path(argv[4]) if argv[4] else None
        output_xlsx = Path(argv[5])
        records_path = Path(argv[6]) if len(argv) == 7 and argv[6] else None
        emit({"event": "progress", "phase": "glossary-translate", "percent": 2, "message": "正在建立高速术语索引…"})
        report = apply_glossary(source_xlsx, source_mapping, glossary_xlsx, glossary_mapping, output_xlsx, records_path)
        emit({"event": "done", "report": report})
        return 0
    except Exception as exc:
        emit({"event": "error", "message": str(exc), "trace": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
