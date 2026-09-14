#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from pc_content_migrator import stable_row_signatures, stable_rows, unique_ini_anchors


RESOURCE_RE = re.compile(r"^(?P<prefix>\d+)_(?P<hash>[0-9A-Fa-f]{8})(?P<ext>\.[^.]+)$")
TEXT_EXTS = {".tsv", ".ini", ".txt"}


def resource_key(path: Path) -> tuple[str, str] | None:
    match = RESOURCE_RE.match(path.name)
    if not match:
        return None
    return match.group("hash").upper(), match.group("ext").lower()


def relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def line_shape(path: Path) -> tuple[int, tuple[int, ...]]:
    lines = path.read_bytes().splitlines()
    return len(lines), tuple(line.count(b"\t") + 1 for line in lines)


def overlap(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def structural_score(mobile: Path, pc: Path) -> tuple[int, str, dict]:
    ext = mobile.suffix.lower()
    mobile_shape = line_shape(mobile)
    pc_shape = line_shape(pc)
    details = {"mobile_lines": mobile_shape[0], "pc_lines": pc_shape[0]}
    if ext != pc.suffix.lower():
        return 0, "扩展名不同", details
    if mobile_shape == pc_shape:
        return 100, "同资源哈希且行列结构完全一致", details
    if ext == ".ini":
        value = overlap(set(unique_ini_anchors(mobile)), set(unique_ini_anchors(pc)))
        details["anchor_overlap"] = round(value, 4)
        if value >= 0.95:
            return 98, "同资源哈希，INI 节/键锚点高度一致", details
        if value >= 0.75:
            return 92, "同资源哈希，INI 节/键大部分一致", details
        return 82, "同资源哈希，但 INI 版本结构差异较大", details
    if ext == ".tsv":
        primary = overlap(set(stable_rows(mobile)), set(stable_rows(pc)))
        signatures = overlap(set(stable_row_signatures(mobile)), set(stable_row_signatures(pc)))
        value = max(primary, signatures)
        details.update(primary_overlap=round(primary, 4), signature_overlap=round(signatures, 4))
        if value >= 0.95:
            return 98, "同资源哈希，TSV 稳定行高度一致", details
        if value >= 0.70:
            return 92, "同资源哈希，TSV 稳定行大部分一致", details
        return 82, "同资源哈希，但 TSV 可能被拆分或合并", details
    ratio = min(mobile_shape[0], pc_shape[0]) / max(1, max(mobile_shape[0], pc_shape[0]))
    details["line_ratio"] = round(ratio, 4)
    return (95, "同资源哈希，文本行数接近", details) if ratio >= 0.95 else (85, "同资源哈希，文本行数不同", details)


def build_index(mobile_root: Path, pc_root: Path, output_dir: Path) -> dict:
    mobile_root = mobile_root.resolve()
    pc_root = pc_root.resolve()
    pc_by_key: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path in pc_root.rglob("*"):
        key = resource_key(path) if path.is_file() else None
        if key:
            pc_by_key[key].append(path)

    rows = []
    for mobile in sorted(p for p in mobile_root.rglob("*") if p.is_file() and resource_key(p)):
        key = resource_key(mobile)
        candidates = pc_by_key.get(key, [])
        ranked = []
        for pc in candidates:
            score, reason, details = structural_score(mobile, pc)
            ranked.append((score, relative(pc, pc_root), reason, details, pc))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        best = ranked[0] if ranked else None
        tied = [item for item in ranked if best and item[0] == best[0]]
        # PC packages often contain byte-identical copies of the same resource.
        # Those are aliases, not conflicting object candidates.
        ambiguous = bool(best and len(tied) > 1 and len({file_digest(item[4]) for item in tied}) > 1)
        confidence = best[0] if best and not ambiguous else (min(best[0], 79) if best else 0)
        status = "自动迁移" if confidence >= 92 else ("人工复核" if best else "国际版无同哈希文件")
        rows.append({
            "越南版文件": relative(mobile, mobile_root),
            "国际版文件": best[1] if best else "",
            "资源哈希": key[0],
            "类型": key[1],
            "置信度": confidence,
            "处理建议": status,
            "判定依据": (best[2] + ("；存在同分候选" if ambiguous else "")) if best else "未找到同哈希同类型资源",
            "越南版行数": best[3].get("mobile_lines", "") if best else "",
            "国际版行数": best[3].get("pc_lines", "") if best else "",
            "候选数量": len(ranked),
            "结构详情": json.dumps(best[3], ensure_ascii=False, separators=(",", ":")) if best else "{}",
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "文件对照表.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    json_path = output_dir / "文件对照表.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "mobile_root": str(mobile_root),
        "pc_root": str(pc_root),
        "mobile_files": len(rows),
        "same_hash_matches": sum(bool(row["国际版文件"]) for row in rows),
        "auto_migration_files": sum(row["处理建议"] == "自动迁移" for row in rows),
        "review_files": sum(row["处理建议"] == "人工复核" for row in rows),
        "unmatched_files": sum(row["处理建议"] == "国际版无同哈希文件" for row in rows),
        "csv": str(csv_path.resolve()),
        "json": str(json_path.resolve()),
        "index_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
    }
    (output_dir / "索引摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def write_progress(index_csv: Path, migration_csv: Path, output_csv: Path) -> dict:
    with index_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    migrated_by_hash: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with migration_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            migrated_by_hash[str(item.get("hash", "")).upper()][str(item.get("method", ""))] += 1
    review_rows = []
    for row in rows:
        if row.get("处理建议") != "人工复核":
            continue
        methods = migrated_by_hash.get(str(row.get("资源哈希", "")).upper(), {})
        count = sum(methods.values())
        if count:
            state = "已部分处理"
        elif row.get("类型") not in TEXT_EXTS:
            state = "非文本资源，无需汉化"
        else:
            state = "仍缺可靠字段锚点"
        review_rows.append({
            **row,
            "已迁移记录数": count,
            "迁移方法": json.dumps(methods, ensure_ascii=False, separators=(",", ":")),
            "处理进度": state,
        })
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(review_rows[0]))
        writer.writeheader()
        writer.writerows(review_rows)
    return {
        "review_candidates": len(review_rows),
        "partially_processed": sum(row["处理进度"] == "已部分处理" for row in review_rows),
        "non_text": sum(row["处理进度"] == "非文本资源，无需汉化" for row in review_rows),
        "unresolved_text": sum(row["处理进度"] == "仍缺可靠字段锚点" for row in review_rows),
        "output": str(output_csv.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="建立越南手机版与国际版资源文件对象索引。")
    parser.add_argument("mobile_root", type=Path, nargs="?")
    parser.add_argument("pc_root", type=Path, nargs="?")
    parser.add_argument("output_dir", type=Path, nargs="?")
    parser.add_argument("--progress-index", type=Path)
    parser.add_argument("--migration-csv", type=Path)
    parser.add_argument("--progress-output", type=Path)
    args = parser.parse_args()
    if args.progress_index and args.migration_csv and args.progress_output:
        report = write_progress(args.progress_index, args.migration_csv, args.progress_output)
    else:
        if not args.mobile_root or not args.pc_root or not args.output_dir:
            parser.error("建立索引需要 mobile_root、pc_root 和 output_dir。")
        report = build_index(args.mobile_root, args.pc_root, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
