#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path


TEXT_EXTS = {".tsv"}
NUMERIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[%‰])?$")
ITEM_CODE_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
PATH_RE = re.compile(r"^[\\/][A-Za-z0-9_./\\:-]+$")
ASCII_CODE_RE = re.compile(r"^[A-Za-z0-9_./\\:-]{2,}$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
VI_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]"
)


def decode_cell(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030", "cp1258", "latin1"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore").strip()


def stable_token(cell: str) -> str | None:
    value = cell.strip()
    if not value:
        return None
    low = value.lower()
    if ITEM_CODE_RE.match(value):
        return "I:" + value
    if PATH_RE.match(value):
        return "P:" + low
    if NUMERIC_RE.match(value):
        return "N:" + value.lstrip("+")
    if ASCII_CODE_RE.match(value) and not (CJK_RE.search(value) or VI_RE.search(value)):
        return "A:" + low
    return None


def row_signatures(path: Path) -> dict[tuple[str, ...], int]:
    found: dict[tuple[str, ...], int] = {}
    dup = set()
    for row_no, raw in enumerate(path.read_bytes().splitlines(), 1):
        if row_no == 1:
            continue
        cells = raw.split(b"\t")
        tokens = []
        for cell in cells:
            token = stable_token(decode_cell(cell))
            if token:
                tokens.append(token)
        signature = tuple(tokens)
        if len(signature) < 4:
            continue
        if signature in found:
            dup.add(signature)
        else:
            found[signature] = row_no
    return {sig: row for sig, row in found.items() if sig not in dup}


def all_tsv_files(root: Path) -> list[Path]:
    return [p for p in (root / "extracted").rglob("*.tsv") if p.is_file()]


def analyze(pc_root: Path, mobile_root: Path, mobile_pak: str, min_hits: int) -> dict:
    mobile_dir = mobile_root / "extracted" / Path(mobile_pak).stem
    mobile_files = [p for p in mobile_dir.glob("*.tsv") if p.is_file()]
    pc_files = all_tsv_files(pc_root)
    pc_index = []
    for path in pc_files:
        sigs = row_signatures(path)
        if sigs:
            pc_index.append((path, sigs))
    rows = []
    for mobile_path in mobile_files:
        msigs = row_signatures(mobile_path)
        if not msigs:
            continue
        mset = set(msigs)
        for pc_path, psigs in pc_index:
            common = mset & set(psigs)
            if len(common) < min_hits:
                continue
            rows.append({
                "mobile_file": mobile_path.name,
                "pc_file": str(pc_path.relative_to(pc_root / "extracted")),
                "hits": len(common),
                "mobile_rows": len(msigs),
                "pc_rows": len(psigs),
                "pc_hit_rate": round(len(common) * 100 / max(1, len(psigs)), 2),
                "mobile_hit_rate": round(len(common) * 100 / max(1, len(msigs)), 2),
            })
    rows.sort(key=lambda x: (-x["hits"], -x["pc_hit_rate"], x["mobile_file"], x["pc_file"]))
    return {"rows": rows, "pc_files": len(pc_index), "mobile_files": len(mobile_files)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Find TSV file relationships by stable row structure.")
    parser.add_argument("--pc-root", type=Path, required=True)
    parser.add_argument("--mobile-root", type=Path, required=True)
    parser.add_argument("--mobile-pak", default="updatefs.pak")
    parser.add_argument("--min-hits", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.pc_root, args.mobile_root, args.mobile_pak, args.min_hits)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    print(f"rows={len(report['rows'])}")
    for row in report["rows"][:80]:
        print(f"{row['hits']:5} pc={row['pc_hit_rate']:6.2f}% mob={row['mobile_hit_rate']:6.2f}% {row['mobile_file']} <= {row['pc_file']}")


if __name__ == "__main__":
    main()
