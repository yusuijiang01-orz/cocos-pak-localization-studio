#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, json, sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from localization_analyzer import decode_best
from parallel_config import worker_count


def _decode_cell(cell: bytes) -> tuple[str, str]:
    if not cell:
        return "", "empty"
    if all(b < 128 for b in cell):
        return cell.decode("ascii", "replace").strip(" \t\r\n\ufeff"), "ascii"
    text, enc, _lang, _score = decode_best(cell)
    return text, enc


def convert_tsv_file(src: Path, dst: Path) -> dict:
    rows = 0
    cols_max = 0
    encodings: dict[str, int] = {}
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fin, dst.open("w", encoding="utf-8-sig", newline="") as fout:
        writer = csv.writer(fout, lineterminator="\n")
        for raw_line in fin.read().splitlines():
            cells = raw_line.split(b"\t")
            decoded = []
            for cell in cells:
                text, enc = _decode_cell(cell)
                decoded.append(text)
                encodings[enc] = encodings.get(enc, 0) + 1
            writer.writerow(decoded)
            rows += 1
            cols_max = max(cols_max, len(cells))
    return {
        "source": str(src),
        "output": str(dst),
        "rows": rows,
        "max_columns": cols_max,
        "encodings": encodings,
    }


def _convert_task(args):
    src_s, src_root_s, out_root_s = args
    src = Path(src_s)
    rel = src.relative_to(Path(src_root_s)).with_suffix(".csv")
    return convert_tsv_file(src, Path(out_root_s) / rel)


def convert_tsv_tree(src_dir: Path, out_dir: Path, workers: int | None = None) -> dict:
    files = sorted(p for p in src_dir.rglob("*.tsv") if p.is_file())
    wc = worker_count(workers)
    jobs = [(str(p), str(src_dir), str(out_dir)) for p in files]
    if wc <= 1 or len(jobs) < 4:
        results = [_convert_task(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=wc) as ex:
            results = list(ex.map(_convert_task, jobs, chunksize=1))
    report = {
        "source_dir": str(src_dir),
        "output_dir": str(out_dir),
        "file_count": len(results),
        "workers": wc,
        "files": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_tsv_to_csv_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv):
    if len(argv) not in (3, 4):
        print("Usage: tabular_converter.py <tsv-file-or-folder> <output-folder-or-csv>")
        return 2
    src = Path(argv[1])
    out = Path(argv[2])
    if src.is_dir():
        report = convert_tsv_tree(src, out)
    else:
        report = {"file_count": 1, "files": [convert_tsv_file(src, out)]}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
