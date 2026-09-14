#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_untranslated_xlsx.py
===========================
把 paks++ 项目中**仅未翻译**的越南文内容，按 Studio 现有「导出 XLSX」
完全兼容的格式导出，同时按 PAK 分包。

特点：
1. 以 text_records.json 为准，过滤 status!='已迁移' && language!='zh'
   && 实际包含越南文字符的条目
2. 以 text_records.json 中的「source_original」字段去重（33570唯一→68153记录）
3. 生成标准 Studio XLSX 导入管道兼容的结构：
   - *_localization.xlsx          (id | text  两列，sheet=本土化)
   - *_localization_mapping.json  (rows: [id, text, cells=[[file_idx,row,col]]])
   - *_localization_mapping.csv   (mapping_id, source_file, row, column)
4. 同时生成 records_mapping.json（xlsx_id -> text_record_id 列表），
   供 import_untranslated_xlsx.py 直接回填 text_records.json。
5. 支持按 PAK 分包（settings / ui / updatefs），可分别翻译和导回。

用法：
  python backend/export_untranslated_xlsx.py
  python backend/export_untranslated_xlsx.py --pak updatefs.pak
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Iterable
from tsv_localization import token_template
from localization_analyzer import active_vi_words

VI_CHARS_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩíịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳỷỹýỵÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊÒỎÕÓỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]")
CODE_IDENTIFIER_RE = re.compile(r"^\[?[A-Za-z_][A-Za-z0-9_.:\-]*\]?$")
FIELD_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:\-]*\s+(?:ID|Name|Key|Path|File|Type)$", re.I)
ONLY_PLACEHOLDERS_RE = re.compile(r"^(?:\s|◈\s*(?:P\s*)?\d+\s*◈|\{P\d+\}|[#$=,:;|/\\\-])+$", re.I)
CODE_CALL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:]*\s*\(.*\)\s*;?$", re.S)
CODE_ASSIGN_RE = re.compile(r"^(?:local\s+)?[A-Za-z_][A-Za-z0-9_.]*\s*=", re.I)


def looks_like_executable_code(text: str) -> bool:
    """Detect complete script expressions, not merely isolated identifiers."""
    value = str(text or "").strip()
    if not value:
        return False
    if CODE_CALL_RE.fullmatch(value):
        return True
    assignment = CODE_ASSIGN_RE.match(value)
    if assignment:
        rhs = value[assignment.end():].strip()
        if CODE_CALL_RE.fullmatch(rhs) or re.fullmatch(r"[-+]?\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_.:/\\-]*", rhs):
            return True
    return bool(
        re.search(r"\b(?:function|return|if|elseif|while|for)\b", value, re.I)
        and re.search(r"[(){}=;]", value)
    )


def looks_like_code_identifier(text: str) -> bool:
    value = str(text or "").strip()
    if not value or ONLY_PLACEHOLDERS_RE.fullmatch(value):
        return True
    if FIELD_LABEL_RE.fullmatch(value):
        return True
    if CODE_IDENTIFIER_RE.fullmatch(value):
        core = value.strip("[]")
        uppercase = sum(1 for ch in core if ch.isupper())
        return (
            value.startswith("[")
            or "_" in core
            or "." in core
            or ":" in core
            or any(ch.isdigit() for ch in core)
            or uppercase >= 2
            or core.isupper()
        )
    return False

SHEET_NAME = "本土化"
XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_INVALID_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml_escape(v: str) -> str:
    import html
    return html.escape(XML_INVALID_CHAR_RE.sub("", str(v or "")), quote=True)


def _column_name(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def write_xlsx(path: Path, rows: list[dict], headers=None) -> None:
    headers = headers or ["id", "text"]
    path.parent.mkdir(parents=True, exist_ok=True)
    def cell_xml(rn, cn, v):
        ref = f"{_column_name(cn)}{rn}"
        return f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape("" if v is None else v)}</t></is></c>'

    sheet_rows = []
    for rn, values in enumerate([dict(zip(headers, headers)), *rows], 1):
        cells = "".join(cell_xml(rn, cn, values.get(h, "")) for cn, h in enumerate(headers, 1))
        sheet_rows.append(f'<row r="{rn}">{cells}</row>')
    dim = f"A1:{_column_name(len(headers))}{len(rows) + 1}"
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{XML_NS}" xmlns:r="{REL_NS}">'
        f'<dimension ref="{dim}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{XML_NS}" xmlns:r="{REL_NS}"><sheets>'
        f'<sheet name="{_xml_escape(SHEET_NAME)}" sheetId="1" r:id="rId1"/>'
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
    ctypes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def has_natural_language_residual(text: str) -> bool:
    """Detect Vietnamese language left inside an otherwise translated value.

    English acronyms/technical terms such as NPC, VIP, SARS and pull/push are
    intentionally allowed.  Vietnamese diacritics or known Vietnamese game
    vocabulary are not.
    """
    from protected_segments import PATTERN
    value = str(text or "")
    if looks_like_executable_code(value):
        return False
    value = re.sub(r"https?://\S+", " ", value)
    value = PATTERN.sub(" ", value)
    if VI_CHARS_RE.search(value):
        return True
    scrubbed = re.sub(r"</?[^>\r\n]+>|(?:[A-Za-z][A-Za-z0-9+.-]{0,15})://\S+", " ", value)
    raw_words = re.findall(r"[A-Za-z]+", scrubbed)
    words = {w.lower() for w in raw_words}
    if words & {str(word).lower() for word in active_vi_words()}:
        return True
    if re.search(r"[\u3400-\u9fff]", value):
        # Latin fragments in localized game text are often legitimate: Lv, x,
        # Mobile, acronyms and player/NPC names. Rejecting every lowercase word
        # caused valid Chinese results to be discarded in bulk. A Latin stem
        # glued directly to CJK is still suspicious (for example "$Ho完成").
        latin_stem = r"[A-Za-z]*[a-z][A-Za-z]+|[A-Za-z]+[a-z][A-Za-z]*"
        return bool(re.search(rf"(?:(?:{latin_stem})[\u3400-\u9fff]|[\u3400-\u9fff](?:{latin_stem}))", value))
    return False


def should_include(r: dict) -> bool:
    """Return True when the text currently displayed by Studio still needs translation.

    Status/language are cached metadata and can be stale after a migration.  The
    current ``original`` value is the source of truth here: a record marked
    ``已迁移`` may still contain untouched Vietnamese text.
    """
    current = str(r.get("original", ""))
    source = str(r.get("source_original", "") or "")
    if not current.strip():
        return False
    transport_marker = re.compile(r"◈\s*(?:P\s*)?\d+\s*◈|《\s*(?:P\s*)?\d+\s*》", re.I)
    if source and transport_marker.search(current) and not transport_marker.search(source):
        return True
    if looks_like_code_identifier(current) or looks_like_executable_code(current):
        return False
    return has_natural_language_residual(current)


def export_pak(records: Iterable[dict], workspace: Path, pak: str) -> dict:
    base = re.sub(r"\.pak$", "", pak, flags=re.I)
    out_dir = workspace / "untranslated_xlsx" / base
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先把代码/数字/格式符映射成占位符，再按可翻译模板去重。
    # 这样只有参数不同的同一句话只需要翻译一次，导入时再逐记录还原。
    grouped: dict[str, dict] = {}
    file_indexes: dict[str, int] = {}
    mapping_csv_rows = []
    records_mapping: dict[str, list[dict]] = {}  # xlsx_id -> 逐记录还原元数据
    total_source_cells = 0
    pak_records = [r for r in records if r.get("pak") == pak]
    included = 0

    for r in pak_records:
        if not should_include(r):
            continue
        sf = str(r.get("source_file", ""))
        row = r.get("line", 0)
        # The structure-aware extractor already excludes schema headers.  Do
        # not blanket-drop line 1 here: headerless/synthetic TSV resources can
        # legitimately contain visible text on their first line.
        current = str(r.get("original", ""))
        source_original = str(r.get("source_original", "") or "")
        # A mixed/partial Google result is poor translation input: models tend
        # to preserve its Latin fragments.  Retry from the untouched source
        # language whenever it is available.
        has_leaked_marker = bool(
            source_original
            and re.search(r"◈\s*(?:P\s*)?\d+\s*◈|《\s*(?:P\s*)?\d+\s*》", current, re.I)
            and not re.search(r"◈\s*(?:P\s*)?\d+\s*◈|《\s*(?:P\s*)?\d+\s*》", source_original, re.I)
        )
        src = source_original if (
            (has_natural_language_residual(current) or has_leaked_marker)
            and source_original
            and source_original != current
        ) else current
        meta = token_template(src)
        export_text = str(meta.get("text") or src)
        fi = file_indexes.setdefault(sf, len(file_indexes))
        col = r.get("column", 0)
        rec_id = str(r.get("id", ""))

        total_source_cells += 1
        item = grouped.get(export_text)
        if item is None:
            item = {"id": str(len(grouped) + 1), "text": export_text, "cells": []}
            grouped[export_text] = item
        item["cells"].append([fi, row, col])
        mapping_csv_rows.append((item["id"], sf, row, col))
        records_mapping.setdefault(item["id"], []).append({
            "id": rec_id,
            "source": src,
            "tokens": meta.get("tokens") or [],
            "template": meta.get("template") or "{TEXT}",
            "export_text": export_text,
            "dollar_spaced": bool(meta.get("dollar_spaced")),
        })
        included += 1

    # Translate high-frequency strings first. A short checkpoint can then remove
    # thousands of visible Vietnamese occurrences instead of spending the same
    # time on one-off rows. `_occurrences` is only a sorting aid; XLSX still
    # exports the original id/text columns.
    unique_rows = [
        {"id": v["id"], "text": v["text"], "_occurrences": len(v["cells"])}
        for v in grouped.values()
    ]
    unique_rows.sort(key=lambda r: (-int(r["_occurrences"]), int(r["id"])))

    # 写出
    xlsx_path = out_dir / f"{base}_localization.xlsx"
    mapping_json = out_dir / f"{base}_localization_mapping.json"
    mapping_csv = out_dir / f"{base}_localization_mapping.csv"
    records_mapping_path = out_dir / f"{base}_records_mapping.json"

    write_xlsx(xlsx_path, unique_rows, ["id", "text"])

    mapping = {
        "version": 5,
        "mode": "xlsx-dedup-cells-compact",
        "pak": pak,
        "base_name": base,
        "source_rows": total_source_cells,
        "unique_rows": len(unique_rows),
        "files": list(file_indexes.keys()),
        "rows": [[item["id"], item["text"], item["cells"]] for item in grouped.values()],
        "derived_from": "text_records.json (untranslated-only, VI-dedup)",
    }
    mapping_json.write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    with mapping_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["mapping_id", "source_file", "row", "column"])
        w.writerows(mapping_csv_rows)

    records_mapping_path.write_text(json.dumps(records_mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    report = {
        "pak": pak,
        "xlsx": str(xlsx_path),
        "mapping_json": str(mapping_json),
        "mapping_csv": str(mapping_csv),
        "records_mapping": str(records_mapping_path),
        "output_dir": str(out_dir),
        "included_records": included,
        "total_source_cells": total_source_cells,
        "unique_rows_xlsx": len(unique_rows),
        "dedup_ratio": "%.2f%%" % (len(unique_rows) * 100 / max(1, total_source_cells)),
    }
    (out_dir / "export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run(workspace, pak=None):
    """导出指定 PAK（或全部含未翻译越南文的 PAK）的未翻译 XLSX。返回 summary dict。"""
    ws = Path(workspace)
    records_path = ws / "localization" / "text_records.json"
    if not records_path.exists():
        raise FileNotFoundError(f"未找到文本记录文件：{records_path}，请先导入 PAK / 打开项目。")
    records = json.loads(records_path.read_text(encoding="utf-8"))

    all_paks = sorted({r.get("pak", "") for r in records if r.get("pak")})
    selected = [pak] if pak else all_paks

    reports = []
    for p in selected:
        if not any(should_include(r) for r in records if r.get("pak") == p):
            base = re.sub(r"\.pak$", "", p, flags=re.I)
            out_dir = ws / "untranslated_xlsx" / base
            out_dir.mkdir(parents=True, exist_ok=True)
            empty_report = {
                "pak": p,
                "included_records": 0,
                "total_source_cells": 0,
                "unique_rows_xlsx": 0,
                "status": "empty",
                "message": "没有可翻译的自然语言文本；代码标识符已排除。",
            }
            (out_dir / "export_report.json").write_text(json.dumps(empty_report, ensure_ascii=False, indent=2), encoding="utf-8")
            reports.append(empty_report)
            continue
        rep = export_pak(records, ws, p)
        reports.append(rep)

    summary = {
        "workspace": str(ws.resolve()),
        "exports": reports,
        "total_records": sum(r["included_records"] for r in reports),
        "total_unique": sum(r["unique_rows_xlsx"] for r in reports),
    }
    (ws / "untranslated_xlsx").mkdir(parents=True, exist_ok=True)
    (ws / "untranslated_xlsx" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++")
    ap.add_argument("--pak", help="单个 PAK 名，如 settings.pak，不填则所有包含未翻译的 PAK")
    args = ap.parse_args()

    summary = run(args.workspace, args.pak)
    print()
    print("=" * 60)
    print(f"总待翻译记录：{summary['total_records']:,} 条")
    print(f"总去重后 XLSX 行数：{summary['total_unique']:,} 条")
    print(f"输出目录：{Path(args.workspace) / 'untranslated_xlsx'}")
    print(f"汇总：{Path(args.workspace) / 'untranslated_xlsx' / 'summary.json'}")


if __name__ == "__main__":
    main()
