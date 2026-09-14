from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from xlsx_localization import (
    export_xlsx_mapping,
    import_xlsx_to_modified,
    read_simple_xlsx,
    remap_xlsx_by_source,
    write_simple_xlsx,
)
import pytest


def test_xlsx_maps_identifiers_before_dedup_and_fans_out_on_import(tmp_path):
    source = tmp_path / "source"
    export_dir = tmp_path / "export"
    output = tmp_path / "output"
    source.mkdir(parents=True)
    resource = source / "sample.tsv"
    resource.write_text(
        "Nhận %s vật phẩm 10 từ $PLAYER_ID\n"
        "Nhận %d vật phẩm 20 từ $TARGET_ID\n",
        encoding="utf-8",
    )

    report = export_xlsx_mapping(source, export_dir, "sample", "sample.pak", workers=1)
    assert report["source_rows"] == 2
    assert report["unique_rows"] == 1
    assert report["mapping_version"] == 5
    assert report["placeholder_style"] == "diamond-number-v1"

    workbook_rows = read_simple_xlsx(Path(report["xlsx"]))
    assert len(workbook_rows) == 1
    assert workbook_rows[0]["text"] == "Nhận ◈1◈ vật phẩm ◈2◈ từ ◈3◈"

    mapping = json.loads(Path(report["mapping_json"]).read_text(encoding="utf-8"))
    assert len(mapping["rows"]) == 1
    assert len(mapping["rows"][0][2]) == 2

    # Simulate a translator changing the marker brackets. Import must repair
    # those markers and fan the single translated row back to both sources.
    translated_xlsx = export_dir / "sample_translated.xlsx"
    write_simple_xlsx(
        translated_xlsx,
        [{"id": workbook_rows[0]["id"], "text": "从《P3》获得（P2）件道具{P1}"}],
        ["id", "text"],
    )
    imported = import_xlsx_to_modified(
        source,
        translated_xlsx,
        Path(report["mapping_json"]),
        output,
    )
    assert imported["updated"] == 2
    result = (output / "sample.tsv").read_text(encoding="utf-8")
    assert "从$PLAYER_ID获得10件道具%s" in result
    assert "从$TARGET_ID获得20件道具%d" in result


def test_xlsx_import_preserves_nested_relative_path(tmp_path):
    source = tmp_path / "source"
    nested = source / "ui" / "dialogs"
    nested.mkdir(parents=True)
    resource = nested / "labels.tsv"
    resource.write_text("Nhận nhiệm vụ\n", encoding="utf-8")
    export_dir = tmp_path / "export"
    output = tmp_path / "output"

    report = export_xlsx_mapping(source, export_dir, "nested", "nested.pak", workers=1)
    row = read_simple_xlsx(Path(report["xlsx"]))[0]
    translated = export_dir / "translated.xlsx"
    write_simple_xlsx(translated, [{"id": row["id"], "text": "领取任务"}], ["id", "text"])
    imported = import_xlsx_to_modified(source, translated, Path(report["mapping_json"]), output)

    assert imported["updated"] == 1
    assert imported["changed_files"] == ["ui/dialogs/labels.tsv"]
    assert (output / "ui" / "dialogs" / "labels.tsv").read_text(encoding="utf-8") == "领取任务\n"
    assert not (output / "labels.tsv").exists()


def test_xlsx_import_discards_stale_modified_resource_bytes(tmp_path):
    source = tmp_path / "extracted" / "updatefs"
    source.mkdir(parents=True)
    raw_source = tmp_path / "_raw_reference" / "updatefs"
    raw_source.mkdir(parents=True)
    resource = source / "0001_AAAAAAAA.tsv"
    resource.write_text("NpcName\tResourcePath\nThương nhân\t\\spr\\npc\\official.spr\n", encoding="utf-8")
    # The analysis copy may have normalized a legacy path. The raw reference
    # remains authoritative for every byte outside the translated cell.
    (raw_source / resource.name).write_text(
        "NpcName\tResourcePath\nThương nhân\t\\spr\\npc\\official.spr\n", encoding="utf-8"
    )
    report = export_xlsx_mapping(source, tmp_path / "export", "updatefs", "updatefs.pak", workers=1)
    row = read_simple_xlsx(Path(report["xlsx"]))[0]
    translated = tmp_path / "translated.xlsx"
    write_simple_xlsx(translated, [{"id": row["id"], "text": "商人"}], ["id", "text"])

    output = tmp_path / "modified"
    output.mkdir()
    (output / resource.name).write_text(
        "NpcName\tResourcePath\nBroken\t\\spr\\npc\\damaged.spr\n", encoding="utf-8"
    )

    imported = import_xlsx_to_modified(source, translated, Path(report["mapping_json"]), output)

    assert imported["updated"] == 1
    rebuilt = (output / resource.name).read_text(encoding="utf-8")
    assert "商人" in rebuilt
    assert "\\spr\\npc\\official.spr" in rebuilt
    assert "damaged.spr" not in rebuilt
    assert imported["raw_byte_source"] is True


def test_old_xlsx_mapping_never_overwrites_changed_new_version_source(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    resource = source / "tasks.txt"
    resource.write_text("Bái phỏng Tạp hóa Thương\n", encoding="utf-8")
    export_dir = tmp_path / "export"
    report = export_xlsx_mapping(source, export_dir, "updatefs", "updatefs.pak", workers=1)
    row = read_simple_xlsx(Path(report["xlsx"]))[0]
    translated = export_dir / "translated.xlsx"
    write_simple_xlsx(translated, [{"id": row["id"], "text": "拜访杂货商"}], ["id", "text"])

    # A game update reused the same file/line for different text.
    resource.write_text("Tiêu diệt Tuyết Quái\n", encoding="utf-8")
    output = tmp_path / "output"
    imported = import_xlsx_to_modified(source, translated, Path(report["mapping_json"]), output)

    assert imported["updated"] == 0
    assert imported["skipped"] >= 1
    assert any("新版源文本" in risk["reason"] for risk in imported["risks"])
    assert (output / "tasks.txt").read_text(encoding="utf-8") == "Tiêu diệt Tuyết Quái\n"


def test_xlsx_from_another_export_cannot_match_by_row_number(tmp_path):
    first = tmp_path / "first"; first.mkdir()
    second = tmp_path / "second"; second.mkdir()
    (first / "tasks.txt").write_text("Khổn Thú\nThiên Công\n", encoding="utf-8")
    (second / "tasks.txt").write_text("Đưa Thư C.1\nTân thủ chiến trường\n", encoding="utf-8")
    first_report = export_xlsx_mapping(first, tmp_path / "first_export", "updatefs", "updatefs.pak", workers=1)
    second_report = export_xlsx_mapping(second, tmp_path / "second_export", "updatefs", "updatefs.pak", workers=1)

    first_rows = read_simple_xlsx(Path(first_report["xlsx"]))
    translated = tmp_path / "wrong.xlsx"
    write_simple_xlsx(translated, [
        {"id": first_rows[0]["id"], "text": "苦难兽"},
        {"id": first_rows[1]["id"], "text": "天宫"},
    ], ["id", "text"])

    with pytest.raises(ValueError, match="不属于同一次导出"):
        import_xlsx_to_modified(second, translated, Path(second_report["mapping_json"]), tmp_path / "output")


def test_unrestored_transport_placeholder_is_rejected(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "tasks.txt").write_text("Đạt cấp %d có thể gặp\n", encoding="utf-8")
    report = export_xlsx_mapping(source, tmp_path / "export", "updatefs", "updatefs.pak", workers=1)
    row = read_simple_xlsx(Path(report["xlsx"]))[0]
    translated = tmp_path / "translated.xlsx"
    write_simple_xlsx(translated, [{"id": row["id"], "text": "达到等级 ◈1◈ 可以遇到 ◈99◈"}], ["id", "text"])

    imported = import_xlsx_to_modified(source, translated, Path(report["mapping_json"]), tmp_path / "output")
    assert imported["updated"] == 0
    assert imported["risk_count"] == 1


def test_export_prefills_current_studio_translation(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "0102_0A24A485.lua").write_text('{"Khổn Thú", "GetSpecTask"}\n', encoding="utf-8")
    records_path = tmp_path / "text_records.json"
    records_path.write_text(json.dumps([{
        "id": "record-1", "pak": "updatefs.pak", "source_file": "0102_0A24A485.lua",
        "line": 1, "column": 1, "source_original": "Khổn Thú", "original": "苦难兽",
    }], ensure_ascii=False), encoding="utf-8")

    report = export_xlsx_mapping(
        source, tmp_path / "export", "updatefs", "updatefs.pak", workers=1,
        records_path=records_path,
    )
    rows = read_simple_xlsx(Path(report["xlsx"]))
    assert report["prefilled_translations"] == 1
    assert rows[0]["text"] == "苦难兽"
    assert rows[0]["id"].startswith("x")


def test_legacy_workbook_is_remapped_to_new_ids_by_source(tmp_path):
    old_mapping = tmp_path / "old_mapping.json"
    old_mapping.write_text(json.dumps({
        "version": 5, "mode": "xlsx-dedup-cells-compact", "files": ["old.lua"],
        "rows": [["1", "Khổn Thú", [[0, 1, 1]]], ["2", "Thiên Công", [[0, 2, 1]]]],
    }, ensure_ascii=False), encoding="utf-8")
    old_xlsx = tmp_path / "old.xlsx"
    write_simple_xlsx(old_xlsx, [{"id": "1", "text": "苦难兽"}, {"id": "2", "text": "天宫"}], ["id", "text"])
    new_mapping = tmp_path / "new_mapping.json"
    new_mapping.write_text(json.dumps({
        "version": 5, "mode": "xlsx-dedup-cells-compact", "id_scheme": "source-sha256-24-v1",
        "files": ["new.lua"], "rows": [["x-new", "Khổn Thú", [[0, 9, 1]]], ["x-added", "Tân Thủ", [[0, 10, 1]]]],
    }, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "migrated.xlsx"

    report = remap_xlsx_by_source(old_xlsx, old_mapping, new_mapping, output)
    rows = {row["id"]: row["text"] for row in read_simple_xlsx(output)}
    assert report["matched"] == 1
    assert rows == {"x-new": "苦难兽", "x-added": "Tân Thủ"}
