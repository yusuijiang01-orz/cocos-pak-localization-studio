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
import unicodedata
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
    PROTECTED_RE,
)
from lua_localization import iter_lua_text_parts, replace_lua_text_parts
from localization_analyzer import (VI_CHARS, decode_best, score_text, is_localizable_text_path,
                                   is_structural_translation_payload)
from localization_tm import internal_text, validate_tokens as validate_build_tokens
from parallel_config import worker_count

XLSX_MAPPING_VERSION = 6
LEGACY_XLSX_MAPPING_VERSIONS = {3, 4, 5}
XLSX_MAPPING_MODE = "xlsx-dedup-cells-compact"
XLSX_SCOPE = "player-visible-only"

_STRUCTURAL_XLSX_RE = re.compile(
    r"(?:^\s*\{.*\}\s*$)"
    r"|(?:\]\s*\*\*\s*[{}]|\*\*\s*[{}])"
    r"|(?:^\s*(?:px\)\s*)?\{.*\}\s*$)"
    r"|(?:\b(?:function|local|return|elseif|then|end)\b\s*[^\n]*[=(){};])",
    re.I | re.S,
)
_UNSAFE_IMPORT_TEXT_RE = re.compile(r"[<>{}\[\]/\\#$%`=;|&^]")
_LATIN_OR_VIETNAMESE_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]")
_HAN_RE = re.compile(r"[\u3400-\u9FFF]")
_MOJIBAKE_PREFIX_RE = re.compile(r"^\s*(?:Ă|Ã|Â|Ä|Å|Æ){2,}")
_CSS_DECLARATION_PREFIX_RE = re.compile(
    r"^\s*(?:align-content|align-items|align-self|background(?:-[\w-]+)?|border(?:-[\w-]+)?|"
    r"bottom|clear|color|column(?:-[\w-]+)?|display|float|font(?:-[\w-]+)?|height|left|"
    r"line-height|margin(?:-[\w-]+)?|max-(?:width|height)|min-(?:width|height)|opacity|"
    r"overflow(?:-[\w-]+)?|padding(?:-[\w-]+)?|position|right|text-align|text-decoration|"
    r"text-shadow|top|transform|vertical-align|visibility|white-space|width|z-index)\s*:",
    re.I,
)

def _is_structural_xlsx_text(text: str) -> bool:
    s = (text or '').strip()
    if not s:
        return False
    if is_structural_translation_payload(s):
        return True
    if "\ufffd" in s or _MOJIBAKE_PREFIX_RE.match(s):
        return True
    if s.startswith("=") or _CSS_DECLARATION_PREFIX_RE.match(s):
        return True
    if s.startswith("#") and len(s) > 1 and _is_structural_xlsx_text(s[1:]):
        return True
    if ((s.startswith('{') and s.endswith('}')) or (s.startswith('[') and s.endswith(']'))) and (':' in s or '"' in s or "'" in s):
        return True
    return bool(_STRUCTURAL_XLSX_RE.search(s))

def _is_safe_pure_chinese_import(text: str) -> bool:
    """Accept only a plain Chinese translation cell for the strict safe flow."""
    s = (text or "").strip()
    return bool(s and _HAN_RE.search(s)
                and not _UNSAFE_IMPORT_TEXT_RE.search(s)
                and not _LATIN_OR_VIETNAMESE_RE.search(s))
MULTI_XLSX_MAPPING_VERSION = 7
MULTI_XLSX_MAPPING_MODE = "multi-pak-out-of-band-skeleton"
TRANSPORT_MARKER_RE = re.compile(r"(?:◈|\{P\s*\d+\}|ZXQROW|<\/?ph(?:\s|>|/))", re.I)
GENERATED_ASCII_DIGIT_RE = re.compile(r"\d")
OUT_OF_BAND_PROTECTED_RE = re.compile(
    # Real runtime tags are covered by PROTECTED_RE.  Decorative player text
    # such as <Chiến> is split into protected brackets plus a translatable
    # inner word, instead of hiding that visible word from Google forever.
    rf"(?:#[A-Za-z]\d+[-+]?)|(?:{PROTECTED_RE.pattern})|(?:[<>])|(?:\r\n|\r|\n|\t|\\n|\\r|\\t|[/\\])"
    r"|(?:\d+(?:[.:]\d+)?%?)|(?:[{}\[\]#$%=;|&^`:+*,.!?\"'()_-])",
    re.UNICODE,
)
GLOSSARY_WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]+|[\u3400-\u9FFF]+", re.UNICODE)
GLOSSARY_TITLE_PHRASE_RE = re.compile(
    r"(?:[A-ZÀ-ÖØ-ÞĂÂĐÊÔƠƯ][A-Za-zÀ-ỹ]*)(?:\s+[A-ZÀ-ÖØ-ÞĂÂĐÊÔƠƯ][A-Za-zÀ-ỹ]*){1,7}",
    re.UNICODE,
)
CJK_TERM_RE = re.compile(r"^[\u3400-\u9FFF]+$")
EXTRA_GLOSSARY_SEED_TERMS = (
    "Khương Tử Nha", "姜子牙",
    "Phong Thần Đài", "封神台",
    "Na Tra", "哪吒",
    "Lý Tịnh", "李靖",
    "Đắc Kỷ", "妲己",
    "Trụ Vương", "纣王",
    "的", "了", "呢", "嗯", "啊", "吧", "吗",
)
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
    """Yield (id, source text, cells) for compact v6 and legacy mappings."""
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
    if mapping.get("version") in {4, 5} and mapping.get("mode") == XLSX_MAPPING_MODE:
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
PLACEHOLDER_MARKER_RE = re.compile(r"\{P\d+\}|◈|ZXQROW|<\/?ph(?:\s|>|/)", re.I)
CONTROL_ANGLE_NAME_RE = re.compile(r"^/?(?:c|color|font|size|img|image|sprite|br|b|i|u|p|a|style|script)\b", re.I)


def structural_angle_tags(text: str) -> list[str]:
    # Only known UI/control tags are runtime structure.  Decorative equipment
    # prefixes such as <Chiến> are visible words and may be translated while
    # their angle brackets are restored out of band.
    tags = []
    for tag in ANGLE_TAG_RE.findall(str(text or "")):
        inner = tag[1:-1].strip()
        if CONTROL_ANGLE_NAME_RE.match(inner) or re.fullmatch(r"(?:enter|\\n|n)", inner, re.I):
            tags.append(tag)
    return tags


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


def export_xlsx_mapping(src_dir: Path, out_dir: Path, base_name: str, pak_name: str, workers: int | None = None, records_path: Path | None = None, source_files: list[Path] | None = None, records_data: list[dict] | None = None, metadata_dir: Path | None = None, include_source_file: bool = False) -> dict:
    src_dir = Path(src_dir)
    out_dir = Path(out_dir)
    metadata_dir = Path(metadata_dir) if metadata_dir is not None else out_dir
    files = sorted(Path(p) for p in source_files) if source_files is not None else sorted(p for p in src_dir.rglob("*") if is_localizable_text_path(p))
    unique_rows = []
    grouped: dict[str, dict] = {}
    file_indexes: dict[str, int] = {}
    mapping_csv_rows = []
    source_rows = 0
    excluded_not_visible = 0
    matched_files = set()
    if records_data is not None:
        studio_records = records_data
    elif records_path and Path(records_path).is_file():
        studio_records = json.loads(Path(records_path).read_text(encoding="utf-8"))
    else:
        raise ValueError("缺少玩家可见记录数据库，已阻止无白名单 XLSX 导出。请从 Studio 项目内重新导出。")
    records_by_location = {
        (str(rec.get("source_file") or ""), int(rec.get("line") or 0), int(rec.get("column") or 0)): rec
        for rec in studio_records
        if rec.get("pak") == pak_name and rec.get("_isPlayerVisible", True) is True
    }
    records_by_file: dict[str, list[dict]] = {}
    for (source_file, _row_no, _col_no), record in records_by_location.items():
        records_by_file.setdefault(source_file, []).append(record)
    for path in files:
        relative = path.relative_to(src_dir).as_posix()
        file_index = file_indexes.setdefault(relative, len(file_indexes))
        file_hit = False
        visible_records = sorted(
            records_by_file.get(path.name, []),
            key=lambda rec: (int(rec.get("line") or 0), int(rec.get("column") or 0)),
        )
        visible_positions = {
            (int(rec.get("line") or 0), int(rec.get("column") or 0))
            for rec in visible_records
        }
        # Keep this audit counter for old scanner false positives, but never use
        # those guesses as export input. The canonical record allowlist below is
        # the sole source of workbook rows, so visible content cannot be lost to
        # a second, slightly different filter either.
        scanned_positions = {
            (int(cell["row"]), int(cell["column"]))
            for cell in iter_translatable_cells(path, pak_name)
        }
        excluded_not_visible += len(scanned_positions - visible_positions)
        for rec in visible_records:
            source = str(rec.get("source_original", rec.get("original", "")) or "")
            if not source:
                continue
            # Defense in depth: old projects may contain records admitted by a
            # pre-fix analyzer. Never export JSON/script/control payloads.
            if _is_structural_xlsx_text(source):
                excluded_not_visible += 1
                continue
            row_no = int(rec.get("line") or 0)
            col_no = int(rec.get("column") or 0)
            text = token_template(source)["text"]
            item = grouped.get(text)
            if item is None:
                item = {"id": _content_mapping_id(text), "text": text, "cells": []}
                grouped[text] = item
                unique_rows.append({"id": item["id"], "text": text})
            item["cells"].append([file_index, row_no, col_no])
            mapping_csv_rows.append((item["id"], relative, row_no, col_no))
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
    if records_by_location:
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

    # The filename column is informational only. A deduplicated sentence can
    # occur in several resources, so list every relative source path while the
    # stable ID + mapping remains the sole authority during import.
    if include_source_file:
        files_by_index = list(file_indexes.keys())
        output_rows = {row["id"]: row for row in unique_rows}
        for item in grouped.values():
            source_names = list(dict.fromkeys(
                files_by_index[int(file_index)]
                for file_index, _row_no, _col_no in item["cells"]
            ))
            output_rows[item["id"]]["source_file"] = " | ".join(source_names)

    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"{base_name}_localization.xlsx"
    mapping_path = metadata_dir / f"{base_name}_localization_mapping.json"
    mapping_csv = metadata_dir / f"{base_name}_localization_mapping.csv"
    headers = ["id", "source_file", "text"] if include_source_file else ["id", "text"]
    write_simple_xlsx(xlsx_path, unique_rows, headers)
    mapping = {
        "version": XLSX_MAPPING_VERSION,
        "mode": XLSX_MAPPING_MODE,
        "scope": XLSX_SCOPE,
        "pak": pak_name,
        "base_name": base_name,
        "extracted_dir": str(src_dir.resolve()),
        "source_rows": source_rows,
        "excluded_not_player_visible": excluded_not_visible,
        "unique_rows": len(unique_rows),
        "prefilled_translations": prefilled_rows,
        "id_scheme": "source-sha256-24-v1",
        "includes_source_file_column": include_source_file,
        "files": list(file_indexes.keys()),
        "rows": [[row["id"], row["text"], row["cells"]] for row in grouped.values()],
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if records_by_location:
        files_by_index = list(file_indexes.keys())
        records_mapping = {}
        for item in grouped.values():
            entries = []
            for file_index, row_no, col_no in item['cells']:
                rel = files_by_index[int(file_index)]
                rec = records_by_location.get((Path(rel).name, int(row_no), int(col_no)))
                if not rec:
                    continue
                source = str(rec.get('source_original') or rec.get('original') or '')
                meta = token_template(source)
                entries.append({'id': str(rec.get('id') or ''), 'source': source, 'tokens': meta.get('tokens') or [], 'template': meta.get('template') or '{TEXT}', 'export_text': meta.get('text') or source, 'dollar_spaced': bool(meta.get('dollar_spaced'))})
            records_mapping[item['id']] = entries
        records_mapping_path = metadata_dir / f"{base_name}_localization_records_mapping.json"
        records_mapping_path.write_text(json.dumps(records_mapping, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
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
        "excluded_not_player_visible": excluded_not_visible,
        "unique_rows": len(unique_rows),
        "prefilled_translations": prefilled_rows,
        "mapping_size": mapping_path.stat().st_size,
        "mapping_version": XLSX_MAPPING_VERSION,
        "scope": XLSX_SCOPE,
        "id_scheme": "source-sha256-24-v1",
        "placeholder_style": "diamond-number-v1",
        "includes_source_file_column": include_source_file,
        "workers": worker_count(workers),
    }
    (metadata_dir / f"{base_name}_localization_export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # A complete workbook is an explicit user-requested archive/export. Keep
    # the 10 MB flag as a Google service compatibility warning, but do not make
    # Studio fail or silently omit content because the package is larger.
    if report["over_limit"] and not include_source_file:
        raise ValueError(f"导出的 xlsx 超过 10MB：{report['xlsx_size']} bytes")
    return report


def export_full_xlsx(src_dir: Path, out_dir: Path, base_name: str, pak_name: str, records_path: Path, metadata_dir: Path | None = None) -> dict:
    """Export every player-visible translation unit into one auditable workbook."""
    report = export_xlsx_mapping(
        src_dir,
        out_dir,
        base_name,
        pak_name,
        workers=worker_count(),
        records_path=records_path,
        metadata_dir=metadata_dir,
        include_source_file=True,
    )
    report["mode"] = "single-player-visible-workbook"
    return report


def _segment_id(text: str) -> str:
    return "s" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _has_natural_language(text: str) -> bool:
    return bool(re.search(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff\u3400-\u9fff]", text or ""))


def _out_of_band_skeleton(text: str) -> list[list[str]]:
    """Split text so protected runtime tokens never enter the workbook."""
    text = str(text or "")
    pieces: list[list[str]] = []
    cursor = 0

    def add_literal(value: str, kind: str = "k") -> None:
        if not value:
            return
        if pieces and pieces[-1][0] == kind:
            pieces[-1][1] += value
        else:
            pieces.append([kind, value])

    def add_span(value: str) -> None:
        if not value:
            return
        if _is_structural_xlsx_text(value):
            add_literal(value)
            return
        if _has_natural_language(value):
            core_start = len(value) - len(value.lstrip())
            core_end = len(value.rstrip())
            add_literal(value[:core_start])
            core = value[core_start:core_end]
            if core:
                pieces.append(["t", _segment_id(core), core])
            add_literal(value[core_end:])
        else:
            add_literal(value)

    for match in OUT_OF_BAND_PROTECTED_RE.finditer(text):
        add_span(text[cursor:match.start()])
        add_literal(match.group(0), "p")
        cursor = match.end()
    add_span(text[cursor:])
    return pieces


def export_multi_pak_full_xlsx(records_path: Path, out_dir: Path, base_name: str,
                               metadata_dir: Path | None = None,
                               pak_names: list[str] | None = None,
                               remaining_only: bool = False) -> dict:
    """Export all visible records from multiple PAKs as one token-free workbook."""
    records_path = Path(records_path)
    out_dir = Path(out_dir)
    metadata_dir = Path(metadata_dir) if metadata_dir is not None else out_dir
    selected = set(pak_names or [])
    records = json.loads(records_path.read_text(encoding="utf-8"))
    # Old projects can contain records produced before the structural filter
    # existed.  Multi-PAK export must enforce the same boundary as per-file
    # export; otherwise JSON/script records re-enter the workbook here.
    visible = [rec for rec in records
               if rec.get("_isPlayerVisible", True) is True
               and not _is_structural_xlsx_text(str(rec.get("source_original") or rec.get("original") or ""))
               and (not selected or rec.get("pak") in selected)]
    if not visible:
        raise ValueError("没有检测到可导出的玩家可见文本")

    segments: dict[str, dict] = {}
    mapped_records = []
    paks = []
    for rec in visible:
        pak = str(rec.get("pak") or "")
        if pak and pak not in paks:
            paks.append(pak)
        source = str(rec.get("source_original") or rec.get("original") or "")
        skeleton = _out_of_band_skeleton(source)
        if not any(piece[0] == "t" for piece in skeleton):
            continue
        for piece in skeleton:
            if piece[0] != "t":
                continue
            segment_id, segment_source = piece[1], piece[2]
            item = segments.setdefault(segment_id, {
                "id": segment_id, "source": segment_source, "current": segment_source,
                "pak": pak, "source_file": str(rec.get("source_file") or ""), "uses": 0,
            })
            item["uses"] += 1
        mapped_records.append([
            rec.get("id"), pak, str(rec.get("source_file") or ""),
            int(rec.get("line") or 0), int(rec.get("column") or 0), source, skeleton,
        ])

    # Carry over a current translation only when it has the exact same protected
    # skeleton. This avoids retranslating already finished Chinese while never
    # trusting a historical string whose tokens were damaged.
    candidates: dict[str, dict[str, int]] = {}
    remaining_values: dict[str, dict[str, int]] = {}
    for rec, mapped in zip([r for r in visible if any(p[0] == "t" for p in _out_of_band_skeleton(str(r.get("source_original") or r.get("original") or "")))], mapped_records):
        current = str(rec.get("original") or "")
        source = mapped[5]
        if TRANSPORT_MARKER_RE.search(current):
            continue
        current_skeleton = _out_of_band_skeleton(current)
        source_skeleton = mapped[6]
        source_literals = [p[1] for p in source_skeleton if p[0] == "p"]
        current_literals = [p[1] for p in current_skeleton if p[0] == "p"]
        source_texts = [p for p in source_skeleton if p[0] == "t"]
        current_texts = [p for p in current_skeleton if p[0] == "t"]
        if source_literals != current_literals or len(source_texts) != len(current_texts):
            if any(ch in VI_CHARS for ch in current):
                for src_piece in source_texts:
                    if any(ch in VI_CHARS for ch in src_piece[2]):
                        bucket = remaining_values.setdefault(src_piece[1], {})
                        bucket[src_piece[2]] = bucket.get(src_piece[2], 0) + 1
            continue
        for src_piece, cur_piece in zip(source_texts, current_texts):
            value = cur_piece[2]
            if any(ch in VI_CHARS for ch in value):
                bucket = remaining_values.setdefault(src_piece[1], {})
                bucket[value] = bucket.get(value, 0) + 1
            # Google-facing cells must contain natural language only.  A
            # historical translation may have injected a number, tag, path,
            # placeholder or other runtime literal that did not exist in this
            # source segment.  Never carry that unsafe text into a new XLSX.
            if (current != source and value
                    and not OUT_OF_BAND_PROTECTED_RE.search(value)
                    and not _is_structural_xlsx_text(value)
                    and _is_safe_pure_chinese_import(value)):
                bucket = candidates.setdefault(src_piece[1], {})
                bucket[value] = bucket.get(value, 0) + 1
    prefilled = 0
    for segment_id, bucket in candidates.items():
        if bucket and segment_id in segments:
            segments[segment_id]["current"] = max(bucket.items(), key=lambda item: item[1])[0]
            prefilled += 1

    all_rows = [{"id": item["id"], "pak": item["pak"], "source_file": item["source_file"], "text": item["current"]}
                for item in segments.values()]
    if remaining_only:
        rows = []
        for row in all_rows:
            bucket = remaining_values.get(row["id"])
            if bucket:
                # Export the clean canonical source segment, not a partially
                # translated or mojibake current value. The bucket is used only
                # to prove that this source segment still has Vietnamese uses.
                rows.append({**row, "text": segments[row["id"]]["source"]})
    else:
        rows = all_rows
    workbook_values = {row["id"]: row["text"] for row in rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"{base_name}_localization.xlsx"
    mapping_path = metadata_dir / f"{base_name}_localization_mapping.json"
    write_simple_xlsx(xlsx_path, rows, headers=["id", "pak", "source_file", "text"])
    mapping = {
        "version": MULTI_XLSX_MAPPING_VERSION,
        "mode": MULTI_XLSX_MAPPING_MODE,
        "scope": XLSX_SCOPE,
        "placeholder_strategy": "out-of-band-exact-skeleton-v1",
        "paks": paks,
        "source_records": len(visible),
        "mapped_records": len(mapped_records),
        "unique_rows": len(all_rows),
        "workbook_rows": len(rows),
        "remaining_only": remaining_only,
        # Keep the exact exported value so an untouched Google row can be
        # distinguished from a genuinely translated row during import.
        "rows": [[item["id"], item["source"], workbook_values.get(item["id"], item["current"])]
                 for item in segments.values()],
        "records": mapped_records,
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "mode": MULTI_XLSX_MAPPING_MODE, "scope": XLSX_SCOPE, "paks": paks,
        "pak_count": len(paks), "source_rows": len(visible), "mapped_records": len(mapped_records),
        "unique_rows": len(all_rows), "workbook_rows": len(rows),
        "remaining_only": remaining_only, "prefilled_translations": prefilled,
        "xlsx": str(xlsx_path.resolve()), "mapping": str(mapping_path.resolve()),
        "placeholder_style": "out-of-band; no runtime placeholder is exported",
    }
    (metadata_dir / f"{base_name}_localization_export_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _glossary_term_key(term: str) -> str:
    return unicodedata.normalize("NFC", str(term or "").strip()).casefold()


EXTRA_GLOSSARY_SEED_KEYS = {_glossary_term_key(term) for term in EXTRA_GLOSSARY_SEED_TERMS}


def _prefer_glossary_text(current: str, candidate: str) -> bool:
    """Return True when candidate is a better display form for the same term key."""
    current = str(current or "")
    candidate = str(candidate or "")
    if not candidate:
        return False
    if _glossary_term_key(candidate) in EXTRA_GLOSSARY_SEED_KEYS:
        return candidate in EXTRA_GLOSSARY_SEED_TERMS and current not in EXTRA_GLOSSARY_SEED_TERMS
    if len(candidate) != len(current):
        return len(candidate) > len(current)
    return sum(ch.isupper() for ch in candidate) > sum(ch.isupper() for ch in current)


@functools.lru_cache(maxsize=1)
def _known_glossary_terms() -> tuple[str, ...]:
    terms: set[str] = set()
    terms.update(EXTRA_GLOSSARY_SEED_TERMS)
    try:
        from api_translator import CORE_GLOSSARY
        for entry in str(CORE_GLOSSARY).split(";"):
            if "=" not in entry:
                continue
            source, target = entry.split("=", 1)
            source = source.strip()
            target = target.strip()
            if source:
                terms.add(source)
            if target:
                terms.add(target)
    except Exception:
        pass
    try:
        from polish_localizer import TERM
        for source, target in TERM.items():
            source = str(source or "").strip()
            target = str(target or "").strip()
            if source:
                terms.add(source)
            if target:
                terms.add(target)
    except Exception:
        pass
    clean = []
    for term in terms:
        if _is_structural_xlsx_text(term) or OUT_OF_BAND_PROTECTED_RE.search(term):
            continue
        if CJK_TERM_RE.fullmatch(term) and len(term) > 8:
            continue
        if len(term) == 1 and term not in VI_CHARS and not CJK_TERM_RE.fullmatch(term):
            continue
        clean.append(unicodedata.normalize("NFC", term))
    return tuple(sorted(set(clean), key=lambda value: (-len(value), value.casefold())))


def _iter_glossary_terms(text: str):
    """Yield safe standalone words from one already token-free text segment."""
    text = str(text or "")
    for match in GLOSSARY_TITLE_PHRASE_RE.finditer(text):
        term = unicodedata.normalize("NFC", match.group(0).strip())
        if term and not _is_structural_xlsx_text(term) and not OUT_OF_BAND_PROTECTED_RE.search(term):
            yield term
    for match in GLOSSARY_WORD_RE.finditer(str(text or "")):
        term = unicodedata.normalize("NFC", match.group(0).strip())
        if not term:
            continue
        if _is_structural_xlsx_text(term) or OUT_OF_BAND_PROTECTED_RE.search(term):
            continue
        if CJK_TERM_RE.fullmatch(term) and len(term) > 8:
            continue
        # Short all-caps labels are usually protocol/UI control abbreviations
        # such as PK/STCB. They are visible, but unsafe for blind glossary
        # replacement because translating them can alter game semantics.
        if term.isascii() and term.isupper() and len(term) <= 4:
            continue
        if len(term) == 1 and term not in VI_CHARS and not CJK_TERM_RE.fullmatch(term):
            continue
        yield term


def export_multi_pak_glossary_xlsx(records_path: Path, out_dir: Path, base_name: str,
                                   metadata_dir: Path | None = None,
                                   pak_names: list[str] | None = None) -> dict:
    """Export a deduplicated standalone term glossary from safe visible text."""
    records_path = Path(records_path)
    out_dir = Path(out_dir)
    metadata_dir = Path(metadata_dir) if metadata_dir is not None else out_dir
    selected = set(pak_names or [])
    records = json.loads(records_path.read_text(encoding="utf-8"))
    visible = [rec for rec in records
               if rec.get("_isPlayerVisible", True) is True
               and not _is_structural_xlsx_text(str(rec.get("source_original") or rec.get("original") or ""))
               and (not selected or rec.get("pak") in selected)]
    if not visible:
        raise ValueError("没有检测到可导出的玩家可见文本")

    terms: dict[str, dict] = {}
    paks = []
    scanned_segments = 0
    for term in _known_glossary_terms():
        key = _glossary_term_key(term)
        if not key:
            continue
        item = terms.setdefault(key, {
            "id": "g" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
            "text": term,
            "uses": 0,
            "pak": "",
            "source_file": "",
            "example": "seed glossary",
        })
        if _prefer_glossary_text(str(item.get("text") or ""), term):
            item["text"] = term
    for rec in visible:
        pak = str(rec.get("pak") or "")
        if pak and pak not in paks:
            paks.append(pak)
        text_values = []
        for value in (rec.get("source_original"), rec.get("original")):
            value = str(value or "")
            if value and value not in text_values:
                text_values.append(value)
        for source in text_values:
            if _is_structural_xlsx_text(source):
                continue
            for piece in _out_of_band_skeleton(source):
                if piece[0] != "t":
                    continue
                scanned_segments += 1
                segment = piece[2]
                for term in _iter_glossary_terms(segment):
                    key = _glossary_term_key(term)
                    if not key:
                        continue
                    item = terms.setdefault(key, {
                        "id": "g" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
                        "text": term,
                        "uses": 0,
                        "pak": pak,
                        "source_file": str(rec.get("source_file") or ""),
                        "example": segment,
                    })
                    item["uses"] += 1
                    if _prefer_glossary_text(str(item.get("text") or ""), term):
                        item["text"] = term

    rows = sorted(terms.values(), key=lambda item: (-int(item["uses"]), item["text"].casefold()))
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"{base_name}_localization.xlsx"
    write_simple_xlsx(
        xlsx_path,
        [{"text": item["text"]} for item in rows],
        headers=["text"],
        sheet_name="术语库",
    )
    mapping_path = metadata_dir / f"{base_name}_localization_mapping.json"
    mapping = {
        "version": 1,
        "mode": "multi-pak-safe-term-glossary",
        "scope": XLSX_SCOPE,
        "placeholder_strategy": "glossary-terms-no-resource-import",
        "paks": paks,
        "source_records": len(visible),
        "scanned_segments": scanned_segments,
        "unique_rows": len(rows),
        "workbook_rows": len(rows),
        "headers": ["text"],
        "rows": [[item["id"], item["text"], item["text"]] for item in rows],
        "terms": [{"id": item["id"], "text": item["text"], "uses": item["uses"]} for item in rows],
    }
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "mode": "multi-pak-safe-term-glossary",
        "scope": XLSX_SCOPE,
        "paks": paks,
        "pak_count": len(paks),
        "source_rows": len(visible),
        "scanned_segments": scanned_segments,
        "unique_terms": len(rows),
        "xlsx": str(xlsx_path.resolve()),
        "mapping": str(mapping_path.resolve()),
        "placeholder_style": "tags, paths, variables, numbers and punctuation are never exported as terms",
    }
    (metadata_dir / f"{base_name}_localization_export_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def apply_multi_pak_full_xlsx_to_records(records_path: Path, xlsx_path: Path,
                                         mapping_path: Path, progress=None) -> dict:
    """Import v7 workbook and rebuild every source string from its exact skeleton."""
    records_path = Path(records_path)
    records = json.loads(records_path.read_text(encoding="utf-8"))
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8-sig"))
    if mapping.get("version") != MULTI_XLSX_MAPPING_VERSION or mapping.get("mode") != MULTI_XLSX_MAPPING_MODE:
        raise ValueError("这不是多 PAK 免占位符完整 XLSX 的配套映射")
    valid_rows = {
        str(row[0]): {"source": str(row[1]), "exported": str(row[2] if len(row) > 2 else row[1])}
        for row in mapping.get("rows", [])
    }
    translated = {}
    duplicate_ids = set()
    unknown = []
    workbook_rows = read_simple_xlsx(xlsx_path)
    blank_xlsx_rows = 0
    for row in workbook_rows:
        values = row.get("_values") or []
        row_id = str(row.get("id", "") or (values[0] if values else "")).strip()
        value = row.get("text")
        if value is None or value == "":
            value = values[3] if len(values) > 3 else ""
        if not row_id:
            continue
        if row_id not in valid_rows:
            unknown.append(row_id)
            continue
        if row_id in translated:
            duplicate_ids.add(row_id)
        translated[row_id] = str(value or "")
        if not str(value or "").strip():
            blank_xlsx_rows += 1
    if duplicate_ids:
        raise ValueError(f"XLSX 包含重复 ID，已阻止错位导入：{', '.join(sorted(duplicate_ids)[:10])}")

    translated_xlsx_ids = {
        row_id for row_id, value in translated.items()
        if value != valid_rows[row_id]["exported"]
    }
    recognized_xlsx_rows = len(translated)
    translated_xlsx_rows = len(translated_xlsx_ids)
    unchanged_xlsx_rows = recognized_xlsx_rows - translated_xlsx_rows
    if not recognized_xlsx_rows:
        detail = f"；有 {len(unknown)} 个 ID 与配套 config 不匹配" if unknown else ""
        raise ValueError(f"所选 XLSX 没有任何可识别的导出行{detail}。请选择该表导出时配套的 config。")
    if mapping.get("remaining_only") and translated_xlsx_rows == 0:
        raise ValueError(
            f"所选“未翻译 XLSX”共识别 {recognized_xlsx_rows} 行，"
            "但 text 列与 Studio 导出时完全相同，未检测到谷歌译文，已阻止无效导入。"
            "请确认谷歌翻译后保存的是当前选中的文件，且译文仍在第 4 列 text。"
        )

    record_map = {str(rec.get("id")): rec for rec in records}
    changed = updated = missing_segments = rejected_segments = repaired_tokens = stale_records = 0
    rejected_segment_ids: set[str] = set()
    paks = set()
    rejected_examples = []
    for index, item in enumerate(mapping.get("records", []), 1):
        record_id, pak, _source_file, _line, _column, source, skeleton = item
        rec = record_map.get(str(record_id))
        if rec is None or rec.get("pak") != pak or rec.get("_isPlayerVisible", True) is not True:
            stale_records += 1
            continue
        # Never allow a pre-fix mapping to write a structured data payload
        # back into a PAK.  It is neither source text nor player-visible text.
        if _is_structural_xlsx_text(str(source or "")):
            stale_records += 1
            continue
        canonical_source = str(rec.get("source_original") or source)
        if canonical_source != source:
            stale_records += 1
            continue
        output = []
        current = str(rec.get("original") or source)
        current_skeleton = _out_of_band_skeleton(current)
        workbook_unchanged = all(
            translated.get(piece[1], (valid_rows.get(piece[1]) or {"exported": piece[2]})["exported"])
            == (valid_rows.get(piece[1]) or {"exported": piece[2]})["exported"]
            for piece in skeleton if piece[0] == "t"
        )
        current_structure_ok, _ = validate_import_structure(source, current)
        current_tokens_ok, _, _ = validate_build_tokens(source, internal_text(current, source))
        if (workbook_unchanged and not TRANSPORT_MARKER_RE.search(current)
                and current_structure_ok and current_tokens_ok):
            rec["language"] = score_text(current)[1]
            rec["status"] = "已翻译" if current != source else "未翻译"
            updated += 1
            paks.add(pak)
            continue
        source_literals = [piece[1] for piece in skeleton if piece[0] == "p"]
        current_literals = [piece[1] for piece in current_skeleton if piece[0] == "p"]
        current_texts = [piece[2] for piece in current_skeleton if piece[0] == "t"]
        source_text_count = sum(1 for piece in skeleton if piece[0] == "t")
        preserve_current = (
            not TRANSPORT_MARKER_RE.search(current)
            and source_literals == current_literals
            and len(current_texts) == source_text_count
        )
        current_text_index = 0
        record_missing = False
        for piece in skeleton:
            if piece[0] in ("k", "p"):
                output.append(piece[1])
                repaired_tokens += 1
                continue
            segment_id, segment_source = piece[1], piece[2]
            value_present = segment_id in translated
            value = translated.get(segment_id, "")
            row_meta = valid_rows.get(segment_id) or {"exported": segment_source}
            if not value_present:
                # A remaining-only workbook intentionally omits already-safe
                # rows. Preserve their current segment instead of reverting it.
                value = current_texts[current_text_index] if preserve_current else row_meta["exported"]
            elif preserve_current and value == row_meta["exported"]:
                value = current_texts[current_text_index]
            current_text_index += 1
            if not value:
                value = segment_source
                missing_segments += 1
                record_missing = True
            # Only reject transport markers here.  Digits, slashes, arrows,
            # abbreviations and similar characters can be legitimate Chinese
            # output (for example "50%", "学校/派系" or "BUFF").  The exact
            # source skeleton is restored below, and the completed record then
            # passes the authoritative tag/path/token validators.
            elif TRANSPORT_MARKER_RE.search(value):
                rejected_segments += 1
                rejected_segment_ids.add(segment_id)
                if len(rejected_examples) < 50:
                    rejected_examples.append({"id": segment_id, "reason": "译文含运输占位符/菱形，已用原文片段补全"})
                value = segment_source
                record_missing = True
            elif GENERATED_ASCII_DIGIT_RE.search(value):
                # Every ASCII digit in the source is already outside the XLSX
                # text cell.  A digit returned inside a translated cell was
                # invented/reinserted by the translator and would duplicate or
                # move a runtime value after the exact skeleton is restored.
                rejected_segments += 1
                rejected_segment_ids.add(segment_id)
                if len(rejected_examples) < 50:
                    rejected_examples.append({"id": segment_id, "reason": "译文凭空加入数字，已用原文片段补全"})
                value = segment_source
                record_missing = True
            elif not _is_safe_pure_chinese_import(value):
                # Strict user-selected import profile: text containing legacy
                # Vietnamese/Latin glyphs or runtime punctuation must never
                # enter modified resources. Preserve the exported source
                # instead and report it as unresolved.
                rejected_segments += 1
                rejected_segment_ids.add(segment_id)
                if len(rejected_examples) < 50:
                    rejected_examples.append({"id": segment_id, "reason": "译文不是纯中文安全文本，已用原文片段补全"})
                value = segment_source
                record_missing = True
            output.append(value)
        target = "".join(output)
        ok, reason = validate_import_structure(source, target)
        token_ok, source_tokens, target_tokens = validate_build_tokens(source, internal_text(target, source))
        if TRANSPORT_MARKER_RE.search(target) or not ok or not token_ok:
            # Exact source fallback is always structurally safe. Natural-language
            # completeness is reported but never made a build prerequisite.
            target = source
            rejected_segments += 1
            rejected_segment_ids.update(
                piece[1] for piece in skeleton if piece[0] == "t"
            )
            if len(rejected_examples) < 50:
                rejected_examples.append({"record_id": record_id, "reason": reason or "最终控制标记不一致", "source_tokens": source_tokens, "target_tokens": target_tokens})
            record_missing = True
        if rec.get("source_original") is None:
            rec["source_original"] = source
        if rec.get("original") != target:
            rec["original"] = target
            rec.pop("review_status", None)
            rec.pop("review_signature", None)
            rec.pop("reviewed_at", None)
            changed += 1
        rec["language"] = score_text(target)[1]
        rec["status"] = "已翻译" if target != source else "未翻译"
        updated += 1
        paks.add(pak)
        if progress and index % 2000 == 0:
            progress({"phase": "xlsx-import", "percent": round(index * 100 / max(1, len(mapping.get("records", []))), 1), "message": f"多 PAK 安全重建：{index:,} 条"})
    scope_paks = set(mapping.get("paks") or [])
    remaining_vi = [rec for rec in records
                    if rec.get("_isPlayerVisible", True) is True
                    and (not scope_paks or rec.get("pak") in scope_paks)
                    and any(ch in VI_CHARS for ch in str(rec.get("original") or ""))]
    remaining_by_pak: dict[str, int] = {}
    remaining_segment_ids: set[str] = set()
    for rec in remaining_vi:
        pak = str(rec.get("pak") or "")
        remaining_by_pak[pak] = remaining_by_pak.get(pak, 0) + 1
        for piece in _out_of_band_skeleton(str(rec.get("original") or "")):
            if piece[0] == "t" and any(ch in VI_CHARS for ch in piece[2]):
                remaining_segment_ids.add(piece[1])
    backup = records_path.with_name(records_path.name + ".before_multi_xlsx_import")
    shutil.copy2(records_path, backup)
    records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    result = {
        "records_path": str(records_path), "xlsx": str(xlsx_path), "paks": sorted(paks),
        "xlsx_rows": len(workbook_rows),
        "recognized_xlsx_rows": recognized_xlsx_rows,
        "translated_xlsx_rows": translated_xlsx_rows,
        "unchanged_xlsx_rows": unchanged_xlsx_rows,
        "blank_xlsx_rows": blank_xlsx_rows,
        "updated": updated, "changed": changed, "missing_segments": missing_segments,
        "rejected_segments": rejected_segments, "rejected": rejected_segments,
        "rejected_unique_segments": len(rejected_segment_ids),
        "repaired_structural_pieces": repaired_tokens, "stale_records": stale_records,
        "unknown_xlsx_ids": len(unknown), "unknown_xlsx_id_examples": unknown[:50],
        "remaining_vietnamese_records": len(remaining_vi),
        "remaining_vietnamese_unique_segments": len(remaining_segment_ids),
        "remaining_vietnamese_by_pak": remaining_by_pak,
        "remaining_vietnamese_examples": [
            {"pak": rec.get("pak"), "source_file": rec.get("source_file"),
             "line": rec.get("line"), "text": rec.get("original")}
            for rec in remaining_vi[:50]
        ],
        "untranslated_allowed": True, "backup": str(backup), "rejections": rejected_examples,
    }
    import_report_path = Path(mapping_path).with_name(f"{Path(xlsx_path).stem}_import_report.json")
    result["import_report"] = str(import_report_path.resolve())
    import_report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def export_xlsx_file_queue(src_dir: Path, out_dir: Path, pak_name: str, workers: int | None = None, records_path: Path | None = None, progress=None, metadata_dir: Path | None = None) -> dict:
    """Export one independent workbook and mapping per source resource file."""
    src_dir = Path(src_dir)
    out_dir = Path(out_dir)
    metadata_dir = Path(metadata_dir) if metadata_dir is not None else out_dir
    files = sorted(p for p in src_dir.rglob('*') if is_localizable_text_path(p))
    reports = []
    skipped = []
    records_data = json.loads(Path(records_path).read_text(encoding='utf-8')) if records_path and Path(records_path).is_file() else None
    for index, path in enumerate(files, 1):
        relative = path.relative_to(src_dir)
        safe_stem = relative.as_posix().replace('/', '__').replace('\\', '__')
        file_dir = out_dir
        # Keep the output folder flat without letting equal basenames from
        # different source directories overwrite each other.
        base_name = safe_stem
        if progress:
            progress({'phase': 'xlsx-export-files', 'percent': round(index * 100 / max(1, len(files)), 1), 'message': f'逐文件导出 {index}/{len(files)}：{relative.as_posix()}', 'current_file': relative.as_posix(), 'current_file_index': index, 'total_files': len(files)})
        try:
            report = export_xlsx_mapping(src_dir, file_dir, base_name, pak_name, workers=1, source_files=[path], records_data=records_data, metadata_dir=metadata_dir)
        except ValueError as exc:
            if '没有检测到可导出' in str(exc):
                skipped.append(relative.as_posix())
                continue
            raise
        report['source_file'] = relative.as_posix()
        reports.append(report)
    summary = {
        'mode': 'one-xlsx-per-source-file',
        'pak': pak_name,
        'output_dir': str(out_dir.resolve()),
        'config_dir': str(metadata_dir.resolve()),
        'scanned_files': len(files),
        'exported_files': len(reports),
        'skipped_files': len(skipped),
        'source_rows': sum(item['source_rows'] for item in reports),
        'excluded_not_player_visible': sum(item.get('excluded_not_player_visible', 0) for item in reports),
        'unique_rows': sum(item['unique_rows'] for item in reports),
        'prefilled_translations': sum(item['prefilled_translations'] for item in reports),
        'files': reports,
        'skipped': skipped,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / '_file_queue_export_report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


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
        if target == source_text:
            untranslated_unique += 1
            untranslated_cells += len(row_cells)
            if len(untranslated_examples) < 30:
                untranslated_examples.append({"id": row_id, "source": source_text, "current": target, "cells": len(row_cells)})
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


def apply_xlsx_to_records(
    records_path: Path,
    xlsx_path: Path,
    mapping_path: Path,
    pak_name: str,
    progress=None,
    records_data: list[dict] | None = None,
    save: bool = True,
    records_by_location_data: dict | None = None,
    records_by_id_data: dict | None = None,
) -> dict:
    records_path = Path(records_path)
    records = records_data if records_data is not None else json.loads(records_path.read_text(encoding="utf-8"))
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8-sig"))
    mapping_rows = list(_mapping_rows(mapping))
    translated = {}
    valid_ids = {row_id for row_id, _source, _cells in mapping_rows}
    unknown = []
    duplicate_ids = set()
    text_value_index = 2 if mapping.get("includes_source_file_column") is True else 1
    for row in read_simple_xlsx(xlsx_path):
        values = row.get("_values") or []
        row_id = str(row.get("id", "") or (values[0] if len(values) > 0 else "")).strip()
        text = row.get("text")
        if text is None:
            text = row.get("text_zh", "")
        if text is None or text == "":
            text = values[text_value_index] if len(values) > text_value_index else ""
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
    rejected = []
    source_dir = Path(mapping.get("extracted_dir") or "")
    line_cache = {}
    records_by_location = records_by_location_data or {
        (str(rec.get("source_file") or ""), int(rec.get("line") or 0), int(rec.get("column") or 0)): rec
        for rec in records if rec.get("pak") == pak_name
    }
    for mapping_index, (row_id, source_text, row_cells) in enumerate(mapping_rows, 1):
        if progress and mapping_index % 1000 == 0:
            progress({"phase": "xlsx-import", "percent": min(92, 85 + mapping_index / max(1, len(mapping_rows)) * 7), "message": f"正在准备界面同步：{mapping_index:,} / {len(mapping_rows):,}"})
        target = translated.get(row_id, "")
        # An untouched export contains the protected source template in the
        # editable column.  Skip it before any polishing/normalization can
        # accidentally turn source text into a seemingly translated update.
        if not target or target == source_text:
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
            if rec.get("_isPlayerVisible", True) is not True:
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
                rejected.append({'mapping_id': row_id, 'record_id': rec.get('id'), 'reason': error or '占位符还原失败'})
                continue
            current_source = str(meta.get("source", rec.get("source_original", "")))
            current_template = token_template(current_source)["text"]
            if current_template != source_text:
                rejected.append({'mapping_id': row_id, 'record_id': rec.get('id'), 'reason': '当前源文本与导出映射不一致'})
                continue
            if RESOURCE_REF_RE.search(meta.get("source", rec.get("source_original", ""))):
                continue
            ok, _reason = validate_import_structure(meta.get("source", rec.get("source_original", "")), restored)
            if not ok:
                rejected.append({'mapping_id': row_id, 'record_id': rec.get('id'), 'reason': _reason or '资源结构不一致'})
                continue
            source_value = str(meta.get("source", rec.get("source_original", "")))
            build_target = internal_text(restored, source_value)
            token_ok, source_tokens, target_tokens = validate_build_tokens(source_value, build_target)
            if not token_ok:
                rejected.append({'mapping_id': row_id, 'record_id': rec.get('id'), 'reason': '最终构建控制标记不一致', 'source_tokens': source_tokens, 'target_tokens': target_tokens})
                continue
            updates[rec.get("id")] = restored
    updated = changed = 0
    status_counts = {"已翻译": 0, "未翻译": 0}
    live_updates = []
    # Folder import calls this once per workbook.  Only visit records targeted
    # by the current workbook in that mode; rescanning the complete 100k-record
    # package for every source file makes an otherwise sequential import look
    # hung.  A standalone import retains the full status refresh behaviour.
    batch_mode = records_data is not None and not save
    if batch_mode:
        records_by_id = records_by_id_data or {
            rec.get("id"): rec for rec in records if rec.get("pak") == pak_name
        }
        records_to_update = [records_by_id[record_id] for record_id in updates if record_id in records_by_id]
    else:
        records_to_update = [rec for rec in records if rec.get("pak") == pak_name]
    for record_index, rec in enumerate(records_to_update, 1):
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
            # Preserve the analyzer's canonical visibility decision.  Importing
            # a workbook must never promote an internal field into build scope.
            rec.setdefault("_isPlayerVisible", True)
            updated += 1
        current = rec.get("original", "")
        rec["language"] = score_text(current)[1]
        rec["status"] = "已翻译" if current != rec.get("source_original", "") else "未翻译"
        status_counts[rec["status"]] += 1
        if target is not None:
            live_updates.append({"id": rec.get("id"), "text": current, "language": rec["language"], "status": rec["status"]})
        if progress and (len(live_updates) >= 200 or record_index == len(records_to_update)):
            progress({
                "phase": "xlsx-import",
                "percent": min(99.8, 92 + record_index / max(1, len(records_to_update)) * 7.8),
                "message": f"正在实时更新界面：{record_index:,} / {len(records_to_update):,}",
                "updates": live_updates,
                "status_counts": status_counts,
            })
            live_updates = []
    if save:
        records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if progress:
        progress({"phase": "xlsx-import", "percent": 100, "message": "界面同步完成", "status_counts": status_counts})
    return {"records_path": str(records_path), "xlsx": str(xlsx_path), "pak": pak_name, "updated": updated, "changed": changed, "rejected": len(rejected), "rejections": rejected[:50], "status_counts": status_counts}


def apply_xlsx_folder_to_records(records_path: Path, folder: Path, pak_name: str, progress=None, metadata_dir: Path | None = None) -> dict:
    """Import every per-file workbook with one records load/save transaction."""
    records_path = Path(records_path)
    folder = Path(folder)
    if metadata_dir is not None:
        metadata_dir = Path(metadata_dir)
    else:
        adjacent_config = folder.parent / 'config'
        metadata_dir = adjacent_config if adjacent_config.is_dir() else folder
    records = json.loads(records_path.read_text(encoding='utf-8'))
    pak_records = [rec for rec in records if rec.get('pak') == pak_name]
    records_by_location = {
        (str(rec.get('source_file') or ''), int(rec.get('line') or 0), int(rec.get('column') or 0)): rec
        for rec in pak_records
    }
    records_by_id = {rec.get('id'): rec for rec in pak_records}
    workbooks = sorted(folder.glob('*_localization.xlsx'))
    if not workbooks:
        raise ValueError(f'文件夹中没有找到 *_localization.xlsx：{folder}')
    reports = []
    failed = []
    for index, workbook in enumerate(workbooks, 1):
        mapping = metadata_dir / f'{workbook.stem}_mapping.json'
        if not mapping.exists():
            # Backward compatibility with the earlier mixed directory layout.
            mapping = workbook.with_name(f'{workbook.stem}_mapping.json')
        if not mapping.exists():
            failed.append({'file': workbook.name, 'reason': '缺少专属 mapping.json'})
            continue
        if progress:
            progress({'phase': 'xlsx-folder-import', 'percent': round((index - 1) * 100 / len(workbooks), 1), 'message': f'导入文件 {index}/{len(workbooks)}：{workbook.name}', 'current_file': workbook.name, 'current_file_index': index, 'total_files': len(workbooks)})
        try:
            reports.append(apply_xlsx_to_records(
                records_path,
                workbook,
                mapping,
                pak_name,
                records_data=records,
                save=False,
                records_by_location_data=records_by_location,
                records_by_id_data=records_by_id,
            ))
        except Exception as exc:
            failed.append({'file': workbook.name, 'reason': str(exc)})
    if not reports and failed:
        first = failed[0]
        raise ValueError(
            f'没有任何 XLSX 可以导入。配置目录：{metadata_dir}；'
            f'首个失败：{first["file"]}：{first["reason"]}'
        )
    backup = records_path.with_name(records_path.name + '.before_folder_import')
    if backup.exists():
        suffix = 1
        while records_path.with_name(records_path.name + f'.before_folder_import.{suffix}').exists():
            suffix += 1
        backup = records_path.with_name(records_path.name + f'.before_folder_import.{suffix}')
    shutil.copy2(records_path, backup)
    records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    rejections = [entry for item in reports for entry in item.get('rejections', [])]
    return {'folder': str(folder.resolve()), 'config_dir': str(metadata_dir.resolve()), 'total_files': len(workbooks), 'imported_files': len(reports), 'failed_files': len(failed), 'failures': failed[:50], 'updated': sum(item['updated'] for item in reports), 'changed': sum(item['changed'] for item in reports), 'rejected': sum(item.get('rejected', 0) for item in reports), 'rejections': rejections[:100], 'backup': str(backup), 'records_path': str(records_path)}


def restore_records_from_materialize_report(records_path: Path, report_path: Path) -> dict:
    """Revert only translations the final resource writer rejected."""
    records_path = Path(records_path)
    report_path = Path(report_path)
    records = json.loads(records_path.read_text(encoding='utf-8'))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    rejected_ids = {str(value) for value in report.get('skipped_ids', []) if str(value)}
    if not rejected_ids:
        return {'restored': 0, 'missing': 0, 'backup': None, 'records_path': str(records_path)}
    backup = records_path.with_name(records_path.name + '.before_sync_repair')
    if backup.exists():
        suffix = 1
        while records_path.with_name(records_path.name + f'.before_sync_repair.{suffix}').exists():
            suffix += 1
        backup = records_path.with_name(records_path.name + f'.before_sync_repair.{suffix}')
    shutil.copy2(records_path, backup)
    restored = 0
    seen = set()
    for record in records:
        record_id = str(record.get('id') or '')
        if record_id not in rejected_ids:
            continue
        source = str(record.get('source_original', record.get('original', '')))
        record['original'] = source
        record['language'] = score_text(source)[1]
        record['status'] = '未翻译'
        record['_isPlayerVisible'] = True
        record.pop('review_status', None)
        record.pop('review_signature', None)
        record.pop('reviewed_at', None)
        seen.add(record_id)
        restored += 1
    records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return {'restored': restored, 'missing': len(rejected_ids - seen), 'backup': str(backup), 'records_path': str(records_path)}
