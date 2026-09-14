#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from localization_analyzer import score_text


MAPPING_VERSION = 1
API_MAPPING_VERSION = 2
PLACEHOLDER_ALIASES = [
    "♥", "♣", "♦", "♠", "★", "☆", "●", "■", "▲", "◆",
    "※", "◎", "◇", "□", "△", "▽", "◁", "▷", "◈", "◉",
]


def _csv_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in Path(root).rglob("*.csv") if path.is_file() and not path.name.startswith("_")),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _alias_text(text: str, placeholders: str) -> tuple[str, list[dict]]:
    aliases = []
    result = text or ""
    if not placeholders:
        return result, aliases
    pairs = []
    for item in placeholders.split("|"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            pairs.append((key, value))
    for index, (key, value) in enumerate(pairs):
        alias = PLACEHOLDER_ALIASES[index] if index < len(PLACEHOLDER_ALIASES) else f"◈{index + 1}◈"
        result = result.replace("{" + key + "}", alias)
        aliases.append({"key": key, "value": value, "alias": alias})
    return result, aliases


def _restore_aliases(text: str, aliases: list[dict]) -> str:
    result = text or ""
    for item in aliases or []:
        key = item.get("key")
        alias = item.get("alias")
        if key and alias:
            result = result.replace(alias, "{" + key + "}")
    return result


def merge_csv_tree(input_dir: Path, merged_csv: Path, mapping_json: Path, include_files: set[str] | None = None, untranslated_only: bool = False) -> dict:
    input_dir = Path(input_dir)
    merged_csv = Path(merged_csv)
    mapping_json = Path(mapping_json)
    files = _csv_files(input_dir)
    if include_files is not None:
        files = [path for path in files if path.relative_to(input_dir).as_posix() in include_files]
    if not files:
        raise ValueError(f"没有找到可合并的 CSV：{input_dir}")

    file_meta = []
    mapping_rows = []
    merged_rows = []
    merged_fields = ["id", "text"]
    next_id = 1
    for source in files:
        relative = source.relative_to(input_dir).as_posix()
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            if "id" not in fields or "text" not in fields:
                raise ValueError(f"CSV 缺少 id/text 表头：{relative}")
            rows = list(reader)
        selected_in_file = 0
        for row_index, row in enumerate(rows):
            if untranslated_only and score_text(row.get("text", ""))[1] not in ("vi", "mixed"):
                continue
            merged_id = str(next_id)
            original_id = row.get("id", "")
            if not original_id:
                raise ValueError(f"CSV 存在空 ID：{relative} 第 {row_index + 2} 行")
            aliased_text, aliases = _alias_text(row.get("text", ""), row.get("placeholders", ""))
            merged_row = {"id": merged_id, "text": aliased_text}
            merged_rows.append(merged_row)
            mapping_rows.append({
                "id": merged_id,
                "file": relative,
                "original_id": original_id,
                "row": row_index,
                "aliases": aliases,
            })
            selected_in_file += 1
            next_id += 1
        if selected_in_file:
            file_meta.append({"file": relative, "fieldnames": fields, "rows": len(rows), "selected_rows": selected_in_file})

    if not mapping_rows:
        raise ValueError("没有检测到仍需翻译的越南文或中越混合内容")

    merged_csv.parent.mkdir(parents=True, exist_ok=True)
    with merged_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=merged_fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged_rows)
    mapping = {
        "version": MAPPING_VERSION,
        "source_dir": str(input_dir.resolve()),
        "merged_csv": merged_csv.name,
        "rows": len(mapping_rows),
        "mode": "sparse" if untranslated_only else "full",
        "files": file_meta,
        "mapping": mapping_rows,
    }
    mapping_json.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    mapping_csv = mapping_json.with_name(mapping_json.stem + ".csv")
    with mapping_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["mapping_id", "original_id", "file", "row"], lineterminator="\n")
        writer.writeheader()
        for row in mapping_rows:
            writer.writerow({
                "mapping_id": row["id"],
                "original_id": row["original_id"],
                "file": row["file"],
                "row": int(row["row"]) + 2,
            })
    return {
        "files": len(file_meta),
        "source_files": len(files),
        "rows": len(mapping_rows),
        "mode": mapping["mode"],
        "merged_csv": str(merged_csv),
        "mapping_json": str(mapping_json),
        "mapping_csv": str(mapping_csv),
    }


def merge_remaining_csv_tree(input_dir: Path, checkpoint_json: Path, merged_csv: Path, mapping_json: Path) -> dict:
    input_dir = Path(input_dir)
    checkpoint = json.loads(Path(checkpoint_json).read_text(encoding="utf-8-sig"))
    completed = set()
    for relative, saved in (checkpoint.get("files") or {}).items():
        output = input_dir / Path(relative)
        if output.exists() and saved.get("output_sha256"):
            import hashlib
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            if digest == saved["output_sha256"]:
                completed.add(Path(relative).as_posix())
    all_files = {path.relative_to(input_dir).as_posix() for path in _csv_files(input_dir)}
    remaining = all_files - completed
    if not remaining:
        raise ValueError("断点显示所有 CSV 都已完成，无需合并剩余文件")
    report = merge_csv_tree(input_dir, merged_csv, mapping_json, remaining, untranslated_only=True)
    report.update({"completed_files": len(completed), "remaining_files": len(remaining), "selection": "remaining-untranslated"})
    return report


def split_merged_csv(merged_csv: Path, mapping_json: Path, output_dir: Path) -> dict:
    merged_csv = Path(merged_csv)
    mapping_json = Path(mapping_json)
    output_dir = Path(output_dir)
    mapping = json.loads(mapping_json.read_text(encoding="utf-8-sig"))
    if mapping.get("version") != MAPPING_VERSION:
        raise ValueError("合并 CSV 映射版本不受支持，请重新合并")
    entries = mapping.get("mapping") or []
    expected = {str(entry["id"]): entry for entry in entries}
    if len(expected) != len(entries):
        raise ValueError("映射文件包含重复数字 ID")

    with merged_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "id" not in reader.fieldnames or "text" not in reader.fieldnames:
            raise ValueError("译后合并 CSV 必须保留 id/text 表头")
        translated = {}
        for line, row in enumerate(reader, start=2):
            merged_id = (row.get("id") or "").strip()
            if not merged_id.isdigit():
                raise ValueError(f"译后合并 CSV 第 {line} 行 ID 不是数字：{merged_id!r}")
            if merged_id in translated:
                raise ValueError(f"译后合并 CSV 存在重复 ID：{merged_id}")
            if merged_id not in expected:
                raise ValueError(f"译后合并 CSV 存在未知 ID：{merged_id}")
            translated[merged_id] = row
    missing = sorted(set(expected) - set(translated), key=int)
    if missing:
        sample = ", ".join(missing[:10])
        raise ValueError(f"译后合并 CSV 缺少 {len(missing)} 个映射 ID，例如：{sample}")

    by_file = {}
    source_dir = Path(mapping.get("source_dir", ""))
    for meta in mapping.get("files", []):
        base = output_dir / Path(meta["file"])
        if not base.exists():
            base = source_dir / Path(meta["file"])
        if not base.exists():
            raise ValueError(f"恢复缺少底稿 CSV：{meta['file']}")
        with base.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != int(meta["rows"]):
            raise ValueError(f"底稿 CSV 行数已变化，无法安全恢复：{meta['file']}")
        by_file[meta["file"]] = rows
    for merged_id, entry in expected.items():
        row = dict(translated[merged_id])
        bucket = by_file.get(entry["file"])
        if bucket is None or not 0 <= int(entry["row"]) < len(bucket):
            raise ValueError(f"映射中的文件或行号无效：{merged_id}")
        if bucket[int(entry["row"])].get("id") != entry["original_id"]:
            raise ValueError(f"底稿 CSV 原始 ID 已变化，无法安全恢复：{entry['file']} 第 {int(entry['row']) + 2} 行")
        target = row.get("text")
        if target is None:
            target = row.get("original", "")
        bucket[int(entry["row"])]["text"] = _restore_aliases(target, entry.get("aliases") or [])

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for meta in mapping.get("files", []):
        relative = meta["file"]
        rows = by_file[relative]
        target = output_dir / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=meta["fieldnames"], lineterminator="\n", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(target)
        written += 1

    if source_dir.is_dir():
        for source in source_dir.glob("_*"):
            if source.is_file():
                target = output_dir / source.name
                if source.resolve() != target.resolve():
                    shutil.copy2(source, target)
    return {"files": written, "rows": len(entries), "merged_csv": str(merged_csv), "output_dir": str(output_dir)}


def prepare_api_localization_package(input_dir: Path, package_dir: Path, base_name: str, chunk_rows: int = 1000) -> dict:
    input_dir = Path(input_dir)
    package_dir = Path(package_dir)
    chunks_dir = package_dir / "chunks"
    if not input_dir.is_dir():
        raise ValueError(f"未找到 CSV 导出目录：{input_dir}")
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    unique_rows = []
    unique_by_text = {}
    mapping_rows = []
    file_meta = []
    occurrence_count = 0
    for source in _csv_files(input_dir):
        relative = source.relative_to(input_dir).as_posix()
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            if "id" not in fields or "text" not in fields:
                raise ValueError(f"CSV 缺少 id/text 表头：{relative}")
            rows = list(reader)
        selected = 0
        for row_index, row in enumerate(rows):
            original_id = row.get("id", "")
            if not original_id:
                continue
            aliased_text, aliases = _alias_text(row.get("text", ""), row.get("placeholders", ""))
            if score_text(aliased_text)[1] not in ("vi", "mixed"):
                continue
            mapping_id = unique_by_text.get(aliased_text)
            if mapping_id is None:
                mapping_id = str(len(unique_rows) + 1)
                unique_by_text[aliased_text] = mapping_id
                unique_rows.append({"id": mapping_id, "text": aliased_text})
            mapping_rows.append({
                "id": mapping_id,
                "file": relative,
                "original_id": original_id,
                "row": row_index,
                "aliases": aliases,
            })
            occurrence_count += 1
            selected += 1
        if selected:
            file_meta.append({"file": relative, "fieldnames": fields, "rows": len(rows), "selected_rows": selected})
    if not unique_rows:
        raise ValueError("没有检测到需要 API 翻译的越南文或中越混合内容")

    master_csv = package_dir / f"{base_name}_localization.csv"
    with master_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "text"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(unique_rows)

    chunk_size = max(1, int(chunk_rows or 1000))
    chunk_files = []
    for index, start in enumerate(range(0, len(unique_rows), chunk_size), 1):
        chunk = unique_rows[start : start + chunk_size]
        chunk_path = chunks_dir / f"{base_name}_localization_{index:02d}.csv"
        with chunk_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["id", "text"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(chunk)
        chunk_files.append(str(chunk_path))

    mapping = {
        "version": API_MAPPING_VERSION,
        "mode": "api-dedup-chunks",
        "source_dir": str(input_dir.resolve()),
        "package_dir": str(package_dir.resolve()),
        "base_name": base_name,
        "master_csv": master_csv.name,
        "unique_rows": len(unique_rows),
        "occurrence_rows": occurrence_count,
        "chunk_rows": chunk_size,
        "chunks": [Path(p).name for p in chunk_files],
        "files": file_meta,
        "mapping": mapping_rows,
    }
    mapping_json = package_dir / f"{base_name}_localization_mapping.json"
    mapping_json.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    mapping_csv = package_dir / f"{base_name}_localization_mapping.csv"
    with mapping_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["mapping_id", "original_id", "file", "row"], lineterminator="\n")
        writer.writeheader()
        for item in mapping_rows:
            writer.writerow({"mapping_id": item["id"], "original_id": item["original_id"], "file": item["file"], "row": int(item["row"]) + 2})
    return {
        "source_dir": str(input_dir),
        "package_dir": str(package_dir),
        "master_csv": str(master_csv),
        "mapping_json": str(mapping_json),
        "mapping_csv": str(mapping_csv),
        "chunks_dir": str(chunks_dir),
        "chunks": len(chunk_files),
        "unique_rows": len(unique_rows),
        "occurrence_rows": occurrence_count,
        "files": len(file_meta),
    }


def apply_api_localization_package(package_dir: Path, translated_dir: Path, output_dir: Path) -> dict:
    package_dir = Path(package_dir)
    translated_dir = Path(translated_dir)
    output_dir = Path(output_dir)
    mapping_path = next(package_dir.glob("*_localization_mapping.json"), None)
    if not mapping_path:
        raise ValueError(f"未找到 API 映射文件：{package_dir}")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    if mapping.get("version") != API_MAPPING_VERSION:
        raise ValueError("API 映射版本不受支持，请重新生成 API 翻译包")
    source_dir = Path(mapping.get("source_dir", ""))
    if not source_dir.is_dir():
        raise ValueError(f"未找到底稿 CSV 目录：{source_dir}")
    if not translated_dir.is_dir():
        raise ValueError(f"未找到 API 译后分片目录：{translated_dir}")

    translated = {}
    for csv_path in _csv_files(translated_dir):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or "id" not in reader.fieldnames or "text" not in reader.fieldnames:
                continue
            for row in reader:
                row_id = str(row.get("id", "")).strip()
                if row_id:
                    translated[row_id] = row.get("text", "")
    if not translated:
        raise ValueError("API 译后分片中没有可用译文")

    by_file = {}
    fields_by_file = {}
    for meta in mapping.get("files", []):
        source = source_dir / Path(meta["file"])
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields_by_file[meta["file"]] = reader.fieldnames or ["id", "text"]
            rows = list(reader)
        if len(rows) != int(meta["rows"]):
            raise ValueError(f"底稿 CSV 行数已变化，无法安全恢复：{meta['file']}")
        by_file[meta["file"]] = rows

    updated = skipped = 0
    for item in mapping.get("mapping", []):
        target = translated.get(str(item.get("id", "")).strip())
        if target is None:
            skipped += 1
            continue
        rows = by_file.get(item["file"])
        row_index = int(item["row"])
        if rows is None or not 0 <= row_index < len(rows):
            skipped += 1
            continue
        if rows[row_index].get("id") != item["original_id"]:
            raise ValueError(f"底稿 CSV 原始 ID 已变化，无法安全恢复：{item['file']} 第 {row_index + 2} 行")
        rows[row_index]["text"] = _restore_aliases(target, item.get("aliases") or [])
        updated += 1

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for relative, rows in by_file.items():
        target = output_dir / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields_by_file[relative], lineterminator="\n", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(target)
        written += 1
    for source in source_dir.glob("_*"):
        if source.is_file():
            shutil.copy2(source, output_dir / source.name)
    return {
        "package_dir": str(package_dir),
        "translated_dir": str(translated_dir),
        "output_dir": str(output_dir),
        "files": written,
        "updated": updated,
        "skipped": skipped,
        "unique_translated": len(translated),
    }


def prepare_api_localization_file(input_csv: Path, package_dir: Path, base_name: str, chunk_rows: int = 1000) -> dict:
    input_csv = Path(input_csv)
    package_dir = Path(package_dir)
    chunks_dir = package_dir / "chunks"
    if not input_csv.is_file():
        raise ValueError(f"未找到完整本地化 CSV：{input_csv}")
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    with input_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if "id" not in fields or "text" not in fields:
            raise ValueError("完整本地化 CSV 必须包含 id,text 两列")
        source_rows = list(reader)

    unique_rows = []
    unique_by_text = {}
    mapping_rows = []
    for row_index, row in enumerate(source_rows):
        original_id = (row.get("id") or "").strip()
        if not original_id:
            continue
        aliased_text, aliases = _alias_text(row.get("text", ""), row.get("placeholders", ""))
        if score_text(aliased_text)[1] not in ("vi", "mixed"):
            continue
        mapping_id = unique_by_text.get(aliased_text)
        if mapping_id is None:
            mapping_id = str(len(unique_rows) + 1)
            unique_by_text[aliased_text] = mapping_id
            unique_rows.append({"id": mapping_id, "text": aliased_text})
        mapping_rows.append({
            "id": mapping_id,
            "original_id": original_id,
            "row": row_index,
            "aliases": aliases,
        })
    if not unique_rows:
        raise ValueError("完整本地化 CSV 中没有检测到需要 API 翻译的越南文或中越混合内容")

    unique_csv = package_dir / f"{base_name}_localization_unique.csv"
    with unique_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "text"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(unique_rows)

    chunk_size = max(1, int(chunk_rows or 1000))
    chunk_files = []
    for index, start in enumerate(range(0, len(unique_rows), chunk_size), 1):
        chunk_path = chunks_dir / f"{base_name}_localization_{index:02d}.csv"
        with chunk_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["id", "text"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(unique_rows[start : start + chunk_size])
        chunk_files.append(chunk_path.name)

    mapping = {
        "version": API_MAPPING_VERSION,
        "mode": "api-master-csv-dedup-chunks",
        "source_csv": str(input_csv.resolve()),
        "package_dir": str(package_dir.resolve()),
        "base_name": base_name,
        "fields": fields,
        "source_rows": len(source_rows),
        "unique_rows": len(unique_rows),
        "occurrence_rows": len(mapping_rows),
        "chunk_rows": chunk_size,
        "unique_csv": unique_csv.name,
        "chunks": chunk_files,
        "mapping": mapping_rows,
    }
    mapping_json = package_dir / f"{base_name}_localization_mapping.json"
    mapping_json.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    mapping_csv = package_dir / f"{base_name}_localization_mapping.csv"
    with mapping_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["mapping_id", "original_id", "row"], lineterminator="\n")
        writer.writeheader()
        for item in mapping_rows:
            writer.writerow({"mapping_id": item["id"], "original_id": item["original_id"], "row": int(item["row"]) + 2})
    return {
        "source_csv": str(input_csv),
        "package_dir": str(package_dir),
        "unique_csv": str(unique_csv),
        "mapping_json": str(mapping_json),
        "mapping_csv": str(mapping_csv),
        "chunks_dir": str(chunks_dir),
        "chunks": len(chunk_files),
        "source_rows": len(source_rows),
        "unique_rows": len(unique_rows),
        "occurrence_rows": len(mapping_rows),
    }


def apply_api_localization_file(package_dir: Path, translated_dir: Path, output_csv: Path) -> dict:
    package_dir = Path(package_dir)
    translated_dir = Path(translated_dir)
    output_csv = Path(output_csv)
    mapping_path = next(package_dir.glob("*_localization_mapping.json"), None)
    if not mapping_path:
        raise ValueError(f"未找到 API 映射文件：{package_dir}")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
    if mapping.get("version") != API_MAPPING_VERSION or mapping.get("mode") != "api-master-csv-dedup-chunks":
        raise ValueError("API 映射不是完整本地化 CSV 模式，请重新生成 API 翻译包")
    source_csv = Path(mapping.get("source_csv", ""))
    if not source_csv.is_file():
        raise ValueError(f"未找到完整本地化 CSV 底稿：{source_csv}")
    if not translated_dir.is_dir():
        raise ValueError(f"未找到 API 译后分片目录：{translated_dir}")

    with source_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or mapping.get("fields") or ["id", "text"]
        rows = list(reader)

    translated = {}
    for csv_path in sorted(p for p in translated_dir.glob("*.csv") if p.is_file() and not p.name.startswith("_")):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or "id" not in reader.fieldnames or "text" not in reader.fieldnames:
                continue
            for row in reader:
                row_id = str(row.get("id", "")).strip()
                if row_id:
                    translated[row_id] = row.get("text", "")
    if not translated:
        raise ValueError("API 译后分片中没有可用译文")

    updated = skipped = 0
    for item in mapping.get("mapping", []):
        target = translated.get(str(item.get("id", "")).strip())
        if target is None:
            skipped += 1
            continue
        row_index = int(item.get("row", -1))
        if not 0 <= row_index < len(rows):
            skipped += 1
            continue
        if rows[row_index].get("id") != item.get("original_id"):
            raise ValueError(f"完整本地化 CSV 原始 ID 已变化，无法安全恢复：第 {row_index + 2} 行")
        rows[row_index]["text"] = _restore_aliases(target, item.get("aliases") or [])
        updated += 1

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return {
        "package_dir": str(package_dir),
        "translated_dir": str(translated_dir),
        "output_csv": str(output_csv),
        "updated": updated,
        "skipped": skipped,
        "unique_translated": len(translated),
        "rows": len(rows),
    }
