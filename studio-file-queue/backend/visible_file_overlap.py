#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path


CJK_RE = re.compile(r"[\u3400-\u9fff]")
TOKEN_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]{2,}")


def cjk_count(text: str) -> int:
    return sum(1 for ch in str(text or "") if "\u3400" <= ch <= "\u9fff")


def normalize(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"<[^<>\r\n]{1,160}>", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def is_visible_zh(text: str) -> bool:
    text = normalize(text)
    return len(text) >= 4 and cjk_count(text) >= 3


def shingles(text: str) -> set[str]:
    text = normalize(text)
    if len(text) < 8:
        return {text} if is_visible_zh(text) else set()
    return {text[i : i + 8] for i in range(0, max(1, len(text) - 7))}


def load_records(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_mobile_index(records: list[dict], pak: str) -> tuple[dict[str, set[str]], dict[str, set[str]], collections.Counter]:
    exact: dict[str, set[str]] = collections.defaultdict(set)
    shingle_index: dict[str, set[str]] = collections.defaultdict(set)
    totals = collections.Counter()
    for record in records:
        if record.get("pak") != pak:
            continue
        file = str(record.get("source_file") or "")
        text = normalize(str(record.get("original") or ""))
        if not is_visible_zh(text):
            continue
        totals[file] += 1
        exact[text].add(file)
        for token in shingles(text):
            shingle_index[token].add(file)
    return exact, shingle_index, totals


def analyze(pc_records: list[dict], mobile_records: list[dict], pak: str, min_hits: int) -> dict:
    exact, shingle_index, mobile_totals = build_mobile_index(mobile_records, pak)
    overlaps: dict[tuple[str, str], dict] = {}
    pc_totals = collections.Counter()
    for record in pc_records:
        pc_file = f"{record.get('pak')}:{record.get('source_file')}"
        text = normalize(str(record.get("original") or ""))
        if not is_visible_zh(text):
            continue
        pc_totals[pc_file] += 1
        candidate_files = set(exact.get(text) or set())
        if not candidate_files:
            votes = collections.Counter()
            for token in shingles(text):
                for mobile_file in shingle_index.get(token, ()):
                    votes[mobile_file] += 1
            candidate_files = {file for file, count in votes.items() if count >= 2}
        for mobile_file in candidate_files:
            key = (mobile_file, pc_file)
            item = overlaps.setdefault(key, {"mobile_file": mobile_file, "pc_file": pc_file, "hits": 0, "samples": []})
            item["hits"] += 1
            if len(item["samples"]) < 3:
                item["samples"].append(text[:80])
    rows = []
    for (mobile_file, pc_file), item in overlaps.items():
        if item["hits"] < min_hits:
            continue
        item["pc_zh_total"] = pc_totals[pc_file]
        item["mobile_zh_total"] = mobile_totals[mobile_file]
        item["pc_hit_rate"] = round(item["hits"] * 100 / max(1, pc_totals[pc_file]), 2)
        rows.append(item)
    rows.sort(key=lambda x: (-x["hits"], -x["pc_hit_rate"], x["mobile_file"], x["pc_file"]))
    by_mobile = collections.defaultdict(list)
    for row in rows:
        by_mobile[row["mobile_file"]].append(row)
    return {
        "pak": pak,
        "rows": rows,
        "by_mobile": {k: v[:30] for k, v in sorted(by_mobile.items(), key=lambda kv: -sum(x["hits"] for x in kv[1]))},
        "pc_visible_files": len(pc_totals),
        "mobile_visible_files": len(mobile_totals),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Find PC files whose Chinese text appears inside mobile files.")
    parser.add_argument("--pc-records", type=Path, required=True)
    parser.add_argument("--mobile-records", type=Path, required=True)
    parser.add_argument("--pak", default="updatefs.pak")
    parser.add_argument("--min-hits", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("visible_file_overlap_report.json"))
    args = parser.parse_args()
    report = analyze(load_records(args.pc_records), load_records(args.mobile_records), args.pak, args.min_hits)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows" and k != "by_mobile"}, ensure_ascii=False, indent=2))
    print(f"rows={len(report['rows'])}")
    for row in report["rows"][:30]:
        print(f"{row['hits']:5} {row['pc_hit_rate']:6.2f}% {row['mobile_file']} <= {row['pc_file']} | {row['samples'][0] if row['samples'] else ''}")


if __name__ == "__main__":
    main()
