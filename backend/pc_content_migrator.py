#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
from pathlib import Path

from script_translator import incomplete_translation
from tsv_localization import validate_translation
from localization_tm import exchange_text, validate_tokens


NUMERIC_KEY = re.compile(rb"[+-]?\d+")
ASCII_STABLE_KEY = re.compile(rb"[A-Za-z0-9_./\\:@+\-]{1,160}")
TEXT_EXTS = {".tsv", ".ini", ".txt"}
METHOD_PRIORITY = {
    "hash_locator_unique": 0,
    "tsv_primary_key": 1,
    "cross_file_composite_key_settings": 2,
    "cross_file_composite_key": 3,
    "cross_file_primary_key": 4,
    "cross_file_row_signature": 5,
    "tsv_row_signature": 6,
    "ini_section_key": 7,
    "strict_locator": 8,
    "ini_key_occurrence": 9,
    "same_hash_near_order": 10,
    "same_hash_locator_soft": 11,
    "context_sequence": 12,
    "exact_text_memory": 13,
    "deterministic_glossary": 14,
    "semantic_pc_content": 15,
}
TRUSTED_CONTENT_ANCHORS = {
    "tsv_primary_key", "cross_file_composite_key_settings", "cross_file_composite_key",
    "cross_file_primary_key",
    "cross_file_row_signature", "context_sequence"
}
RESOURCE_PATH_RE = re.compile(r"(?:^|[=\s])(?:[A-Za-z]:)?[\\/](?:spr|script|ui|settings|maps?|resource)[\\/]", re.I)
VIETNAMESE_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
    r"ÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊÒỎÕỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]"
)
VIETNAMESE_WORD_RE = re.compile(
    r"\b(nhiệm|vụ|kỹ|năng|trang|bị|người|chơi|điểm|thương|phòng|thành|nhận|thưởng|thông|báo)\b",
    re.I,
)
METHOD_CONFIDENCE = {
    "hash_locator_unique": 1.0,
    "strict_locator": 1.0,
    "tsv_primary_key": 0.99,
    "cross_file_composite_key_settings": 0.99,
    "cross_file_composite_key": 0.98,
    "ini_section_key": 0.99,
    "tsv_row_signature": 0.98,
    "cross_file_row_signature": 0.97,
    "cross_file_primary_key": 0.96,
    "context_sequence": 0.95,
    "exact_text_memory": 0.98,
    "ini_key_occurrence": 0.96,
    "same_hash_near_order": 0.95,
    "same_hash_locator_soft": 0.95,
    "semantic_pc_content": 0.80,
    "deterministic_glossary": 0.75,
}

COMPOSITE_ID_HEADERS = {
    "id", "sid", "objid", "genre", "detailtype", "particulartype",
    "type", "subtype", "index", "key", "series", "level",
}


def cjk_count(text: str) -> int:
    return sum("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def acceptable_pc_text(text: str) -> bool:
    text = str(text or "")
    if not text or "\ufffd" in text or cjk_count(text) == 0:
        return False
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        return False
    # PC resources also contain a small number of half-decoded legacy strings.
    # Never propagate those into the cleaner mobile resource set.
    if incomplete_translation(text):
        return False
    return True


def needs_pc_recovery(record: dict) -> bool:
    """Use current text, not cached language/status, to decide whether PC Chinese helps."""
    current = str(record.get("original", ""))
    if not current.strip():
        return False
    vi = re.search(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]", current)
    return bool(vi) or incomplete_translation(current) or record.get("language") in ("vi", "mixed")


def clean_existing_chinese(record: dict) -> bool:
    """True when the untouched mobile value is already usable Chinese.

    Cross-version numeric IDs are not globally stable. An existing Chinese
    mobile literal is therefore stronger evidence than any PC hash/row match.
    """
    source = str(record.get("source_original", record.get("original", "")) or "")
    if not re.search(r"[\u4e00-\u9fff]", source):
        return False
    if re.search(r"[\u00c0-\u024f\u1e00-\u1eff\uac00-\ud7af\uf900-\ufaff\ue000-\uf8ff\ufffd]", source):
        return False
    return not incomplete_translation(source)


def protected_existing_translation(record: dict) -> bool:
    note = str(record.get("note", ""))
    status = str(record.get("status", ""))
    return "[人工TSV合并]" in note or status in ("人工确认", "已审核")


def should_try_pc_replacement(record: dict, target: str) -> bool:
    current = str(record.get("original", ""))
    if (not current.strip() or current == target
            or protected_existing_translation(record)
            or clean_existing_chinese(record)):
        return False
    if needs_pc_recovery(record):
        return True
    # Google/imported Chinese can be semantically poor even when it is valid CJK.
    # Re-run trusted international matches even for an older PC migration: the
    # reference cache may have been re-analyzed after an encoding-decoder fix.
    # Manual/approved records were already rejected above.
    return cjk_count(current) > 0


def pak_stem(pak_name: str) -> str:
    return Path(str(pak_name)).stem


def extracted_file(root: Path, file_key: tuple[str, str]) -> Path:
    pak, source_file = file_key
    return root / "extracted" / pak_stem(pak) / source_file


def stable_rows(path: Path) -> dict[bytes, int]:
    found: dict[bytes, int] = {}
    duplicate: set[bytes] = set()
    for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
        first = raw.split(b"\t", 1)[0].strip()
        if NUMERIC_KEY.fullmatch(first):
            key = b"N:" + (first.lstrip(b"+0") or b"0")
        elif ASCII_STABLE_KEY.fullmatch(first) and any(65 <= ch <= 90 or 97 <= ch <= 122 or ch in b"_./\\:@" for ch in first):
            key = b"A:" + first.lower()
        else:
            continue
        if key in found:
            duplicate.add(key)
        else:
            found[key] = line_no
    return {key: line for key, line in found.items() if key not in duplicate}


def stable_row_signatures(path: Path) -> dict[tuple, int]:
    """Index rows by unique combinations of non-language numeric/code cells."""
    found: dict[tuple, int] = {}
    duplicate: set[tuple] = set()
    for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
        stable = []
        for column, cell in enumerate(raw.split(b"\t"), 1):
            value = cell.strip()
            if NUMERIC_KEY.fullmatch(value):
                normalized = b"N:" + (value.lstrip(b"+0") or b"0")
            elif ASCII_STABLE_KEY.fullmatch(value) and any(ch in b"_./\\:@" for ch in value):
                normalized = b"A:" + value.lower()
            else:
                continue
            stable.append((column, normalized))
        signature = tuple(stable)
        if len(signature) < 2:
            continue
        if signature in found:
            duplicate.add(signature)
        else:
            found[signature] = line_no
    return {key: line for key, line in found.items() if key not in duplicate}


def structure(path: Path):
    data = path.read_bytes()
    lines = data.splitlines()
    ext = path.suffix.lower()
    if ext == ".tsv":
        return ext, len(lines), tuple(line.count(b"\t") + 1 for line in lines)
    if ext == ".ini":
        keys = tuple(
            line.split(b"=", 1)[0].strip().lower()
            for line in lines
            if b"=" in line and not line.lstrip().startswith((b";", b"#"))
        )
        return ext, len(lines), keys
    if ext == ".txt":
        return ext, len(lines), tuple(line.count(b"\t") + 1 for line in lines)
    return ext, len(data)


def ini_section_key_lines(path: Path) -> dict[int, tuple[str, str]]:
    """Map INI value lines to their section/key anchor."""
    anchors: dict[int, tuple[str, str]] = {}
    section = ""
    for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b";", b"#")):
            continue
        if stripped.startswith(b"[") and stripped.endswith(b"]"):
            section = stripped[1:-1].decode("utf-8", errors="ignore").strip().lower()
            continue
        if b"=" not in raw:
            continue
        key = raw.split(b"=", 1)[0].strip().decode("utf-8", errors="ignore").lower()
        if key:
            anchors[line_no] = (section, key)
    return anchors


def unique_ini_anchors(path: Path) -> dict[tuple[str, str], int]:
    lines = ini_section_key_lines(path)
    counts = collections.Counter(lines.values())
    return {anchor: line for line, anchor in lines.items() if counts[anchor] == 1}


def records_by_file(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[(str(record.get("pak", "")), str(record.get("source_file", "")))].append(record)
    return grouped


def files_by_hash(grouped: dict[tuple[str, str], list[dict]]):
    result: dict[str, list[tuple[tuple[str, str], list[dict]]]] = collections.defaultdict(list)
    for key, records in grouped.items():
        if records:
            result[str(records[0].get("hash", ""))].append((key, records))
    return result


def normalized_content_anchor(text: str) -> str:
    text = str(text or "").strip()
    if len(text) < 2 or cjk_count(text) == 0 or incomplete_translation(text):
        return ""
    return re.sub(r"\s+", "", exchange_text(text))


def resource_path_anchor(text: str) -> str:
    text = str(text or "").strip().replace("/", "\\").lower()
    return text if RESOURCE_PATH_RE.search(" " + text) else ""


def language_bucket(text: str) -> str:
    value = str(text or "")
    has_han = cjk_count(value) > 0
    has_vi = bool(VIETNAMESE_RE.search(value) or VIETNAMESE_WORD_RE.search(value))
    if has_han and has_vi:
        return "mixed"
    if has_han:
        return "zh"
    if has_vi:
        return "vi"
    return "other"


def coverage_summary(records: list[dict], pak_scope: str = "") -> dict:
    scoped = [r for r in records if not pak_scope or str(r.get("pak", "")) == pak_scope]
    counts = collections.Counter(language_bucket(r.get("original", "")) for r in scoped)
    return {
        "total": len(scoped),
        "zh": counts["zh"],
        "vi": counts["vi"],
        "mixed": counts["mixed"],
        "other": counts["other"],
        "percent": round(counts["zh"] * 100 / max(1, len(scoped)), 2),
    }


def cross_file_relations(pc_grouped, mobile_grouped, pc_root: Path, mobile_root: Path):
    """Find split/merged TSV relations without relying on file-name hashes."""
    pc_text_files = collections.defaultdict(set)
    pc_path_files = collections.defaultdict(set)
    pc_rows_cache = {}
    for file_key, records in pc_grouped.items():
        if Path(file_key[1]).suffix.lower() != ".tsv":
            continue
        for record in records:
            text = str(record.get("original", ""))
            anchor = normalized_content_anchor(text)
            if anchor:
                pc_text_files[anchor].add(file_key)
            path_anchor = resource_path_anchor(text)
            if path_anchor:
                pc_path_files[path_anchor].add(file_key)

    relations = []
    for mobile_key, mobile_records in mobile_grouped.items():
        if Path(mobile_key[1]).suffix.lower() != ".tsv":
            continue
        evidence = collections.defaultdict(lambda: collections.Counter())
        for record in mobile_records:
            text = str(record.get("original", ""))
            anchor = normalized_content_anchor(text)
            for pc_key in pc_text_files.get(anchor, ()):
                evidence[pc_key]["exact_text"] += 1
            path_anchor = resource_path_anchor(text)
            for pc_key in pc_path_files.get(path_anchor, ()):
                evidence[pc_key]["resource_path"] += 1

        mobile_path = extracted_file(mobile_root, mobile_key)
        if not mobile_path.is_file():
            continue
        mobile_rows = stable_rows(mobile_path)
        for pc_key, counts in evidence.items():
            pc_path = extracted_file(pc_root, pc_key)
            if not pc_path.is_file():
                continue
            pc_rows = pc_rows_cache.setdefault(pc_key, stable_rows(pc_path))
            common_ids = len(set(mobile_rows) & set(pc_rows))
            anchor_strength = counts["exact_text"] + counts["resource_path"] * 2
            # Cross-hash relationships need independent content evidence. This
            # avoids pairing unrelated tables that both number rows from 1.
            if common_ids < 3 or not (
                counts["exact_text"] >= 2
                or counts["resource_path"] >= 1
                or anchor_strength >= 4
            ):
                continue
            confidence = min(0.99, 0.93 + min(0.04, anchor_strength * 0.005) + min(0.02, common_ids / 1000))
            relations.append({
                "mobile_key": mobile_key,
                "pc_key": pc_key,
                "mobile_rows": mobile_rows,
                "pc_rows": pc_rows,
                "common_ids": common_ids,
                "exact_text": counts["exact_text"],
                "resource_path": counts["resource_path"],
                "confidence": round(confidence, 4),
            })
    return relations


def decode_header_cells(path: Path) -> list[str]:
    lines = path.read_bytes().splitlines()
    if not lines:
        return []
    result = []
    for raw in lines[0].split(b"\t"):
        for encoding in ("utf-8-sig", "gb18030", "cp1258", "latin1"):
            try:
                result.append(raw.decode(encoding).strip())
                break
            except UnicodeDecodeError:
                continue
        else:
            result.append(raw.decode("utf-8", errors="ignore").strip())
    return result


def unique_header_columns(path: Path) -> dict[str, int]:
    columns = {}
    duplicates = set()
    for column, header in enumerate(decode_header_cells(path), 1):
        key = re.sub(r"\s+", "", header).lower()
        if not key:
            continue
        if key in columns:
            duplicates.add(key)
        else:
            columns[key] = column
    return {key: column for key, column in columns.items() if key not in duplicates}


def composite_key_rows(path: Path, key_headers: tuple[str, ...]) -> dict[tuple[str, ...], int]:
    """Return unique TSV rows indexed by shared, non-language identity columns."""
    headers = unique_header_columns(path)
    columns = [headers.get(header) for header in key_headers]
    if not columns or any(column is None for column in columns):
        return {}
    found: dict[tuple[str, ...], int] = {}
    duplicates = set()
    lines = path.read_bytes().splitlines()
    for line_no, raw in enumerate(lines[1:], 2):
        cells = raw.split(b"\t")
        values = []
        for column in columns:
            if column > len(cells):
                values = []
                break
            value = decode_header_cells_from_bytes(cells[column - 1]).strip().lower()
            if not value or not re.fullmatch(r"[a-z0-9_./\\:+-]+", value):
                values = []
                break
            if re.fullmatch(r"[+-]?\d+", value):
                value = value.lstrip("+0") or "0"
            values.append(value)
        if not values:
            continue
        key = tuple(values)
        if key in found:
            duplicates.add(key)
        else:
            found[key] = line_no
    return {key: line for key, line in found.items() if key not in duplicates}


def decode_header_cells_from_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "cp1258", "latin1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def shared_composite_headers(mobile_path: Path, pc_path: Path) -> tuple[str, ...]:
    mobile = set(unique_header_columns(mobile_path)) & COMPOSITE_ID_HEADERS
    pc = set(unique_header_columns(pc_path)) & COMPOSITE_ID_HEADERS
    common = mobile & pc
    item_key = ("genre", "detailtype", "particulartype")
    if set(item_key) <= common:
        return item_key
    for strong in ("id", "sid", "objid", "key", "index"):
        if strong in common:
            companions = tuple(sorted(common - {strong}))[:2]
            return (strong,) + companions
    return ()


def add_cross_composite_key_candidates(candidates, pc_grouped, mobile_grouped,
                                       pc_root: Path, mobile_root: Path):
    """Align split/merged TSVs by shared schema keys, independent of file hash."""
    pc_profiles = []
    for pc_key, pc_records in pc_grouped.items():
        if Path(pc_key[1]).suffix.lower() != ".tsv":
            continue
        pc_path = extracted_file(pc_root, pc_key)
        if pc_path.is_file():
            pc_profiles.append((pc_key, pc_records, pc_path))

    relations = []
    for mobile_key, mobile_records in mobile_grouped.items():
        if Path(mobile_key[1]).suffix.lower() != ".tsv":
            continue
        mobile_path = extracted_file(mobile_root, mobile_key)
        if not mobile_path.is_file():
            continue
        mobile_headers = unique_header_columns(mobile_path)
        mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_records}
        for pc_key, pc_records, pc_path in pc_profiles:
            key_headers = shared_composite_headers(mobile_path, pc_path)
            if not key_headers:
                continue
            mobile_rows = composite_key_rows(mobile_path, key_headers)
            pc_rows = composite_key_rows(pc_path, key_headers)
            common = set(mobile_rows) & set(pc_rows)
            if len(common) < 5:
                continue
            pc_rate = len(common) / max(1, len(pc_rows))
            mobile_rate = len(common) / max(1, len(mobile_rows))
            if not (pc_rate >= 0.80 or mobile_rate >= 0.80 or len(common) >= 50):
                continue
            pc_headers = unique_header_columns(pc_path)
            column_map = {
                mobile_column: pc_headers[header]
                for header, mobile_column in mobile_headers.items()
                if header in pc_headers and header not in COMPOSITE_ID_HEADERS
            }
            if not column_map:
                continue
            pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_records}
            before = sum(len(values) for methods in candidates.values() for values in methods.values())
            for key in common:
                mobile_line = mobile_rows[key]
                pc_line = pc_rows[key]
                for mobile_column, pc_column in column_map.items():
                    mobile = mobile_locators.get((mobile_line, mobile_column))
                    pc = pc_locators.get((pc_line, pc_column))
                    if mobile and pc:
                        method = ("cross_file_composite_key_settings"
                                  if pc_key[0] == "settings.pak" else "cross_file_composite_key")
                        add_candidate(candidates, mobile, pc, method)
            after = sum(len(values) for methods in candidates.values() for values in methods.values())
            if after > before:
                relations.append({
                    "mobile_key": mobile_key,
                    "pc_key": pc_key,
                    "key_headers": key_headers,
                    "hits": len(common),
                    "pc_hit_rate": round(pc_rate, 4),
                    "mobile_hit_rate": round(mobile_rate, 4),
                    "candidates_added": after - before,
                })
    return relations


def add_cross_structure_candidates(candidates, pc_grouped, mobile_grouped, pc_root: Path, mobile_root: Path):
    """Align split/merged TSV rows through stable non-language cell signatures."""
    from tsv_structure_overlap import row_signatures

    pc_profiles = []
    for pc_key, pc_records in pc_grouped.items():
        if Path(pc_key[1]).suffix.lower() != ".tsv":
            continue
        pc_path = extracted_file(pc_root, pc_key)
        if not pc_path.is_file():
            continue
        signatures = row_signatures(pc_path)
        if signatures:
            pc_profiles.append((pc_key, pc_records, pc_path, signatures, set(signatures)))

    relation_rows = []
    for mobile_key, mobile_records in mobile_grouped.items():
        if Path(mobile_key[1]).suffix.lower() != ".tsv":
            continue
        mobile_path = extracted_file(mobile_root, mobile_key)
        if not mobile_path.is_file():
            continue
        mobile_signatures = row_signatures(mobile_path)
        if not mobile_signatures:
            continue
        mobile_set = set(mobile_signatures)
        mobile_headers = unique_header_columns(mobile_path)
        mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_records}
        for pc_key, pc_records, pc_path, pc_signatures, pc_set in pc_profiles:
            common = mobile_set & pc_set
            if len(common) < 5:
                continue
            pc_rate = len(common) / max(1, len(pc_signatures))
            mobile_rate = len(common) / max(1, len(mobile_signatures))
            # Accept either a substantial shared block or a nearly complete PC
            # shard embedded in a much larger mobile table.
            if not ((len(common) >= 20 and (pc_rate >= 0.45 or mobile_rate >= 0.45))
                    or pc_rate >= 0.90 or mobile_rate >= 0.90):
                continue
            pc_headers = unique_header_columns(pc_path)
            column_map = {
                mobile_column: pc_headers[header]
                for header, mobile_column in mobile_headers.items()
                if header in pc_headers
            }
            if not column_map:
                continue
            pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_records}
            before = sum(len(values) for methods in candidates.values() for values in methods.values())
            for signature in common:
                mobile_line = mobile_signatures[signature]
                pc_line = pc_signatures[signature]
                for mobile_column, pc_column in column_map.items():
                    mobile = mobile_locators.get((mobile_line, mobile_column))
                    pc = pc_locators.get((pc_line, pc_column))
                    if mobile and pc:
                        add_candidate(candidates, mobile, pc, "cross_file_row_signature")
            after = sum(len(values) for methods in candidates.values() for values in methods.values())
            relation_rows.append({
                "mobile_key": mobile_key,
                "pc_key": pc_key,
                "hits": len(common),
                "pc_hit_rate": round(pc_rate, 4),
                "mobile_hit_rate": round(mobile_rate, 4),
                "column_matches": len(column_map),
                "candidates_added": after - before,
            })
    return relation_rows


def sorted_records(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: (int(r.get("line") or 0), int(r.get("column") or 1), str(r.get("id", ""))))


def compatible_record_sets(mobile_records: list[dict], pc_records: list[dict]) -> bool:
    """True when two extracted resources are close enough for order-based pairing."""
    if not mobile_records or not pc_records:
        return False
    mobile_count = len(mobile_records)
    pc_count = len(pc_records)
    ratio = min(mobile_count, pc_count) / max(mobile_count, pc_count)
    if ratio < 0.94:
        return False
    mobile_columns = collections.Counter(int(r.get("column") or 1) for r in mobile_records)
    pc_columns = collections.Counter(int(r.get("column") or 1) for r in pc_records)
    common_columns = set(mobile_columns) & set(pc_columns)
    if not common_columns:
        return False
    common_mobile = sum(mobile_columns[c] for c in common_columns)
    common_pc = sum(pc_columns[c] for c in common_columns)
    return min(common_mobile, common_pc) / max(common_mobile, common_pc) >= 0.94


def unique_order_candidates(mobile_records: list[dict], pc_records: list[dict]) -> list[tuple[dict, dict]]:
    """Pair records by source order when same-hash resources only differ by small insertions."""
    mobile_sorted = sorted_records(mobile_records)
    pc_sorted = sorted_records(pc_records)
    if len(mobile_sorted) == len(pc_sorted):
        return list(zip(mobile_sorted, pc_sorted))

    # Keep only columns with equal counts, then pair within each column. This avoids
    # shifting a whole table when one optional language column exists on only one side.
    pairs: list[tuple[dict, dict]] = []
    by_mobile_col: dict[int, list[dict]] = collections.defaultdict(list)
    by_pc_col: dict[int, list[dict]] = collections.defaultdict(list)
    for record in mobile_sorted:
        by_mobile_col[int(record.get("column") or 1)].append(record)
    for record in pc_sorted:
        by_pc_col[int(record.get("column") or 1)].append(record)
    for column, mobile_values in by_mobile_col.items():
        pc_values = by_pc_col.get(column, [])
        if mobile_values and len(mobile_values) == len(pc_values):
            pairs.extend(zip(mobile_values, pc_values))
    return pairs


def add_candidate(candidates, mobile: dict, pc: dict, method: str):
    target = str(pc.get("original", ""))
    source = str(mobile.get("source_original", mobile.get("original", "")))
    if method in ("cross_file_composite_key_settings", "cross_file_composite_key"):
        if not needs_pc_recovery(mobile):
            return
        source_tags = re.findall(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:=[^>\r\n]*)?>", source)
        target_tags = re.findall(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:=[^>\r\n]*)?>", target)
        if len(source_tags) == len(target_tags):
            source_tag_iter = iter(source_tags)
            target = re.sub(
                r"</?[A-Za-z][A-Za-z0-9_:-]*(?:=[^>\r\n]*)?>",
                lambda _match: next(source_tag_iter),
                target,
            )
        elif not source_tags:
            target = re.sub(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:=[^>\r\n]*)?>", "", target)
        source_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", source)
        target_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", target)
        if source_numbers and len(source_numbers) == len(target_numbers):
            source_number_iter = iter(source_numbers)
            target = re.sub(
                r"(?<![A-Za-z])\d+(?:\.\d+)?",
                lambda _match: next(source_number_iter),
                target,
            )
    if not should_try_pc_replacement(mobile, target) or target == source or not acceptable_pc_text(target):
        return
    # A leading '$' is a legacy client text marker, not part of a Vietnamese
    # identifier.  Strip it on both sides for the broad placeholder validator;
    # prefix equality below still guarantees that it is preserved verbatim.
    # '$', '#', and '=' are client-side value markers and belong to the trusted
    # international field value. In this client, untranslated Vietnamese Tip
    # values often omit '$' while the corresponding Chinese literal requires it.
    # Migrating only the body makes the client decode Chinese through the legacy
    # Vietnamese path, so keep the complete international value.
    ok, reason = validate_translation(exchange_text(source), exchange_text(target))
    tokens_ok, _source_tokens, _target_tokens = validate_tokens(source, target)
    if (not ok and method in TRUSTED_CONTENT_ANCHORS and tokens_ok
            and reason.startswith("missing protected tokens:")
            and not RESOURCE_PATH_RE.search(source) and not RESOURCE_PATH_RE.search(target)):
        missing = [item.strip() for item in reason.split(":", 1)[1].split("|")]
        # Literal quantities may be written as a Chinese numeral in the official
        # text. Runtime placeholders/tags are still protected by validate_tokens.
        ok = bool(missing) and all(re.fullmatch(r"\d+(?:\.\d+)?", item) for item in missing)
    if not ok or not tokens_ok:
        return
    candidates[mobile["id"]][method].add(target)


def select_candidates(candidates, mobile_by_id):
    selected: dict[str, tuple[str, str]] = {}
    conflicts = []
    for record_id, methods in candidates.items():
        for method in sorted(methods, key=lambda item: METHOD_PRIORITY[item]):
            values = methods[method]
            if len(values) == 1:
                selected[record_id] = (method, next(iter(values)))
                break
            if len(values) > 1:
                mobile = mobile_by_id.get(record_id, {})
                conflicts.append({
                    "id": record_id,
                    "pak": mobile.get("pak", ""),
                    "source_file": mobile.get("source_file", ""),
                    "line": mobile.get("line", ""),
                    "column": mobile.get("column", ""),
                    "method": method,
                    "values": len(values),
                    "mobile_text": mobile.get("original", ""),
                    "pc_candidates": " | ".join(sorted(values)),
                })
                break
    return selected, conflicts


def migrate(pc_root: Path, mobile_root: Path, output_dir: Path, *, semantic: bool = False,
            progress=None, pak_scope: str = "") -> dict:
    pc_records = json.loads((pc_root / "localization" / "text_records.json").read_text(encoding="utf-8"))
    mobile_records = json.loads((mobile_root / "localization" / "text_records.json").read_text(encoding="utf-8"))
    working_mobile_records = [
        record for record in mobile_records
        if not pak_scope or str(record.get("pak", "")) == pak_scope
    ]
    pc_grouped = records_by_file(pc_records)
    mobile_grouped = records_by_file(working_mobile_records)
    pc_hashes = files_by_hash(pc_grouped)
    mobile_hashes = files_by_hash(mobile_grouped)
    candidates = collections.defaultdict(lambda: collections.defaultdict(set))

    # PC international client stores a large part of mobile settings.pak under
    # serverlist.pak.  File names and full structures may differ, but the
    # internal resource hash plus row/column locator is stable.  Only accept the
    # locator when every PC candidate resolves to one unique Chinese value.
    pc_by_hash_locator = collections.defaultdict(set)
    for pc in pc_records:
        target = str(pc.get("original", ""))
        if not acceptable_pc_text(target):
            continue
        key = (str(pc.get("hash", "")), int(pc.get("line") or 0), int(pc.get("column") or 1))
        pc_by_hash_locator[key].add(target)
    for mobile in working_mobile_records:
        key = (str(mobile.get("hash", "")), int(mobile.get("line") or 0), int(mobile.get("column") or 1))
        values = pc_by_hash_locator.get(key)
        if not values or len(values) != 1:
            continue
        pseudo_pc = {"original": next(iter(values))}
        add_candidate(candidates, mobile, pseudo_pc, "hash_locator_unique")

    # Same-hash TSVs: match cells through stable numeric row IDs, even when rows moved.
    for hash_id in set(pc_hashes) & set(mobile_hashes):
        for mobile_key, mobile_file_records in mobile_hashes[hash_id]:
            if Path(mobile_key[1]).suffix.lower() != ".tsv":
                continue
            mobile_path = extracted_file(mobile_root, mobile_key)
            if not mobile_path.exists():
                continue
            mobile_rows = stable_rows(mobile_path)
            mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_file_records}
            for pc_key, pc_file_records in pc_hashes[hash_id]:
                if Path(pc_key[1]).suffix.lower() != ".tsv":
                    continue
                pc_path = extracted_file(pc_root, pc_key)
                if not pc_path.exists():
                    continue
                pc_rows = stable_rows(pc_path)
                pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_file_records}
                # A unique numeric primary key remains authoritative when one
                # client adds or removes rows. Requiring identical whole-file
                # structure previously discarded thousands of valid SID/STRING
                # matches in the large updatefs dialogue table.
                for row_id, mobile_line in mobile_rows.items():
                    pc_line = pc_rows.get(row_id)
                    if pc_line is None:
                        continue
                    for (line, column), mobile in mobile_locators.items():
                        if line != mobile_line:
                            continue
                        pc = pc_locators.get((pc_line, column))
                        if pc:
                            add_candidate(candidates, mobile, pc, "tsv_primary_key")

                # A composite of unchanged numeric/resource cells can align rows
                # whose first column is blank, repeated, or localized.
                mobile_signatures = stable_row_signatures(mobile_path)
                pc_signatures = stable_row_signatures(pc_path)
                for signature, mobile_line in mobile_signatures.items():
                    pc_line = pc_signatures.get(signature)
                    if pc_line is None:
                        continue
                    for (line, column), mobile in mobile_locators.items():
                        if line != mobile_line:
                            continue
                        pc = pc_locators.get((pc_line, column))
                        if pc:
                            add_candidate(candidates, mobile, pc, "tsv_row_signature")

    # Same-hash, strictly identical file structure: row/column locators are safe.
    structure_cache = {}
    for hash_id in set(pc_hashes) & set(mobile_hashes):
        for mobile_key, mobile_file_records in mobile_hashes[hash_id]:
            mobile_path = extracted_file(mobile_root, mobile_key)
            if not mobile_path.exists() or mobile_path.suffix.lower() not in TEXT_EXTS:
                continue
            mobile_struct = structure_cache.setdefault(str(mobile_path), structure(mobile_path))
            mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_file_records}
            for pc_key, pc_file_records in pc_hashes[hash_id]:
                pc_path = extracted_file(pc_root, pc_key)
                if not pc_path.exists() or pc_path.suffix.lower() != mobile_path.suffix.lower():
                    continue
                if structure_cache.setdefault(str(pc_path), structure(pc_path)) != mobile_struct:
                    continue
                for pc in pc_file_records:
                    mobile = mobile_locators.get((int(pc["line"]), int(pc.get("column") or 1)))
                    if mobile:
                        add_candidate(candidates, mobile, pc, "strict_locator")

                # Some PC/mobile resources share the same internal file id but gained
                # or lost a small number of rows. When record counts and column shape
                # are nearly identical, source order remains a strong anchor.
                if compatible_record_sets(mobile_file_records, pc_file_records):
                    for mobile, pc in unique_order_candidates(mobile_file_records, pc_file_records):
                        add_candidate(candidates, mobile, pc, "same_hash_near_order")

                # If the locator itself exists on both sides, it is still useful even
                # when comments or unparsed cells changed the broader structure tuple.
                pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_file_records}
                if len(mobile_locators) and len(pc_locators):
                    common = set(mobile_locators) & set(pc_locators)
                    overlap = len(common) / max(1, min(len(mobile_locators), len(pc_locators)))
                    if overlap >= 0.94:
                        for locator in common:
                            add_candidate(candidates, mobile_locators[locator], pc_locators[locator], "same_hash_locator_soft")

    # Same-hash INIs may gain a leading section or move blocks between client
    # versions. Section+key remains the safest anchor when it is unique.
    for hash_id in set(pc_hashes) & set(mobile_hashes):
        for mobile_key, mobile_file_records in mobile_hashes[hash_id]:
            if Path(mobile_key[1]).suffix.lower() != ".ini":
                continue
            mobile_path = extracted_file(mobile_root, mobile_key)
            if not mobile_path.exists():
                continue
            mobile_anchors = unique_ini_anchors(mobile_path)
            if not mobile_anchors:
                continue
            mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_file_records}
            mobile_by_anchor = {
                anchor: mobile_locators[(line, 1)]
                for anchor, line in mobile_anchors.items()
                if (line, 1) in mobile_locators
            }
            if not mobile_by_anchor:
                continue
            for pc_key, pc_file_records in pc_hashes[hash_id]:
                if Path(pc_key[1]).suffix.lower() != ".ini":
                    continue
                pc_path = extracted_file(pc_root, pc_key)
                if not pc_path.exists():
                    continue
                pc_anchors = unique_ini_anchors(pc_path)
                if not pc_anchors:
                    continue
                pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_file_records}
                for anchor, mobile in mobile_by_anchor.items():
                    pc_line = pc_anchors.get(anchor)
                    if pc_line is None:
                        continue
                    pc = pc_locators.get((pc_line, 1))
                    if pc:
                        add_candidate(candidates, mobile, pc, "ini_section_key")

    # Same-hash INIs: pair equal key multiplicities in source order, independent of line shifts.
    for hash_id in set(pc_hashes) & set(mobile_hashes):
        for mobile_key, mobile_file_records in mobile_hashes[hash_id]:
            if Path(mobile_key[1]).suffix.lower() != ".ini":
                continue
            mobile_keys = collections.defaultdict(list)
            for record in sorted(mobile_file_records, key=lambda r: (int(r["line"]), int(r.get("column") or 1))):
                mobile_keys[str(record.get("key", "")).lower()].append(record)
            for pc_key, pc_file_records in pc_hashes[hash_id]:
                if Path(pc_key[1]).suffix.lower() != ".ini":
                    continue
                mobile_path = extracted_file(mobile_root, mobile_key)
                pc_path = extracted_file(pc_root, pc_key)
                if not mobile_path.exists() or not pc_path.exists() or structure(mobile_path) != structure(pc_path):
                    continue
                pc_keys = collections.defaultdict(list)
                for record in sorted(pc_file_records, key=lambda r: (int(r["line"]), int(r.get("column") or 1))):
                    pc_keys[str(record.get("key", "")).lower()].append(record)
                for key, mobile_values in mobile_keys.items():
                    pc_values = pc_keys.get(key, [])
                    if len(mobile_values) != len(pc_values):
                        continue
                    for mobile, pc in zip(mobile_values, pc_values):
                        add_candidate(candidates, mobile, pc, "ini_key_occurrence")

    # Files may be split, merged, or renumbered between the PC and mobile
    # clients. Establish a relation from independent content/path anchors first,
    # then use the unique row ID only inside that related pair.
    relations = cross_file_relations(pc_grouped, mobile_grouped, pc_root, mobile_root)
    for relation in relations:
        mobile_file_records = mobile_grouped[relation["mobile_key"]]
        pc_file_records = pc_grouped[relation["pc_key"]]
        mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_file_records}
        pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_file_records}
        for row_id, mobile_line in relation["mobile_rows"].items():
            pc_line = relation["pc_rows"].get(row_id)
            if pc_line is None:
                continue
            for (line, column), mobile in mobile_locators.items():
                if line != mobile_line:
                    continue
                pc = pc_locators.get((pc_line, column))
                if pc:
                    add_candidate(candidates, mobile, pc, "cross_file_primary_key")

        # Recover short runs between two stable ID anchors. This covers blank-ID
        # rows and small renumbering changes while requiring the same local row
        # span on both clients, similar to a conservative sequence alignment.
        anchors = sorted(
            (
                mobile_line,
                relation["pc_rows"][row_id],
            )
            for row_id, mobile_line in relation["mobile_rows"].items()
            if row_id in relation["pc_rows"]
        )
        for (mobile_start, pc_start), (mobile_end, pc_end) in zip(anchors, anchors[1:]):
            mobile_gap = mobile_end - mobile_start
            pc_gap = pc_end - pc_start
            if mobile_gap != pc_gap or not 1 < mobile_gap <= 12:
                continue
            for offset in range(1, mobile_gap):
                mobile_line = mobile_start + offset
                pc_line = pc_start + offset
                mobile_columns = {
                    column for line, column in mobile_locators
                    if line == mobile_line
                }
                pc_columns = {
                    column for line, column in pc_locators
                    if line == pc_line
                }
                for column in mobile_columns & pc_columns:
                    add_candidate(
                        candidates,
                        mobile_locators[(mobile_line, column)],
                        pc_locators[(pc_line, column)],
                        "context_sequence",
                    )

    composite_relations = add_cross_composite_key_candidates(
        candidates, pc_grouped, mobile_grouped, pc_root, mobile_root
    )
    structure_relations = add_cross_structure_candidates(
        candidates, pc_grouped, mobile_grouped, pc_root, mobile_root
    )

    selected, conflicts = select_candidates(candidates, {r["id"]: r for r in mobile_records})

    # High-confidence pairs form a conservative exact-text translation memory.
    memory = collections.defaultdict(set)
    mobile_by_id = {r["id"]: r for r in working_mobile_records}
    for record_id, (_method, target) in selected.items():
        source = str(mobile_by_id[record_id].get("source_original", mobile_by_id[record_id].get("original", "")))
        if source.strip():
            memory[source].add(target)
    unique_memory = {source: next(iter(values)) for source, values in memory.items() if len(values) == 1}
    for mobile in working_mobile_records:
        if mobile["id"] in selected or not needs_pc_recovery(mobile):
            continue
        source = str(mobile.get("source_original", mobile.get("original", "")))
        target = unique_memory.get(source)
        if not target or not validate_translation(source, target)[0] or not validate_tokens(source, target)[0]:
            continue
        selected[mobile["id"]] = ("exact_text_memory", target)

    # Final automatic fallback is a checked-in, deterministic glossary. Never
    # read semantic/model caches here: one-click migration must be reproducible
    # and work fully offline.
    from polish_localizer import (
        SOURCE_OVERRIDE, apply_canon, apply_fix, apply_terms, translate_template,
    )
    from rule_translate_visible_remaining import generic_replace, translate_rule
    fallback_candidates = collections.defaultdict(lambda: collections.defaultdict(set))
    for mobile in working_mobile_records:
        if mobile["id"] in selected or not needs_pc_recovery(mobile):
            continue
        source = str(mobile.get("source_original", mobile.get("original", "")))
        target = SOURCE_OVERRIDE.get(source) or translate_rule(source)
        if not target:
            target = translate_template(source) or source
            target = apply_fix(apply_canon(apply_terms(generic_replace(target))))
        if target:
            add_candidate(fallback_candidates, mobile, {"original": target}, "deterministic_glossary")
    fallback_selected, fallback_conflicts = select_candidates(
        fallback_candidates, {r["id"]: r for r in working_mobile_records}
    )
    selected.update(fallback_selected)
    conflicts.extend(fallback_conflicts)

    semantic_report = {
        "status": "disabled_no_model" if not semantic else "ignored_no_model_policy",
        "matched": 0,
    }

    migrated = []
    rows = []
    method_counts = collections.Counter()
    pak_counts = collections.Counter()
    for record in mobile_records:
        item = dict(record)
        picked = selected.get(record["id"])
        if picked:
            method, target = picked
            source = str(item.get("source_original", item.get("original", "")))
            item["source_original"] = source
            item["original"] = target
            item["translation"] = target
            item["status"] = "已迁移"
            item["note"] = (str(item.get("note", "")).strip() + f" [PC迁移:{method}]").strip()
            method_counts[method] += 1
            pak_counts[str(item.get("pak", ""))] += 1
            rows.append({
                "id": item["id"], "pak": item.get("pak", ""), "hash": item.get("hash", ""),
                "source_file": item.get("source_file", ""), "line": item.get("line", ""),
                "column": item.get("column", ""), "method": method,
                "confidence": METHOD_CONFIDENCE.get(method, 0.0),
                "mobile_text": source, "pc_text": target,
            })
        migrated.append(item)

    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "text_records.pc_zh.json"
    records_path.write_text(json.dumps(migrated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with (output_dir / "migration_rows.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)
    relation_rows = [
        {
            "mobile_pak": relation["mobile_key"][0],
            "mobile_file": relation["mobile_key"][1],
            "pc_pak": relation["pc_key"][0],
            "pc_file": relation["pc_key"][1],
            "common_ids": relation["common_ids"],
            "exact_text_anchors": relation["exact_text"],
            "resource_path_anchors": relation["resource_path"],
            "confidence": relation["confidence"],
        }
        for relation in relations
    ]
    relation_rows.extend({
        "mobile_pak": relation["mobile_key"][0],
        "mobile_file": relation["mobile_key"][1],
        "pc_pak": relation["pc_key"][0],
        "pc_file": relation["pc_key"][1],
        "common_ids": relation["hits"],
        "exact_text_anchors": "",
        "resource_path_anchors": "",
        "confidence": round(max(relation["pc_hit_rate"], relation["mobile_hit_rate"]), 4),
    } for relation in structure_relations)
    relation_rows.extend({
        "mobile_pak": relation["mobile_key"][0],
        "mobile_file": relation["mobile_key"][1],
        "pc_pak": relation["pc_key"][0],
        "pc_file": relation["pc_key"][1],
        "common_ids": relation["hits"],
        "exact_text_anchors": "composite:" + "+".join(relation["key_headers"]),
        "resource_path_anchors": "",
        "confidence": round(max(relation["pc_hit_rate"], relation["mobile_hit_rate"]), 4),
    } for relation in composite_relations)
    with (output_dir / "file_relations.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(relation_rows[0]) if relation_rows else ["mobile_file"])
        writer.writeheader()
        writer.writerows(relation_rows)
    with (output_dir / "migration_conflicts.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(conflicts[0]) if conflicts else ["id"])
        writer.writeheader()
        writer.writerows(conflicts)
    before_coverage = coverage_summary(working_mobile_records)
    after_coverage = coverage_summary(migrated, pak_scope)
    report = {
        "pc_root": str(pc_root.resolve()), "mobile_root": str(mobile_root.resolve()),
        "pc_records": len(pc_records), "mobile_records": len(mobile_records),
        "scope_records": len(working_mobile_records),
        "migrated_records": len(rows), "coverage_percent": round(len(rows) * 100 / max(1, len(working_mobile_records)), 2),
        "methods": dict(method_counts), "paks": dict(pak_counts),
        "unique_text_memory_entries": len(unique_memory), "conflicts_skipped": len(conflicts),
        "deterministic_glossary_entries": len(SOURCE_OVERRIDE),
        "cross_file_relations": len(relations),
        "cross_structure_relations": len(structure_relations),
        "cross_composite_relations": len(composite_relations),
        "coverage_before": before_coverage,
        "coverage_after": after_coverage,
        "relation_report": str((output_dir / "file_relations.csv").resolve()),
        "conflict_report": str((output_dir / "migration_conflicts.csv").resolve()),
        "semantic": semantic_report,
        "records_output": str(records_path.resolve()),
        "safety": "Original paks++ records and extracted files were not modified. Placeholder mismatches, ambiguous PC values, and existing Chinese records were skipped.",
    }
    (output_dir / "migration_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Migrate reusable PC Chinese text into a mobile localization record set.")
    parser.add_argument("pc_root", type=Path)
    parser.add_argument("mobile_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.pc_root, args.mobile_root, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
