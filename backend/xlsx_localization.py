#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import functools
import hashlib
import html
import importlib.util
import json
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from tsv_localization import (
    KV_RE,
    LOCALIZABLE_SUFFIXES,
    encode_cell,
    iter_translatable_cells,
    restore_template,
    stable_cell_id,
    token_template,
    validate_translation,
)
from lua_localization import iter_lua_text_parts, replace_lua_text_parts
from localization_analyzer import decode_best, score_text, is_localizable_text_path
from parallel_config import worker_count

XLSX_MAPPING_VERSION = 5
LEGACY_XLSX_MAPPING_VERSIONS = {3, 4}
XLSX_MAPPING_MODE = "xlsx-dedup-cells-compact"
SHEET_NAME = "本土化"
XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_INVALID_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_POLISH_MODULE_CACHE = None
_POLISH_MODULE_LOADED = False
_TRUSTED_TM_CACHE = None


def _xml_escape(value: str) -> str:
    text = XML_INVALID_CHAR_RE.sub("", str(value or ""))
    return html.escape(text, quote=True)


def _column_name(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def write_simple_xlsx(path: Path, rows: list[dict], headers: list[str] | None = None, sheet_name: str = SHEET_NAME) -> None:
    path = Path(path)
    headers = headers or ["id", "text"]
    path.parent.mkdir(parents=True, exist_ok=True)

    def cell_xml(row_no: int, col_no: int, value) -> str:
        ref = f"{_column_name(col_no)}{row_no}"
        return f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape("" if value is None else value)}</t></is></c>'

    sheet_rows = []
    for row_no, values in enumerate([dict(zip(headers, headers)), *rows], 1):
        cells = "".join(cell_xml(row_no, col_no, values.get(header, "")) for col_no, header in enumerate(headers, 1))
        sheet_rows.append(f'<row r="{row_no}">{cells}</row>')
    dimension = f"A1:{_column_name(len(headers))}{len(rows) + 1}"
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{XML_NS}" xmlns:r="{REL_NS}">'
        f'<dimension ref="{dimension}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{XML_NS}" xmlns:r="{REL_NS}"><sheets>'
        f'<sheet name="{_xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
        '</sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"x": XML_NS}
    values = []
    for si in root.findall("x:si", ns):
        texts = [node.text or "" for node in si.findall(".//x:t", ns)]
        values.append("".join(texts))
    return values


def read_simple_xlsx(path: Path) -> list[dict]:
    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        shared = _read_shared_strings(zf)
        sheet_name = "xl/worksheets/sheet1.xml"
        root = ET.fromstring(zf.read(sheet_name))
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
        item["_values"] = values
        for i, header in enumerate(headers):
            if header:
                item[header] = values[i] if i < len(values) else ""
        rows.append(item)
    return rows


def _polish_module():
    global _POLISH_MODULE_CACHE, _POLISH_MODULE_LOADED
    if _POLISH_MODULE_LOADED:
        return _POLISH_MODULE_CACHE
    candidates = [
        Path(__file__).with_name("polish_localizer.py"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_pak_polish_localizer", path)
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _POLISH_MODULE_CACHE = module
        _POLISH_MODULE_LOADED = True
        return module
    _POLISH_MODULE_LOADED = True
    return None


def _trusted_tm() -> dict[str, str]:
    global _TRUSTED_TM_CACHE
    if _TRUSTED_TM_CACHE is not None:
        return _TRUSTED_TM_CACHE
    from localization_tm import init_db
    db = init_db(Path(__file__).resolve().parents[1] / "localization.db")
    rows = db.execute(
        "SELECT source_key,target_text FROM translation_memory "
        "WHERE quality IN ('manual','reviewed','approved','seed')"
    ).fetchall()
    glossary = db.execute("SELECT source,target FROM glossary WHERE mode='exact'").fetchall()
    db.close()
    _TRUSTED_TM_CACHE = {str(row[0]): str(row[1]) for row in rows}
    for row in glossary:
        _TRUSTED_TM_CACHE.setdefault(str(row[0]), str(row[1]))
    return _TRUSTED_TM_CACHE


def polish_text(source: str, translated: str, numeric_id: str = "") -> tuple[str, bool]:
    module = _polish_module()
    text = str(translated or "")
    changed = False
    if module is None:
        return text.strip(), False
    src_key = str(source or "").strip()
    before = text
    # Trusted TM is authoritative over Google output.  It is populated by
    # manual/approved translations and by the safe PC merge workflow.
    try:
        from localization_tm import normalize_key
        tm_text = _trusted_tm().get(normalize_key(src_key))
        if tm_text:
            return str(tm_text).strip(), str(tm_text).strip() != before
    except Exception:
        pass
    if hasattr(module, "FALLBACK") and not text:
        text = module.FALLBACK.get(str(numeric_id), "")
    if hasattr(module, "SOURCE_OVERRIDE") and src_key in module.SOURCE_OVERRIDE:
        text = module.SOURCE_OVERRIDE[src_key]
    elif hasattr(module, "translate_template"):
        try:
            templated = module.translate_template(src_key)
            vi = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡớợùúủũụưứừửữựỳýỷỹỵđ]", re.I)
            if templated is not None and not vi.search(templated):
                text = templated
        except Exception:
            pass
    for fn in ("apply_terms", "apply_canon", "apply_fix"):
        if hasattr(module, fn):
            try:
                text = getattr(module, fn)(text)
            except Exception:
                pass
    if hasattr(module, "restore_symbols"):
        try:
            text = module.restore_symbols(src_key, text)
        except Exception:
            pass
    text = text.strip()
    changed = text != before
    return text, changed


def _mapping_rows(mapping: dict):
    """Yield (id, source text, cells) for compact v5 and legacy mappings."""
    if mapping.get("version") == XLSX_MAPPING_VERSION and mapping.get("mode") == XLSX_MAPPING_MODE:
        files = mapping.get("files") or []
        for row in mapping.get("rows") or []:
            if not isinstance(row, list) or len(row) < 3:
                continue
            cells = []
            for cell in row[2] or []:
                if not isinstance(cell, list) or len(cell) < 3:
                    continue
                file_index = int(cell[0])
                if file_index < 0 or file_index >= len(files):
                    continue
                cells.append({"relative_path": files[file_index], "row": int(cell[1]), "column": int(cell[2])})
            yield str(row[0]), str(row[1] or ""), cells
        return
    if mapping.get("version") in LEGACY_XLSX_MAPPING_VERSIONS and mapping.get("mode") == "xlsx-dedup-cells":
        for row in mapping.get("rows") or []:
            yield str(row.get("id")), str(row.get("text") or ""), row.get("cells") or []
        return
    if mapping.get("version") == 4 and mapping.get("mode") == XLSX_MAPPING_MODE:
        files = mapping.get("files") or []
        for row in mapping.get("rows") or []:
            if not isinstance(row, list) or len(row) < 3:
                continue
            cells = []
            for cell in row[2] or []:
                if not isinstance(cell, list) or len(cell) < 3:
                    continue
                file_index = int(cell[0])
                if 0 <= file_index < len(files):
                    cells.append({"relative_path": files[file_index], "row": int(cell[1]), "column": int(cell[2])})
            yield str(row[0]), str(row[1] or ""), cells
        return
    raise ValueError("不支持的 XLSX 映射格式，请重新导出")


def _source_cell_meta(src_dir: Path, pak_name: str, cell: dict, line_cache: dict) -> dict:
    """Rebuild metadata omitted by compact mappings from the untouched source file."""
    if "source" in cell and "tokens" in cell and "template" in cell:
        return cell
    rel = cell.get("relative_path") or cell.get("source_file")
    src = src_dir / Path(rel)
    cache_key = str(src)
    raw_lines = line_cache.get(cache_key)
    if raw_lines is None:
        raw_lines = src.read_bytes().splitlines()
        line_cache[cache_key] = raw_lines
    row_no = int(cell["row"])
    col_no = int(cell["column"])
    if row_no < 1 or row_no > len(raw_lines):
        raise ValueError(f"源文件行号越界：{rel} L{row_no}")
    raw_line = raw_lines[row_no - 1]
    if src.suffix.lower() == ".lua":
        literal = next((x for x in iter_lua_text_parts(raw_line) if x.ordinal == col_no), None)
        if literal is None:
            raise ValueError(f"Lua 字符串位置不存在：{rel} L{row_no}:C{col_no}")
        original = literal.content
        source, encoding, _language, _score = decode_best(original)
        template = token_template(source)
        return {
            "id": stable_cell_id(pak_name, src.name, row_no, col_no, source),
            "source_file": src.name,
            "relative_path": rel,
            "row": row_no,
            "column": col_no,
            "mode": "lua_string",
            "prefix": "",
            "source": source,
            "tokens": template["tokens"],
            "template": template["template"],
            "encoding": encoding,
        }
    raw_cells = raw_line.split(b"\t")
    mode = "cell"
    prefix = ""
    original = raw_cells[col_no - 1] if 0 < col_no <= len(raw_cells) else None
    if src.suffix.lower() in (".ini", ".txt") and len(raw_cells) == 1:
        match = KV_RE.match(raw_line)
        if match and col_no == 1:
            mode = "key_value"
            prefix = match.group(1).decode("ascii", "replace")
            original = match.group(2)
    if original is None:
        raise ValueError(f"源文件列号越界：{rel} L{row_no}:C{col_no}")
    source, encoding, _language, _score = decode_best(original)
    template = token_template(source)
    source_file = src.name
    return {
        "id": stable_cell_id(pak_name, source_file, row_no, col_no, source),
        "source_file": source_file,
        "relative_path": rel,
        "row": row_no,
        "column": col_no,
        "mode": mode,
        "prefix": prefix,
        "source": source,
        "tokens": template["tokens"],
        "template": template["template"],
        "dollar_spaced": bool(template.get("dollar_spaced")),
        "encoding": encoding,
    }


ANGLE_TAG_RE = re.compile(r"(?<!<)<[^<>\r\n]{1,160}>(?!>)")
from localization_tm import RESOURCE_PATH_PATTERN

RESOURCE_REF_RE = re.compile(RESOURCE_PATH_PATTERN, re.I)
HTML_CODE_BLOCK_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.I | re.S)
PLACEHOLDER_MARKER_RE = re.compile(r"\{P\d+\}|◈\s*(?:P\s*)?\d+\s*◈", re.I)
CONTROL_ANGLE_NAME_RE = re.compile(r"^/?(?:c|color|font|size|img|image|sprite|br|b|i|u|p|a|style|script)\b", re.I)


def structural_angle_tags(text: str) -> list[str]:
    # Every <...> span is runtime structure. Even nonstandard tags can contain
    # Vietnamese identifiers; changing any byte can make the whole UI line vanish.
    return ANGLE_TAG_RE.findall(str(text or ""))


def validate_import_structure(source: str, target: str) -> tuple[bool, str]:
    source = str(source or "")
    target = str(target or "")
    source_tags = structural_angle_tags(source)
    target_tags = structural_angle_tags(target)
    if source_tags != target_tags:
        return False, f"尖括号控制标记不一致: src={source_tags[:8]} tgt={target_tags[:8]}"

    source_refs = RESOURCE_REF_RE.findall(source)
    target_refs = RESOURCE_REF_RE.findall(target)
    if source_refs != target_refs:
        return False, f"资源路径不一致: src={source_refs[:4]} tgt={target_refs[:4]}"

    source_blocks = [m.group(0).replace("\r\n", "\n").replace("\r", "\n") for m in HTML_CODE_BLOCK_RE.finditer(source)]
    target_blocks = [m.group(0).replace("\r\n", "\n").replace("\r", "\n") for m in HTML_CODE_BLOCK_RE.finditer(target)]
    if source_blocks != target_blocks:
        return False, "HTML style/script 代码块被改变"

    # Diamond markers are transport-only placeholders used inside exported
    # workbooks. They must be restored to the source token before game files or
    # Studio records are updated.
    if PLACEHOLDER_MARKER_RE.search(target) and not PLACEHOLDER_MARKER_RE.search(source):
        return False, "XLSX 运输占位符尚未还原"

    return True, ""


def _content_mapping_id(text: str) -> str:
    """Use source content as the XLSX namespace so unrelated exports cannot collide."""
    return "x" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:24]


def _lua_line_skeleton(raw_line: bytes) -> bytes:
    parts = list(iter_lua_text_parts(raw_line))
    if not parts:
        return raw_line
    out = bytearray(raw_line)
    for part in reversed(parts):
        out[part.start:part.end] = f"<STR{part.ordinal}>".encode("ascii")
    return bytes(out)


def _tabular_anchor(raw_line: bytes, translated_column: int) -> tuple:
    cells = raw_line.split(b"\t")
    anchors = []
    for index, value in enumerate(cells, 1):
        if index == translated_column:
            continue
        text = value.decode("latin1", "ignore").strip()
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:/\\-]*", text):
            anchors.append((index, text.lower()))
    return len(cells), tuple(anchors)


def _line_structure(raw: bytes, suffix: str, translated_column: int) -> tuple | None:
    if suffix == ".lua":
        return _lua_line_skeleton(raw)
    if suffix == ".ini":
        match = KV_RE.match(raw)
        if match:
            return match.group(1).strip().lower()
        stripped = raw.strip()
        return stripped.lower() if stripped.startswith(b"[") and stripped.endswith(b"]") else None
    if suffix == ".tsv":
        return _tabular_anchor(raw, translated_column)
    return None


@functools.lru_cache(maxsize=4096)
def _cached_resource_lines(path_text: str) -> tuple[bytes, ...]:
    return tuple(Path(path_text).read_bytes().splitlines())


@functools.lru_cache(maxsize=1000000)
def _cached_line_structure(path_text: str, row: int, suffix: str, translated_column: int) -> tuple | None:
    lines = _cached_resource_lines(path_text)
    if row < 1 or row > len(lines):
        return None
    # Column does not affect Lua/INI skeletons; callers pass zero to maximize
    # cache reuse for lines containing several visible strings.
    return _line_structure(lines[row - 1], suffix, translated_column)


@functools.lru_cache(maxsize=500000)
def _cached_location_guard(root_text: str, rel: str, row: int, column: int) -> tuple | None:
    path = Path(root_text) / Path(rel)
    path_text = str(path)
    try:
        lines = _cached_resource_lines(path_text)
    except OSError:
        return None
    if row < 1 or row > len(lines):
        return None
    suffix = path.suffix.lower()
    structure_column = column if suffix == ".tsv" else 0
    current = _cached_line_structure(path_text, row, suffix, structure_column)
    if current is None:
        return None
    context = []
    for offset in (-2, -1, 1, 2):
        index = row - 1 + offset
        if 0 <= index < len(lines):
            anchor = _cached_line_structure(path_text, index + 1, suffix, structure_column)
            if anchor is not None:
                context.append((offset, anchor))
    if suffix in (".lua", ".ini", ".tsv"):
        return (suffix, current, tuple(context))
    # Plain TXT lines have no stable key/code skeleton. Row number alone is not
    # sufficient evidence across game versions.
    return None


def location_guard_signature(root: Path, cell: dict) -> tuple | None:
    """Return cached structure-only identity, excluding player-visible text."""
    rel = str(cell.get("relative_path") or cell.get("source_file") or "")
    return _cached_location_guard(
        str(Path(root)), rel, int(cell.get("row") or 0), int(cell.get("column") or 0)
    )


def location_guard_matches(source_root: Path, target_root: Path, cell: dict) -> bool:
    left = location_guard_signature(source_root, cell)
    right = location_guard_signature(target_root, cell)
    return left is not None and left == right


def _assert_safe_mapping_ids(mapping: dict, mapping_rows: list[tuple]) -> None:
    if mapping.get("id_scheme") == "source-sha256-24-v1":
        return
    ids = [str(row_id) for row_id, _source, _cells in mapping_rows]
    if ids and all(row_id.isdigit() for row_id in ids):
        raise ValueError(
            "这是旧版顺序 ID 的 XLSX 映射，存在整表错位风险，已禁止导入。"
            "请在当前项目重新点击‘导出 XLSX’，只使用新生成的 XLSX 和映射表。"
        )


def remap_xlsx_by_source(xlsx_path: Path, source_mapping_path: Path, target_mapping_path: Path, output_path: Path) -> dict:
    """Convert an older workbook to a new export by matching immutable source text."""
    source_mapping = json.loads(Path(source_mapping_path).read_text(encoding="utf-8-sig"))
    target_mapping = json.loads(Path(target_mapping_path).read_text(encoding="utf-8-sig"))
    source_rows = list(_mapping_rows(source_mapping))
    target_rows = list(_mapping_rows(target_mapping))
    workbook = {}
    for row in read_simple_xlsx(xlsx_path):
        values = row.get("_values") or []
        row_id = str(row.get("id", "") or (values[0] if values else "")).strip()
        value = row.get("text")
        if value is None or value == "":
            value = values[1] if len(values) > 1 else ""
        if row_id:
            workbook[row_id] = str(value or "")
    def location_keys(mapping, rows):
        files = mapping.get("files") or []
        result = {}
        for _row_id, source, cells in rows:
            for cell in cells:
                rel = str(cell.get("relative_path") or cell.get("source_file") or "")
                result[(Path(rel).name, int(cell.get("row") or 0), int(cell.get("column") or 0))] = source
        return result

    target_source_by_location = location_keys(target_mapping, target_rows)
    source_root = Path(source_mapping.get("extracted_dir") or "").resolve()
    target_root = Path(target_mapping.get("extracted_dir") or "").resolve()
    translations_by_source = {}
    rescued_by_location = 0
    for row_id, source, cells in source_rows:
        value = workbook.get(row_id, "")
        if not value or value == source:
            continue
        translations_by_source[source] = value
        # Some older projects accidentally overwrote their extracted/source
        # text with Chinese. Recover the real source identity from the new
        # pristine mapping at the same resource cell, then continue matching by
        # that recovered source text.
        for cell in cells:
            rel = str(cell.get("relative_path") or cell.get("source_file") or "")
            recovered_source = target_source_by_location.get(
                (Path(rel).name, int(cell.get("row") or 0), int(cell.get("column") or 0))
            )
            guarded = source_root.is_dir() and target_root.is_dir() and location_guard_matches(source_root, target_root, cell)
            if guarded and recovered_source and recovered_source != source:
                translations_by_source.setdefault(recovered_source, value)
                rescued_by_location += 1
    converted = []
    matched = 0
    for row_id, source, _cells in target_rows:
        value = translations_by_source.get(source, source)
        if value != source:
            matched += 1
        converted.append({"id": row_id, "text": value})
    write_simple_xlsx(output_path, converted, ["id", "text"])
    return {"xlsx": str(output_path), "source_rows": len(source_rows), "target_rows": len(target_rows),
            "translated_sources": len(translations_by_source), "matched": matched,
            "new_or_changed": len(target_rows) - matched, "rescued_by_location": rescued_by_location}


def recover_xlsx_from_modified(translated_dir: Path, target_mapping_path: Path, output_path: Path) -> dict:
    """Recover validated Chinese from materialized resources using a clean mapping."""
    target_mapping = json.loads(Path(target_mapping_path).read_text(encoding="utf-8-sig"))
    target_rows = list(_mapping_rows(target_mapping))
    cache = {}
    output_rows = []
    matched = conflicts = rejected = 0
    matched_cells = 0
    pak_name = str(target_mapping.get("pak") or "")
    clean_root = Path(target_mapping.get("extracted_dir") or "").resolve()
    translated_dir = Path(translated_dir).resolve()
    clean_root_available = clean_root.is_dir()
    for row_id, source, cells in target_rows:
        candidates: dict[str, int] = {}
        for cell in cells:
            if not clean_root_available or not location_guard_matches(clean_root, translated_dir, cell):
                rejected += 1
                continue
            try:
                meta = _source_cell_meta(Path(translated_dir), pak_name, cell, cache)
            except Exception:
                continue
            target = str(meta.get("source") or "")
            if not target or target == source or score_text(target)[1] != "zh":
                continue
            token_ok, _token_reason = validate_translation(source, target)
            structure_ok, _structure_reason = validate_import_structure(source, target)
            if not token_ok or not structure_ok:
                rejected += 1
                continue
            portable = token_template(target)["text"]
            candidates[portable] = candidates.get(portable, 0) + 1
            matched_cells += 1
        if candidates:
            ordered = sorted(candidates.items(), key=lambda pair: (-pair[1], pair[0]))
            value = ordered[0][0]
            matched += 1
            if len(ordered) > 1:
                conflicts += 1
        else:
            value = source
        output_rows.append({"id": row_id, "text": value})
    write_simple_xlsx(output_path, output_rows, ["id", "text"])
    return {"xlsx": str(output_path), "rows": len(target_rows), "matched": matched,
            "matched_cells": matched_cells, "conflicts_resolved_by_majority": conflicts,
            "rejected_cells": rejected, "remaining": len(target_rows) - matched}


def restore_xlsx_translation(target: str, meta: dict, source_text: str | None = None) -> tuple[str | None, str]:
    source = str(source_text if source_text is not None else meta.get("source", "") or "")
    raw = str(target or "")

    # When translators keep the real tags/paths directly in XLSX, do not wrap
    # them with the template again.  That used to turn
    # <c=yellow>中文<c> into <c=yellow><c=yellow>中文<c><c>.
    token_ok, token_reason = validate_translation(source, raw)
    structure_ok, _structure_reason = validate_import_structure(source, raw)
    exported_tokens = [str(item.get("value", "")) for item in meta.get("tokens", []) if item.get("value") is not None]
    tokens_already_restored = all(value in raw for value in exported_tokens)
    if (structure_ok and token_ok and tokens_already_restored
            and not meta.get("dollar_spaced") and not PLACEHOLDER_MARKER_RE.search(raw)):
        return raw, ""

    restored, error = restore_template(raw, meta)
    if restored is not None and not error:
        return restored, ""
    return None, error or token_reason or "占位符还原失败"


def export_xlsx_mapping(src_dir: Path, out_dir: Path, base_name: str, pak_name: str, workers: int | None = None, records_path: Path | None = None) -> dict:
    src_dir = Path(src_dir)
    out_dir = Path(out_dir)
    files = sorted(p for p in src_dir.rglob("*") if is_localizable_text_path(p))
    unique_rows = []
    grouped: dict[str, dict] = {}
    file_indexes: dict[str, int] = {}
    mapping_csv_rows = []
    source_rows = 0
    matched_files = set()
    for path in files:
        relative = path.relative_to(src_dir).as_posix()
        file_index = file_indexes.setdefault(relative, len(file_indexes))
        file_hit = False
        for cell in iter_translatable_cells(path, pak_name):
            text = cell["template"]["text"]
            item = grouped.get(text)
            if item is None:
                item = {"id": _content_mapping_id(text), "text": text, "cells": []}
                grouped[text] = item
                unique_rows.append({"id": item["id"], "text": text})
            item["cells"].append([file_index, cell["row"], cell["column"]])
            mapping_csv_rows.append((item["id"], relative, cell["row"], cell["column"]))
            source_rows += 1
            file_hit = True
        if file_hit:
            matched_files.add(relative)
    if not unique_rows:
        raise ValueError("没有检测到可导出的越南文或中越混合文本")

    # This workbook is also the portable translation package. Keep mapping IDs
    # anchored to source text, while prefilling the editable text with the
    # current Studio translation. New game versions can then match by source
    # fingerprint instead of fragile file/line positions.
    prefilled_rows = 0
    if records_path and Path(records_path).is_file():
        studio_records = json.loads(Path(records_path).read_text(encoding="utf-8"))
        records_by_location = {
            (str(rec.get("source_file") or ""), int(rec.get("line") or 0), int(rec.get("column") or 0)): rec
            for rec in studio_records if rec.get("pak") == pak_name
        }
        files_by_index = list(file_indexes.keys())
        output_rows = {row["id"]: row for row in unique_rows}
        for item in grouped.values():
            candidates: dict[str, int] = {}
            for file_index, row_no, col_no in item["cells"]:
                rel = files_by_index[int(file_index)]
                rec = records_by_location.get((Path(rel).name, int(row_no), int(col_no)))
                if not rec:
                    continue
                current = str(rec.get("original") or "")
                source = str(rec.get("source_original") or "")
                if not current or current == source or score_text(current)[1] in ("vi", "mixed"):
                    continue
                token_ok, _token_reason = validate_translation(source, current)
                structure_ok, _structure_reason = validate_import_structure(source, current)
                if not token_ok or not structure_ok:
                    continue
                portable = token_template(current)["text"]
                candidates[portable] = candidates.get(portable, 0) + 1
            if candidates:
                output_rows[item["id"]]["text"] = max(candidates.items(), key=lambda pair: (pair[1], pair[0]))[0]
                prefilled_rows += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"{base_name}_localization.xlsx"
    mapping_path = out_dir / f"{base_name}_localization_mapping.json"
    mapping_csv = out_dir / f"{base_name}_localization_mapping.csv"
    write_simple_xlsx(xlsx_path, unique_rows, ["id", "text"])
    mapping = {
        "version": XLSX_MAPPING_VERSION,
        "mode": XLSX_MAPPING_MODE,
        "pak": pak_name,
        "base_name": base_name,
        "extracted_dir": str(src_dir.resolve()),
        "source_rows": source_rows,
        "unique_rows": len(unique_rows),
        "prefilled_translations": prefilled_rows,
        "id_scheme": "source-sha256-24-v1",
        "files": list(file_indexes.keys()),
        "rows": [[row["id"], row["text"], row["cells"]] for row in grouped.values()],
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with mapping_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["mapping_id", "source_file", "row", "column"])
        writer.writerows(mapping_csv_rows)
    report = {
        "encoding": "utf8-visible-cells/raw-structure-v1",
        "xlsx": str(xlsx_path),
        "mapping_json": str(mapping_path),
        "mapping_csv": str(mapping_csv),
        "xlsx_size": xlsx_path.stat().st_size,
        "xlsx_size_limit": 10 * 1024 * 1024,
        "over_limit": xlsx_path.stat().st_size > 10 * 1024 * 1024,
        "pak": pak_name,
        "source_dir": str(src_dir),
        "files": len(files),
        "matched_files": len(matched_files),
        "source_rows": source_rows,
        "unique_rows": len(unique_rows),
        "prefilled_translations": prefilled_rows,
        "mapping_size": mapping_path.stat().st_size,
        "mapping_version": XLSX_MAPPING_VERSION,
        "id_scheme": "source-sha256-24-v1",
        "placeholder_style": "diamond-number-v1",
        "workers": worker_count(workers),
    }
    (out_dir / f"{base_name}_localization_export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["over_limit"]:
        raise ValueError(f"导出的 xlsx 超过 10MB：{report['xlsx_size']} bytes")
    return report


def import_xlsx_to_modified(src_dir: Path, xlsx_path: Path, mapping_path: Path, out_dir: Path, progress=None) -> dict:
    src_dir = Path(src_dir)
    xlsx_path = Path(xlsx_path)
    mapping_path = Path(mapping_path)
    out_dir = Path(out_dir)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    raw_candidate = src_dir.parent.parent / "_raw_reference" / src_dir.name
    write_source_dir = raw_candidate if raw_candidate.is_dir() else src_dir
    mapping_rows = list(_mapping_rows(mapping))
    if progress:
        progress({"phase": "xlsx-import", "percent": 3, "message": f"已读取映射表：{len(mapping_rows):,} 条去重文本"})
    rows = read_simple_xlsx(xlsx_path)
    translated = {}
    unknown = []
    valid_ids = {row_id for row_id, _source, _cells in mapping_rows}
    duplicate_ids = set()
    for row_index, row in enumerate(rows, 1):
        values = row.get("_values") or []
        row_id = str(row.get("id", "") or (values[0] if len(values) > 0 else "")).strip()
        if not row_id:
            continue
        text = row.get("text")
        if text is None:
            text = row.get("text_zh", "")
        if text is None or text == "":
            text = values[1] if len(values) > 1 else ""
        if row_id not in valid_ids:
            unknown.append(row_id)
            continue
        if row_id in translated:
            duplicate_ids.add(row_id)
        translated[row_id] = str(text or "")
        if progress and row_index % 2000 == 0:
            progress({"phase": "xlsx-import", "percent": min(12, 3 + row_index / max(1, len(rows)) * 9), "message": f"正在读取译后 XLSX：{row_index:,} / {len(rows):,}"})
    # Extra rows often appear when the translated workbook was produced from a
    # nearby/older export.  They are not dangerous as long as we only apply rows
    # that still exist in the current mapping, so keep importing the valid IDs
    # and surface the skipped count in the report instead of failing the whole
    # import.
    # The full export and the deduplicated "untranslated_xlsx" export both use
    # small sequential IDs beginning at 1, but they are different namespaces.
    # A partial untranslated workbook therefore used to look valid while its
    # translations were applied to unrelated full-export rows.  Refuse that
    # destructive mismatch before touching either resources or text_records.
    if duplicate_ids:
        raise ValueError(f"XLSX 包含重复 ID，已阻止导入：{', '.join(sorted(duplicate_ids)[:10])}")
    minimum_expected = max(1, int(len(mapping_rows) * 0.8))
    if len(translated) < minimum_expected:
        raise ValueError(
            f"XLSX 与映射表不属于同一次导出：XLSX 仅有 {len(translated):,} 个有效 ID，"
            f"完整映射有 {len(mapping_rows):,} 个 ID。请勿用‘导入润色’导入‘导出未翻译’生成的 XLSX；"
            "后者必须使用‘导回翻译’。"
        )

    used_existing_output = out_dir.exists()
    # `modified` is generated output, never a source of truth. Reusing files
    # left by an older import carries previously re-encoded paths and schema
    # fields into every later build. Regenerate a sparse delta from clean source
    # bytes on each import instead.
    out_dir.mkdir(parents=True, exist_ok=True)
    stale_entry_re = re.compile(r"^\d+_[0-9A-Fa-f]+\.[^.]+$")
    for stale in list(out_dir.rglob("*")):
        if stale.is_file() and stale_entry_re.match(stale.name):
            stale.unlink()
    updates_by_file = {}
    skipped = 0
    risks = []
    polished = 0
    missing = 0
    untranslated_unique = 0
    untranslated_cells = 0
    untranslated_examples = []
    line_cache = {}
    for mapping_index, (row_id, source_text, row_cells) in enumerate(mapping_rows, 1):
        if progress and mapping_index % 500 == 0:
            progress({"phase": "xlsx-import", "percent": min(68, 12 + mapping_index / max(1, len(mapping_rows)) * 56), "message": f"正在润色并校验：{mapping_index:,} / {len(mapping_rows):,}"})
        target = translated.get(row_id, "")
        if not target:
            missing += len(row_cells)
            skipped += len(row_cells)
            continue
        target, changed = polish_text(source_text, target, row_id)
        target_language = score_text(target)[1]
        if target == source_text or target_language in ("vi", "mixed"):
            untranslated_unique += 1
            untranslated_cells += len(row_cells)
            if len(untranslated_examples) < 30:
                untranslated_examples.append({"id": row_id, "source": source_text, "current": target, "cells": len(row_cells)})
            skipped += len(row_cells)
            continue
        if changed:
            polished += 1
        for compact_cell in row_cells:
            try:
                cell = _source_cell_meta(src_dir, mapping.get("pak") or "", compact_cell, line_cache)
            except Exception as exc:
                risks.append({"mapping_id": row_id, "reason": str(exc)})
                skipped += 1
                continue
            current_export_text = token_template(str(cell.get("source", "")))["text"]
            if current_export_text != source_text:
                risks.append({
                    "id": cell.get("id"),
                    "mapping_id": row_id,
                    "reason": "新版源文本与旧 XLSX 映射不一致，已跳过",
                    "old_source": source_text,
                    "current_source": current_export_text,
                })
                skipped += 1
                continue
            if Path(cell.get("relative_path") or cell.get("source_file") or "").suffix.lower() == ".tsv" and int(cell.get("row") or 0) == 1:
                risks.append({"id": cell.get("id"), "mapping_id": row_id, "reason": "TSV 表头行已跳过"})
                skipped += 1
                continue
            if RESOURCE_REF_RE.search(cell.get("source", "")):
                risks.append({"id": cell.get("id"), "mapping_id": row_id, "reason": "资源路径单元格已跳过"})
                skipped += 1
                continue
            restored, error = restore_xlsx_translation(target, cell, cell.get("source", ""))
            if error or restored is None:
                risks.append({"id": cell.get("id"), "mapping_id": row_id, "reason": error or "restore failed"})
                skipped += 1
                continue
            ok, reason = validate_translation(cell.get("source", ""), restored)
            if not ok:
                risks.append({"id": cell.get("id"), "mapping_id": row_id, "reason": reason})
                skipped += 1
                continue
            ok, reason = validate_import_structure(cell.get("source", ""), restored)
            if not ok:
                risks.append({"id": cell.get("id"), "mapping_id": row_id, "reason": reason})
                skipped += 1
                continue
            rel = cell.get("relative_path") or cell.get("source_file")
            updates_by_file.setdefault(rel, {})[(int(cell["row"]), int(cell["column"]))] = (restored, cell.get("encoding", ""), cell)

    updated = 0
    changed_files = []
    update_files = list(updates_by_file.items())
    for file_index, (rel, updates) in enumerate(update_files, 1):
        analysis_src = src_dir / Path(rel)
        src = write_source_dir / Path(rel)
        if not analysis_src.is_file() or not src.is_file():
            risks.append({"file": rel, "reason": "source file missing"})
            skipped += len(updates)
            continue
        dst = out_dir / Path(rel)
        # Preserve every untouched byte in the source file.  Resource paths,
        # pre-existing Chinese filenames, INI keys, and script/config fields are
        # often byte-sensitive in the client.  Normalizing the whole file here
        # can silently re-encode non-translated cells and produce crashy PAKs.
        working_bytes = src.read_bytes()
        if b"\r\n" in working_bytes:
            line_sep = b"\r\n"
        elif b"\n" in working_bytes:
            line_sep = b"\n"
        elif b"\r" in working_bytes:
            line_sep = b"\r"
        else:
            line_sep = b"\r\n"
        terminal_newline = working_bytes.endswith((b"\r\n", b"\n", b"\r"))
        raw_lines = working_bytes.splitlines()
        out_lines = []
        file_updated = 0
        for row_no, raw_line in enumerate(raw_lines, 1):
            if src.suffix.lower() == ".lua":
                replacements = {}
                for (update_row, update_col), (target_text, encoding, _cell) in updates.items():
                    if update_row != row_no:
                        continue
                    literal = next((x for x in iter_lua_text_parts(raw_line) if x.ordinal == update_col), None)
                    if literal is None:
                        risks.append({"file": rel, "row": row_no, "column": update_col, "reason": "Lua string missing"})
                        skipped += 1
                        continue
                    try:
                        replacements[update_col] = encode_cell(target_text, literal.content, encoding)
                    except Exception as exc:
                        risks.append({"file": rel, "row": row_no, "column": update_col, "reason": str(exc)})
                        skipped += 1
                new_line, applied = replace_lua_text_parts(raw_line, replacements)
                out_lines.append(new_line)
                file_updated += len(applied)
                continue
            cells = raw_line.split(b"\t")
            for col_no in range(1, len(cells) + 1):
                key = (row_no, col_no)
                if key not in updates:
                    continue
                target_text, encoding, cell = updates[key]
                try:
                    if cell.get("mode") == "key_value":
                        match = KV_RE.match(raw_line)
                        original = match.group(2) if match else cells[col_no - 1]
                        encoded = encode_cell(target_text, original, encoding)
                        cells[col_no - 1] = (match.group(1) if match else b"") + encoded
                    else:
                        cells[col_no - 1] = encode_cell(target_text, cells[col_no - 1], encoding)
                    file_updated += 1
                except Exception as exc:
                    risks.append({"file": rel, "row": row_no, "column": col_no, "reason": str(exc)})
                    skipped += 1
            out_lines.append(b"\t".join(cells))
        if file_updated:
            rebuilt = line_sep.join(out_lines)
            if terminal_newline and out_lines:
                rebuilt += line_sep
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(rebuilt)
            changed_files.append(Path(rel).as_posix())
            updated += file_updated
        if progress:
            progress({"phase": "xlsx-import", "percent": min(84, 68 + file_index / max(1, len(update_files)) * 16), "message": f"正在回写文本资源：{file_index:,} / {len(update_files):,} 个文件"})
    report = {
        "encoding": "force-utf8-v7",
        "xlsx": str(xlsx_path),
        "mapping_json": str(mapping_path),
        "output_dir": str(out_dir),
        "write_source_dir": str(write_source_dir),
        "raw_byte_source": write_source_dir == raw_candidate,
        "preserved_existing_output": used_existing_output,
        "source_rows": mapping.get("source_rows", 0),
        "unique_rows": mapping.get("unique_rows", 0),
        "translated_rows": len(translated),
        "unknown_xlsx_ids": len(unknown),
        "unknown_xlsx_id_examples": unknown[:50],
        "updated": updated,
        "changed_file_count": len(changed_files),
        "changed_files": changed_files,
        "missing": missing,
        "untranslated_unique": untranslated_unique,
        "untranslated_cells": untranslated_cells,
        "untranslated_examples": untranslated_examples,
        "skipped": skipped,
        "polished": polished,
        "risk_count": len(risks),
        "risks": risks[:100],
    }
    (out_dir / "_xlsx_localization_import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress({"phase": "xlsx-import", "percent": 85, "message": f"资源回写完成：{updated:,} 个单元格，正在同步界面"})
    return report


def apply_xlsx_to_records(records_path: Path, xlsx_path: Path, mapping_path: Path, pak_name: str, progress=None) -> dict:
    records_path = Path(records_path)
    records = json.loads(records_path.read_text(encoding="utf-8"))
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8-sig"))
    mapping_rows = list(_mapping_rows(mapping))
    translated = {}
    valid_ids = {row_id for row_id, _source, _cells in mapping_rows}
    unknown = []
    duplicate_ids = set()
    for row in read_simple_xlsx(xlsx_path):
        values = row.get("_values") or []
        row_id = str(row.get("id", "") or (values[0] if len(values) > 0 else "")).strip()
        text = row.get("text")
        if text is None:
            text = row.get("text_zh", "")
        if text is None or text == "":
            text = values[1] if len(values) > 1 else ""
        if not row_id:
            continue
        if row_id not in valid_ids:
            unknown.append(row_id)
            continue
        if row_id in translated:
            duplicate_ids.add(row_id)
        translated[row_id] = str(text or "")
    if duplicate_ids:
        raise ValueError(f"XLSX 包含重复 ID，已阻止界面同步：{', '.join(sorted(duplicate_ids)[:10])}")
    minimum_expected = max(1, int(len(mapping_rows) * 0.8))
    if len(translated) < minimum_expected:
        raise ValueError(
            f"XLSX 与映射表不属于同一次导出：XLSX 仅有 {len(translated):,} 个有效 ID，"
            f"映射表有 {len(mapping_rows):,} 个 ID。已阻止界面错位。"
        )
    updates = {}
    source_dir = Path(mapping.get("extracted_dir") or "")
    line_cache = {}
    records_by_location = {
        (str(rec.get("source_file") or ""), int(rec.get("line") or 0), int(rec.get("column") or 0)): rec
        for rec in records if rec.get("pak") == pak_name
    }
    for mapping_index, (row_id, source_text, row_cells) in enumerate(mapping_rows, 1):
        if progress and mapping_index % 1000 == 0:
            progress({"phase": "xlsx-import", "percent": min(92, 85 + mapping_index / max(1, len(mapping_rows)) * 7), "message": f"正在准备界面同步：{mapping_index:,} / {len(mapping_rows):,}"})
        target = translated.get(row_id, "")
        if not target:
            continue
        target, _changed = polish_text(source_text, target, row_id)
        if target == source_text or score_text(target)[1] in ("vi", "mixed"):
            continue
        for cell in row_cells:
            rel = cell.get("relative_path") or cell.get("source_file") or ""
            if Path(rel).suffix.lower() == ".tsv" and int(cell.get("row") or 0) == 1:
                continue
            rec = records_by_location.get((Path(rel).name, int(cell["row"]), int(cell["column"])))
            if rec is None:
                continue
            if "source" in cell and "tokens" in cell and "template" in cell:
                meta = cell
            elif source_dir.is_dir():
                try:
                    meta = _source_cell_meta(source_dir, pak_name, cell, line_cache)
                except Exception:
                    original = rec.get("source_original") or rec.get("original") or ""
                    template = token_template(original)
                    meta = {"tokens": template["tokens"], "template": template["template"]}
            else:
                original = rec.get("source_original") or rec.get("original") or ""
                template = token_template(original)
                meta = {"tokens": template["tokens"], "template": template["template"]}
            restored, error = restore_xlsx_translation(
                target,
                meta,
                meta.get("source", rec.get("source_original", "")),
            )
            if error or restored is None:
                continue
            current_source = str(meta.get("source", rec.get("source_original", "")))
            current_template = token_template(current_source)["text"]
            if current_template != source_text:
                continue
            if RESOURCE_REF_RE.search(meta.get("source", rec.get("source_original", ""))):
                continue
            ok, _reason = validate_import_structure(meta.get("source", rec.get("source_original", "")), restored)
            if ok:
                updates[rec.get("id")] = restored
    updated = changed = 0
    status_counts = {"已翻译": 0, "未翻译": 0}
    live_updates = []
    pak_records = [rec for rec in records if rec.get("pak") == pak_name]
    for record_index, rec in enumerate(pak_records, 1):
        target = updates.get(rec.get("id"))
        if rec.get("source_original") is None:
            rec["source_original"] = rec.get("original", "")
        if target is not None:
            if rec.get("original") != target:
                changed += 1
                rec.pop("review_status", None)
                rec.pop("review_signature", None)
                rec.pop("reviewed_at", None)
            rec["original"] = target
            updated += 1
        current = rec.get("original", "")
        rec["language"] = score_text(current)[1]
        rec["status"] = "已翻译" if current != rec.get("source_original", "") else "未翻译"
        status_counts[rec["status"]] += 1
        if target is not None:
            live_updates.append({"id": rec.get("id"), "text": current, "language": rec["language"], "status": rec["status"]})
        if progress and (len(live_updates) >= 200 or record_index == len(pak_records)):
            progress({
                "phase": "xlsx-import",
                "percent": min(99.8, 92 + record_index / max(1, len(pak_records)) * 7.8),
                "message": f"正在实时更新界面：{record_index:,} / {len(pak_records):,}",
                "updates": live_updates,
                "status_counts": status_counts,
            })
            live_updates = []
    records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if progress:
        progress({"phase": "xlsx-import", "percent": 100, "message": "界面同步完成", "status_counts": status_counts})
    return {"records_path": str(records_path), "xlsx": str(xlsx_path), "pak": pak_name, "updated": updated, "changed": changed, "status_counts": status_counts}
