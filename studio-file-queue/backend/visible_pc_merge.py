#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from localization_tm import exchange_text, validate_tokens
from pc_content_migrator import (
    acceptable_pc_text,
    extracted_file,
    stable_row_signatures,
    stable_rows,
    unique_ini_anchors,
)
from tsv_structure_overlap import row_signatures
from tsv_localization import validate_translation


TEXT_EXTS = {".tsv", ".ini", ".txt"}
VISIBLE_HEADER_RE = re.compile(
    r"(^|[_\s-])(name|title|intro|desc|description|text|content|message|msg|tip|caption|dialog|talk|say|quest|task|mission|skill|npc|item|weapon|equip|mapname|dropname|notice|label|help|story)([_\s-]|$)|名称|名字|标题|说明|描述|简介|内容|文本|对白|对话|提示|消息|任务|技能名|物品名|装备名|地图名|备注",
    re.I,
)
INTERNAL_HEADER_RE = re.compile(
    r"(image|img|icon|spr|sprite|file|path|script|sound|music|texture|font|res|objid|id$|genre|type|kind|width|height|price|count|weight|trade|time|rate|param|frame|radius|speed|damage|defense|resist|color|lum|attrib|level|drop$|pk|ai|barrier|stun|confuse|freeze|timer|clientonly)",
    re.I,
)
VISIBLE_KEY_RE = re.compile(
    r"(name|title|text|desc|description|message|msg|tip|dialog|talk|quest|task|mission|skill|item|weapon|equip|npc|notice|label|help|success|fail|error|warning|info)",
    re.I,
)
INTERNAL_KEY_RE = re.compile(
    r"(image|img|icon|spr|file|path|script|sound|music|texture|font|res|id$|rate|param|frame|speed|color|stone|stunid|level|time|value|num|count|price|width|height)",
    re.I,
)
VI_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]"
)
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def file_type(record: dict) -> str:
    return Path(str(record.get("source_file", ""))).suffix.lower()


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(str(text or "")))


def has_vi(text: str) -> bool:
    return bool(VI_RE.search(str(text or "")))


def header_name(record: dict) -> str:
    if record.get("_header_name"):
        return str(record.get("_header_name") or "")
    if file_type(record) != ".tsv":
        return str(record.get("key") or "")
    context = str(record.get("context") or "")
    first_line = context.splitlines()[0] if context else ""
    cells = first_line.split("\t") if first_line else []
    idx = int(record.get("column") or 1) - 1
    if 0 <= idx < len(cells):
        return cells[idx].strip()
    return str(record.get("key") or "").strip()


def common_visible_tsv_column(record: dict) -> bool:
    col = int(record.get("column") or 1)
    line = int(record.get("line") or 0)
    text = str(record.get("original") or record.get("source_original") or "")
    if line == 1:
        return False
    if not re.search(r"[A-Za-z\u00c0-\u1ef9\u3400-\u9fff]", text):
        return False
    return col in {1, 2, 5, 9, 11, 12, 15, 19, 26, 28, 30, 31, 32, 88}


def is_player_visible(record: dict) -> bool:
    ext = file_type(record)
    text = str(record.get("original") or record.get("source_original") or "").strip()
    if not text:
        return False
    if ext == ".tsv":
        if int(record.get("line") or 0) == 1:
            return False
        header = header_name(record)
        if INTERNAL_HEADER_RE.search(header) and not VISIBLE_HEADER_RE.search(header):
            return False
        if VISIBLE_HEADER_RE.search(header):
            return True
        return common_visible_tsv_column(record)
    if ext == ".ini":
        key = str(record.get("key") or "")
        if INTERNAL_KEY_RE.search(key) and not VISIBLE_KEY_RE.search(key):
            return False
        if VISIBLE_KEY_RE.search(key):
            return True
        return bool(re.search(r"<color=|<c=|[\u00c0-\u1ef9\u3400-\u9fff]", text, re.I)) and not bool(
            re.match(r"^[\\/.\w:-]+$", text, re.I)
        )
    if ext == ".txt":
        return bool(re.search(r"[\u00c0-\u1ef9\u3400-\u9fff]", text)) and not bool(
            re.match(r"^[\\/.\w:-]+$", text, re.I)
        )
    return False


def should_replace(record: dict) -> bool:
    if not is_player_visible(record):
        return False
    note = str(record.get("note") or "")
    status = str(record.get("status") or "")
    if "[人工TSV合并]" in note or status in {"人工确认", "已审核"}:
        return False
    current = str(record.get("original") or "")
    return not has_cjk(current) and has_vi(current)


def source_text(record: dict) -> str:
    return str(record.get("source_original") or record.get("original") or "")


def add_candidate(candidates: dict, record: dict, target: str, method: str) -> None:
    source = source_text(record)
    target = str(target or "")
    if not should_replace(record) or not acceptable_pc_text(target) or source == target:
        return
    ok, _ = validate_translation(exchange_text(source), exchange_text(target))
    tokens_ok, _source_tokens, _target_tokens = validate_tokens(source, target)
    if not ok or not tokens_ok:
        return
    candidates[str(record["id"])][method].add(target)


def records_by_file(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[(str(record.get("pak", "")), str(record.get("source_file", "")))].append(record)
    return grouped


def files_by_hash(grouped: dict[tuple[str, str], list[dict]]) -> dict[str, list[tuple[tuple[str, str], list[dict]]]]:
    result: dict[str, list[tuple[tuple[str, str], list[dict]]]] = collections.defaultdict(list)
    for key, values in grouped.items():
        if values:
            result[str(values[0].get("hash", ""))].append((key, values))
    return result


def add_exact_memory(candidates: dict, records: list[dict], target_records: list[dict]) -> dict:
    memory = collections.defaultdict(set)
    for record in records:
        src = source_text(record)
        cur = str(record.get("original") or "")
        if src and src != cur and has_vi(src) and has_cjk(cur) and is_player_visible(record):
            memory[src].add(cur)
    unique = {src: next(iter(values)) for src, values in memory.items() if len(values) == 1}
    for record in target_records:
        target = unique.get(source_text(record))
        if target:
            add_candidate(candidates, record, target, "visible_text_memory")
    return {"unique_entries": len(unique), "ambiguous_entries": sum(1 for v in memory.values() if len(v) > 1)}


def add_tsv_candidates(candidates: dict, pc_root: Path, mobile_root: Path, pc_records: list[dict], target_records: list[dict]) -> None:
    pc_hashes = files_by_hash(records_by_file(pc_records))
    mobile_hashes = files_by_hash(records_by_file(target_records))
    for hash_id in set(pc_hashes) & set(mobile_hashes):
        for mobile_key, mobile_file_records in mobile_hashes[hash_id]:
            if Path(mobile_key[1]).suffix.lower() != ".tsv":
                continue
            mobile_path = extracted_file(mobile_root, mobile_key)
            if not mobile_path.exists():
                continue
            mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_file_records}
            mobile_rows = stable_rows(mobile_path)
            mobile_signatures = stable_row_signatures(mobile_path)
            for pc_key, pc_file_records in pc_hashes[hash_id]:
                if Path(pc_key[1]).suffix.lower() != ".tsv":
                    continue
                pc_path = extracted_file(pc_root, pc_key)
                if not pc_path.exists():
                    continue
                pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_file_records}
                pc_rows = stable_rows(pc_path)
                for row_id, mobile_line in mobile_rows.items():
                    pc_line = pc_rows.get(row_id)
                    if pc_line is None:
                        continue
                    for (line, column), mobile in mobile_locators.items():
                        if line != mobile_line:
                            continue
                        pc = pc_locators.get((pc_line, column))
                        if pc:
                            add_candidate(candidates, mobile, str(pc.get("original") or ""), "visible_tsv_primary_key")
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
                            add_candidate(candidates, mobile, str(pc.get("original") or ""), "visible_tsv_row_signature")


def decode_header_cells(path: Path) -> list[str]:
    if not path.is_file():
        return []
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


def visible_header_name(name: str) -> bool:
    name = str(name or "")
    if not name:
        return False
    if INTERNAL_HEADER_RE.search(name) and not VISIBLE_HEADER_RE.search(name):
        return False
    return bool(VISIBLE_HEADER_RE.search(name))


def pak_name_from_extracted_path(path: Path) -> str:
    return f"{path.parent.name}.pak"


def add_cross_tsv_structure_candidates(
    candidates: dict,
    pc_root: Path,
    mobile_root: Path,
    pc_records: list[dict],
    target_records: list[dict],
    min_hits: int = 20,
) -> dict:
    """Match PC split TSV files to mobile merged TSV files by stable row structure."""
    pc_by_file = records_by_file(pc_records)
    mobile_by_file = records_by_file(target_records)
    pc_files = [p for p in (pc_root / "extracted").rglob("*.tsv") if p.is_file()]
    mobile_dir = mobile_root / "extracted" / "updatefs"
    mobile_files = [p for p in mobile_dir.glob("*.tsv") if p.is_file()]
    pc_index = []
    for pc_path in pc_files:
        sigs = row_signatures(pc_path)
        if sigs:
            pc_index.append((pc_path, sigs, set(sigs)))
    pair_count = 0
    pair_updates = collections.Counter()
    for mobile_path in mobile_files:
        mobile_key = ("updatefs.pak", mobile_path.name)
        mobile_records = mobile_by_file.get(mobile_key, [])
        if not mobile_records:
            continue
        mobile_sigs = row_signatures(mobile_path)
        if not mobile_sigs:
            continue
        mobile_set = set(mobile_sigs)
        mobile_headers = decode_header_cells(mobile_path)
        mobile_visible_cols = {
            idx + 1: header
            for idx, header in enumerate(mobile_headers)
            if visible_header_name(header)
        }
        if not mobile_visible_cols:
            continue
        mobile_by_locator = {(int(r.get("line") or 0), int(r.get("column") or 1)): r for r in mobile_records}
        for pc_path, pc_sigs, pc_set in pc_index:
            common = mobile_set & pc_set
            if len(common) < min_hits:
                continue
            pc_hit_rate = len(common) / max(1, len(pc_sigs))
            mobile_hit_rate = len(common) / max(1, len(mobile_sigs))
            if pc_hit_rate < 0.60 and mobile_hit_rate < 0.60:
                continue
            pc_pak = pak_name_from_extracted_path(pc_path)
            pc_key = (pc_pak, pc_path.name)
            pc_file_records = pc_by_file.get(pc_key, [])
            if not pc_file_records:
                continue
            pc_headers = decode_header_cells(pc_path)
            pc_cols_by_header: dict[str, int] = {}
            duplicates = set()
            for idx, header in enumerate(pc_headers, 1):
                if not visible_header_name(header):
                    continue
                key = re.sub(r"\s+", "", header).lower()
                if key in pc_cols_by_header:
                    duplicates.add(key)
                else:
                    pc_cols_by_header[key] = idx
            pc_cols_by_header = {k: v for k, v in pc_cols_by_header.items() if k not in duplicates}
            col_map = {}
            for mobile_col, header in mobile_visible_cols.items():
                key = re.sub(r"\s+", "", header).lower()
                pc_col = pc_cols_by_header.get(key)
                if pc_col:
                    col_map[mobile_col] = pc_col
            if not col_map:
                continue
            pc_by_locator = {(int(r.get("line") or 0), int(r.get("column") or 1)): r for r in pc_file_records}
            before = sum(len(v) for methods in candidates.values() for v in methods.values())
            for sig in common:
                mobile_line = mobile_sigs[sig]
                pc_line = pc_sigs[sig]
                for mobile_col, pc_col in col_map.items():
                    mobile = mobile_by_locator.get((mobile_line, mobile_col))
                    pc = pc_by_locator.get((pc_line, pc_col))
                    if mobile and pc:
                        add_candidate(candidates, mobile, str(pc.get("original") or ""), "visible_cross_tsv_structure")
            after = sum(len(v) for methods in candidates.values() for v in methods.values())
            added = after - before
            if added:
                pair_count += 1
                pair_updates[f"{mobile_path.name}<={pc_pak}:{pc_path.name}"] += added
    return {"pairs": pair_count, "top_pairs": dict(pair_updates.most_common(30))}


def add_ini_candidates(candidates: dict, pc_root: Path, mobile_root: Path, pc_records: list[dict], target_records: list[dict]) -> None:
    pc_hashes = files_by_hash(records_by_file(pc_records))
    mobile_hashes = files_by_hash(records_by_file(target_records))
    for hash_id in set(pc_hashes) & set(mobile_hashes):
        for mobile_key, mobile_file_records in mobile_hashes[hash_id]:
            if Path(mobile_key[1]).suffix.lower() != ".ini":
                continue
            mobile_path = extracted_file(mobile_root, mobile_key)
            if not mobile_path.exists():
                continue
            mobile_anchors = unique_ini_anchors(mobile_path)
            mobile_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in mobile_file_records}
            mobile_by_anchor = {
                anchor: mobile_locators[(line, 1)]
                for anchor, line in mobile_anchors.items()
                if (line, 1) in mobile_locators
            }
            for pc_key, pc_file_records in pc_hashes[hash_id]:
                if Path(pc_key[1]).suffix.lower() != ".ini":
                    continue
                pc_path = extracted_file(pc_root, pc_key)
                if not pc_path.exists():
                    continue
                pc_anchors = unique_ini_anchors(pc_path)
                pc_locators = {(int(r["line"]), int(r.get("column") or 1)): r for r in pc_file_records}
                for anchor, mobile in mobile_by_anchor.items():
                    pc_line = pc_anchors.get(anchor)
                    if pc_line is None:
                        continue
                    pc = pc_locators.get((pc_line, 1))
                    if pc:
                        add_candidate(candidates, mobile, str(pc.get("original") or ""), "visible_ini_section_key")


def select_candidates(candidates: dict) -> tuple[dict[str, tuple[str, str]], int]:
    priority = [
        "visible_text_memory",
        "visible_tsv_primary_key",
        "visible_tsv_row_signature",
        "visible_ini_section_key",
        "visible_cross_tsv_structure",
    ]
    selected: dict[str, tuple[str, str]] = {}
    conflicts = 0
    for record_id, methods in candidates.items():
        for method in priority:
            values = methods.get(method)
            if not values:
                continue
            if len(values) == 1:
                selected[record_id] = (method, next(iter(values)))
            else:
                conflicts += 1
            break
    return selected, conflicts


def merge_visible(pc_root: Path, mobile_root: Path, pak_name: str, workspace: Path, dry_run: bool) -> dict:
    records_path = workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    pc_records = json.loads((pc_root / "localization" / "text_records.json").read_text(encoding="utf-8"))
    target_records = [r for r in records if r.get("pak") == pak_name]
    target_headers = {
        (r.get("source_file"), int(r.get("column") or 1)): str(r.get("original") or "")
        for r in target_records
        if file_type(r) == ".tsv" and int(r.get("line") or 0) == 1
    }
    for record in target_records:
        if file_type(record) == ".tsv":
            record["_header_name"] = target_headers.get((record.get("source_file"), int(record.get("column") or 1)), "")
    candidates: dict = collections.defaultdict(lambda: collections.defaultdict(set))

    memory_report = add_exact_memory(candidates, records, target_records)
    add_tsv_candidates(candidates, pc_root, mobile_root, pc_records, target_records)
    add_ini_candidates(candidates, pc_root, mobile_root, pc_records, target_records)
    cross_tsv_report = add_cross_tsv_structure_candidates(candidates, pc_root, mobile_root, pc_records, target_records)
    selected, conflicts = select_candidates(candidates)

    method_counts = collections.Counter()
    file_counts = collections.Counter()
    visible_total = sum(1 for r in target_records if is_player_visible(r))
    visible_done_before = sum(1 for r in target_records if is_player_visible(r) and has_cjk(r.get("original")))
    for record in records:
        picked = selected.get(str(record.get("id")))
        if not picked:
            continue
        method, target = picked
        src = source_text(record)
        record["source_original"] = src
        record["original"] = target
        record["translation"] = target
        record["language"] = "zh"
        record["status"] = "已迁移"
        record["note"] = (str(record.get("note") or "").strip() + f" [玩家可见PC迁移:{method}]").strip()
        method_counts[method] += 1
        file_counts[str(record.get("source_file") or "")] += 1

    visible_done_after = sum(
        1 for r in records if r.get("pak") == pak_name and is_player_visible(r) and has_cjk(r.get("original"))
    )
    for record in records:
        record.pop("_header_name", None)
    report = {
        "pak": pak_name,
        "workspace": str(workspace.resolve()),
        "pc_root": str(pc_root.resolve()),
        "dry_run": dry_run,
        "visible_total": visible_total,
        "visible_done_before": visible_done_before,
        "visible_done_after": visible_done_after,
        "selected_updates": len(selected),
        "conflicts_skipped": conflicts,
        "methods": dict(method_counts),
        "top_files": dict(file_counts.most_common(30)),
        "memory": memory_report,
        "cross_tsv": cross_tsv_report,
    }

    out_dir = workspace / "build" / f"visible_pc_merge_{pak_name.replace('.pak', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "visible_pc_merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not dry_run and selected:
        backup = records_path.with_name(f"text_records.before_visible_pc_merge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        shutil.copy2(records_path, backup)
        records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        report["backup"] = str(backup.resolve())
        (out_dir / "visible_pc_merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str((out_dir / "visible_pc_merge_report.json").resolve())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely migrate PC Chinese into player-visible records only.")
    parser.add_argument("--pc-root", type=Path, required=True)
    parser.add_argument("--mobile-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--pak", default="updatefs.pak")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(merge_visible(args.pc_root, args.mobile_root, args.pak, args.workspace, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
