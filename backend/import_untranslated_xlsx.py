#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_untranslated_xlsx.py
===========================
把翻译好的 XLSX（经 Studio 「导出XLSX」或 export_untranslated_xlsx.py 生成）
填回 text_records.json，并通过 records_mapping.json 把每个 xlsx id 同步到所有
对应的 text_record_ids。

同时：
- 做占位符/格式符一致性校验（◈N◈、特殊符号个数、前缀$#=）
- 输出报告：更新数 / 拒翻译数 / 空行数
- 直接更新 workspace/localization/text_records.json，做备份

用法：
  # 把翻译好的 settings_localization.xlsx 放到 workspace/untranslated_xlsx/settings/
  # （覆盖翻译前的同路径空文XLSX）
  python backend/import_untranslated_xlsx.py --pak settings.pak

  # 或者指定自定义 xlsx 路径：
  python backend/import_untranslated_xlsx.py --pak updatefs.pak --xlsx path/to/translated.xlsx
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from localization_tm import validate_tokens
from tsv_localization import validate_translation
from xlsx_localization import restore_xlsx_translation, validate_import_structure
from export_untranslated_xlsx import looks_like_executable_code

XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

def accept_translation(source, target, meta=None):
    """Single restoration and acceptance contract for translation and import."""
    restored = str(target or '')
    if meta is not None:
        restored, error = restore_xlsx_translation(restored, meta, source)
        if error or restored is None:
            return None, error or '占位符还原失败'
    ok, reason = validate_translation(source, restored)
    if not ok:
        return None, reason
    ok, before, after = validate_tokens(source, restored)
    if not ok:
        return None, f'保护标识符不一致: {before!r} -> {after!r}'
    ok, reason = validate_import_structure(source, restored)
    if not ok:
        return None, reason
    return restored, ''

# 占位符样式：◈1◈ ◈2◈ 等
DIAMOND_RE = re.compile(r"◈\d+◈")
HALF_DIAMOND_RE = re.compile(r"[◈]?\d+[◈]?")  # 兼容半角
DIAMOND_LIKE = re.compile(r"\{P\d+\}|《P\d+》|\(P\d+\)|◈\d*◈")


def read_simple_xlsx(xlsx_path: Path) -> list[dict]:
    """读 Studio 标准 XLSX：id/text 两列，sheet 名「本土化」或默认 sheet1."""
    with zipfile.ZipFile(xlsx_path) as zf:
        # shared strings
        try:
            sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"x": XML_NS}
            shared = []
            for si in sroot.findall("x:si", ns):
                texts = [n.text or "" for n in si.iter() if n.tag.endswith("}t")]
                shared.append("".join(texts))
        except KeyError:
            shared = None

        # workbook sheets -> locate 本土化 / sheet1
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        relns = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
        target = None
        for s in wb.findall(".//{*}sheet"):
            name = s.get("name", "")
            if name == "本土化":
                target = s.get(f"{{{relns['r']}}}id")
                break
        if target is None:
            first_sheet = wb.find(".//{*}sheets/{*}sheet[1]")
            if first_sheet is not None:
                target = first_sheet.get(f"{{{relns['r']}}}id")
        if not target:
            raise ValueError("XLSX 找不到工作表")

        rel = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        sheet_xml_path = None
        for r in rel.findall("{*}Relationship"):
            if r.get("Id") == target:
                t = r.get("Target")
                if not t.startswith("/"):
                    t = "xl/" + t
                sheet_xml_path = t.lstrip("/")
                break
        if not sheet_xml_path:
            raise ValueError("找不到 sheet xml")
        sheet_xml = zf.read(sheet_xml_path)

    root = ET.fromstring(sheet_xml)
    rows = []
    ns = {"x": XML_NS}
    for rn, row in enumerate(root.findall(".//x:sheetData/x:row", ns), 1):
        values = {}
        for c in row.findall("x:c", ns):
            ref = c.get("r", "")
            m = re.match(r"([A-Z]+)(\d+)", ref)
            col_letter = m.group(1) if m else ""
            t = c.get("t")
            v_el = c.find("x:v", ns)
            v = ""
            if t == "inlineStr":
                is_el = c.find("x:is", ns)
                if is_el is not None:
                    pieces = [n.text or "" for n in is_el.iter() if n.tag.endswith("}t")]
                    v = "".join(pieces)
            elif t == "s" and shared is not None and v_el is not None:
                try:
                    v = shared[int(v_el.text)]
                except Exception:
                    v = ""
            elif v_el is not None:
                v = v_el.text or ""
            values[col_letter] = v
        rows.append({"_row": rn, "_values": values})

    # 识别 header：id/text 列
    if not rows:
        return []
    header = rows[0]["_values"]
    # 按列 A=id B=text
    if header.get("A", "").strip() == "id" and header.get("B", "").strip() == "text":
        result = []
        for r in rows[1:]:
            vs = r["_values"]
            rid = vs.get("A", "").strip()
            txt = vs.get("B", "")
            if not rid:
                continue
            result.append({"id": rid, "text": txt})
        return result

    # 否则尝试按名查找
    col_map = {}
    for letter, name in header.items():
        col_map[name.strip()] = letter
    if "id" in col_map and "text" in col_map:
        result = []
        for r in rows[1:]:
            vs = r["_values"]
            rid = vs.get(col_map["id"], "").strip()
            txt = vs.get(col_map["text"], "")
            if not rid:
                continue
            result.append({"id": rid, "text": txt})
        return result

    raise ValueError(f"XLSX header 不识别：{header}")


def validate(src: str, tgt: str) -> tuple[bool, str]:
    """校验翻译是否保持了占位符、前缀、符号一致性。返回 (是否接受, 原因)。"""
    if not tgt or not tgt.strip():
        return False, "译文为空"

    # 前缀 $ # = 必须一致
    pf_src = re.match(r"^[\$#=]+", src)
    pf_tgt = re.match(r"^[\$#=]+", tgt)
    ps = pf_src.group(0) if pf_src else ""
    pt = pf_tgt.group(0) if pf_tgt else ""
    if ps != pt:
        return False, f"前缀不一致 src[{ps}] != tgt[{pt}]"

    # ◈N◈ 占位符数量和顺序
    src_d = DIAMOND_RE.findall(src) or DIAMOND_LIKE.findall(src)
    tgt_d = DIAMOND_RE.findall(tgt) or DIAMOND_LIKE.findall(tgt)
    if src_d and src_d != tgt_d:
        # 只数数量，顺序不强制相同（避免 false negative）
        if Counter(src_d) != Counter(tgt_d):
            return False, f"占位符不匹配: {src_d} != {tgt_d}"

    # 颜色标签数量 <c=...> </c>
    color_tags = lambda t: (len(re.findall(r"<c=[^>]+>", t)), len(re.findall(r"</c>", t)))
    so = color_tags(src)
    to = color_tags(tgt)
    if so != to:
        return False, f"颜色标签不一致: src={so} tgt={to}"

    return True, "ok"


def run(workspace, pak, xlsx=None):
    ws = Path(workspace)
    base = re.sub(r"\.pak$", "", pak, flags=re.I)
    default_xlsx = ws / "untranslated_xlsx" / base / f"{base}_localization.xlsx"
    xlsx_path = Path(xlsx) if xlsx else default_xlsx
    export_report_path = ws / "untranslated_xlsx" / base / "export_report.json"
    if export_report_path.exists():
        export_report = json.loads(export_report_path.read_text(encoding="utf-8"))
        if export_report.get("status") == "empty" or int(export_report.get("unique_rows_xlsx") or 0) == 0:
            raise ValueError(export_report.get("message") or "没有可导回的自然语言译文")
    if not xlsx_path.exists():
        raise SystemExit(f"XLSX 不存在：{xlsx_path}")

    rm_path = ws / "untranslated_xlsx" / base / f"{base}_records_mapping.json"
    if not rm_path.exists():
        raise SystemExit(f"records_mapping.json 不存在，请先运行 export_untranslated_xlsx.py：{rm_path}")

    records_path = ws / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    rec_by_id = {str(r.get("id", "")): r for r in records}

    translated_rows = read_simple_xlsx(xlsx_path)
    duplicate_ids = sorted(
        rid for rid, count in Counter(str(row.get("id", "")).strip() for row in translated_rows if str(row.get("id", "")).strip()).items()
        if count > 1
    )
    if duplicate_ids:
        raise ValueError(f"XLSX 存在重复 ID，已阻止可能的错位导入：{duplicate_ids[:10]}")
    records_mapping = json.loads(rm_path.read_text(encoding="utf-8"))
    selected_ids = {str(row.get("id", "")).strip() for row in translated_rows if str(row.get("id", "")).strip()}
    mapping_ids = {str(x).strip() for x in records_mapping}
    missing_mapping_ids = sorted(mapping_ids - selected_ids)
    extra_xlsx_ids = sorted(selected_ids - mapping_ids)
    if missing_mapping_ids or extra_xlsx_ids:
        print(
            f"提示：所选 XLSX 与当前 {pak} 映射不是完整一一对应；"
            f"缺少 {len(missing_mapping_ids):,} 个映射 ID，多出 {len(extra_xlsx_ids):,} 个 XLSX ID。"
            "本次仅导入能匹配且通过校验的行。"
        )

    bak = records_path.with_name(records_path.name + f".before_import_{base}_{len(list(records_path.parent.glob('before_import_*')))}")
    shutil.copy2(records_path, bak)
    print(f"备份 text_records.json -> {bak.name}")
    print(f"读入 XLSX 行: {len(translated_rows):,}")
    print(f"records_mapping xlsx_id 数: {len(records_mapping):,}")

    updated = 0
    rejected = 0
    empty = 0
    unmapped = 0
    unchanged = 0
    reasons = Counter()
    note_tag = f"[导入:{base}_xlsx]"

    for row in translated_rows:
        xid = str(row.get("id", "")).strip()
        zh_text = row.get("text", "")
        rec_entries = records_mapping.get(xid)
        if not rec_entries:
            unmapped += 1
            continue
        if not zh_text or not str(zh_text).strip():
            empty += len(rec_entries)
            continue

        # v2 映射为每条记录保存各自占位符。兼容 TRAE 旧版的 id 字符串列表。
        for entry in rec_entries:
            if isinstance(entry, dict):
                rid = str(entry.get("id", ""))
                meta = entry
            else:
                rid = str(entry)
                meta = None
            r = rec_by_id.get(rid)
            if not r:
                continue
            src_text = str((meta or {}).get("source") or r.get("source_original", r.get("original", "")))
            export_text = str((meta or {}).get("export_text") or src_text)
            if looks_like_executable_code(src_text):
                rejected += 1
                reasons["脚本表达式禁止翻译"] += 1
                continue
            if str(zh_text).strip() in {src_text.strip(), export_text.strip()}:
                unchanged += 1
                continue
            restored, reason = accept_translation(src_text, zh_text, meta)
            if restored is None:
                rejected += 1
                reasons[reason] += 1
                continue
            r["source_original"] = src_text
            r["original"] = restored
            r["translation"] = restored
            r["language"] = "zh"
            r["status"] = "已迁移"
            existing_note = str(r.get("note", "")).strip()
            if note_tag not in existing_note:
                r["note"] = (existing_note + " " + note_tag).strip()
            updated += 1

    # 保存
    records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    total = len(records)
    zh_total = sum(1 for r in records if r.get("language") == "zh")
    migrated_total = sum(1 for r in records if r.get("status") == "已迁移")
    covered_total = sum(1 for r in records if r.get("language") == "zh" or r.get("status") == "已迁移")
    coverage = covered_total * 100 / total

    report = {
        "pak": pak,
        "xlsx": str(xlsx_path.resolve()),
        "backup": str(bak),
        "updated": updated,
        "rejected": rejected,
        "rejected_reasons": dict(reasons),
        "empty_rows_skipped": empty,
        "unchanged_rows_skipped": unchanged,
        "unmapped_xlsx_ids": unmapped,
        "missing_mapping_ids": len(missing_mapping_ids),
        "missing_mapping_id_examples": missing_mapping_ids[:50],
        "extra_xlsx_ids": len(extra_xlsx_ids),
        "extra_xlsx_id_examples": extra_xlsx_ids[:50],
        "coverage_after": {
            "chinese_records": zh_total,
            "migrated_records": migrated_total,
            "total": total,
            "covered_records": covered_total,
            "percent": round(coverage, 2),
        },
    }
    out_rep = ws / "untranslated_xlsx" / base / "import_report.json"
    out_rep.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("===== 导入完成 =====")
    print(f"更新:   {updated:,} 条")
    print(f"拒绝:   {rejected:,} 条")
    if rejected:
        for k, v in reasons.most_common():
            print(f"  - {k}: {v:,}")
    print(f"空行跳过: {empty:,}")
    print(f"未改变译文跳过: {unchanged:,}")
    print(f"总覆盖率: {coverage:.2f}% ({covered_total:,}/{total:,})")
    print(f"报告: {out_rep}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++")
    ap.add_argument("--pak", required=True, help="settings.pak / ui.pak / updatefs.pak / script.pak")
    ap.add_argument("--xlsx", default=None, help="可选：自定义译后 XLSX 路径")
    args = ap.parse_args()
    run(args.workspace, args.pak, args.xlsx)


if __name__ == "__main__":
    main()
