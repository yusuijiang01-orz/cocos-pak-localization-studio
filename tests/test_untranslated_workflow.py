from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from export_untranslated_xlsx import looks_like_code_identifier, run as export_untranslated, should_include, write_xlsx
from import_untranslated_xlsx import read_simple_xlsx, run as import_untranslated
import ollama_batch_translate
import export_untranslated_xlsx
from xml.etree import ElementTree as ET
import zipfile


def _record(rid: str, text: str) -> dict:
    return {
        "id": rid,
        "pak": "ui.pak",
        "source_file": "0001_TEST.tsv",
        "line": int(rid),
        "column": 1,
        "source_original": text,
        "original": text,
        "language": "vi",
        "status": "未翻译",
    }


def test_untranslated_export_dedupes_after_placeholder_mapping_and_restores(tmp_path):
    localization = tmp_path / "localization"
    localization.mkdir()
    records = [_record("1", "Nhận 10 vàng"), _record("2", "Nhận 20 vàng")]
    records_path = localization / "text_records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    report = export_untranslated(tmp_path, "ui.pak")
    assert report["total_records"] == 2
    assert report["total_unique"] == 1

    xlsx = Path(report["exports"][0]["xlsx"])
    rows = read_simple_xlsx(xlsx)
    assert rows == [{"id": "1", "text": "Nhận ◈1◈ vàng"}]
    write_xlsx(xlsx, [{"id": "1", "text": "获得◈1◈金币"}])

    imported = import_untranslated(tmp_path, "ui.pak")
    assert imported["updated"] == 2
    updated = json.loads(records_path.read_text(encoding="utf-8"))
    assert [r["original"] for r in updated] == ["获得10金币", "获得20金币"]


def test_untranslated_import_skips_source_text(tmp_path):
    localization = tmp_path / "localization"
    localization.mkdir()
    records_path = localization / "text_records.json"
    records_path.write_text(json.dumps([_record("1", "Nhận 10 vàng")], ensure_ascii=False), encoding="utf-8")
    export_untranslated(tmp_path, "ui.pak")

    imported = import_untranslated(tmp_path, "ui.pak")
    assert imported["updated"] == 0
    assert imported["unchanged_rows_skipped"] == 1
    unchanged = json.loads(records_path.read_text(encoding="utf-8"))[0]
    assert unchanged["status"] == "未翻译"
    assert unchanged["language"] == "vi"


def test_code_identifiers_are_not_exported_for_translation():
    for text in ("ImageName", "ImageDropName", "[ManaMedicine]", "Map ID", "MapName", "◈1◈"):
        assert looks_like_code_identifier(text)
        assert not should_include(_record("1", text))
    assert should_include(_record("1", "Chơi ngay"))
    assert should_include(_record("1", "Nhiệm vụ hàng ngày"))


def test_current_vietnamese_text_is_exported_even_when_migration_status_is_stale():
    record = _record("1", "old source")
    record.update({
        "source_original": "旧的历史原文",
        "original": "Chào mừng bạn",
        "status": "已迁移",
        "language": "vi",
    })
    assert should_include(record)


def test_ollama_progress_contains_restored_live_record_updates(tmp_path, monkeypatch):
    localization = tmp_path / "localization"
    localization.mkdir()
    records = [_record("1", "Nhận 10 vàng"), _record("2", "Nhận 20 vàng")]
    (localization / "text_records.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    export_untranslated(tmp_path, "ui.pak")

    monkeypatch.setattr(ollama_batch_translate, "load_translator_profile", lambda: {
        "name": "Ollama", "baseUrl": "http://localhost:11435", "model": "qwen3:14b", "prompt": "translate"
    })
    response = '[{"id":"s0","text":"获得◈90000000◈金币"}]'
    def fake_stream_chat(*args, **kwargs):
        callback = kwargs.get("on_delta")
        if callback:
            cut = response.index("}") + 1
            callback(response[:cut], response[:cut])
            callback(response[cut:], response)
        return response
    monkeypatch.setattr(ollama_batch_translate, "ollama_chat", fake_stream_chat)
    events = []
    report = ollama_batch_translate.run(tmp_path, "ui.pak", resume=False, progress=events.append)

    assert report["translated_rows"] == 1
    assert any("第 1/1 桶" in event.get("message", "") for event in events)
    assert any("实时显示 1/1 条" in event.get("message", "") for event in events)
    updates = [u for event in events for u in event.get("updates", [])]
    assert {u["id"]: u["text"] for u in updates} == {"1": "获得10金币", "2": "获得20金币"}


def test_leaked_transport_markers_are_exported_again_from_clean_source():
    record = _record("1", "Thăng cấp vũ khí 120 lần thứ 4")
    record["source_original"] = "Thăng cấp vũ khí 120 lần thứ 4"
    record["original"] = "提升武器《1》阶《2》次"
    record["language"] = "zh"
    record["status"] = "已翻译"
    assert export_untranslated_xlsx.should_include(record)


def test_untranslated_xlsx_removes_xml_forbidden_control_characters(tmp_path):
    path = tmp_path / "control.xlsx"
    write_xlsx(path, [{"id": "1", "text": "坏\x13\x14文本"}])
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml")
    ET.fromstring(xml)
    assert b"\x13" not in xml and b"\x14" not in xml
