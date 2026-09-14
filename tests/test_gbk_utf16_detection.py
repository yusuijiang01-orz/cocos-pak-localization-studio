import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from localization_analyzer import decode_best, normalize_text_resource_utf8
from pc_content_migrator import add_candidate


def test_short_gbk_chinese_is_not_misdetected_as_utf16():
    raw = "微笑".encode("gbk")
    text, encoding, language, _score = decode_best(raw)
    assert text == "微笑"
    assert encoding == "gb18030"
    assert language == "zh"


def test_gbk_ini_normalizes_to_clean_utf8():
    raw = ";flying创建\r\n[Face1]\r\nTip=$微笑\r\nSpr=\\Spr\\Ui4\\表情\\01.spr\r\n".encode("gbk")
    normalized = normalize_text_resource_utf8(raw, ".ini").decode("utf-8")
    assert ";flying创建" in normalized
    assert "Tip=$微笑" in normalized
    assert "\\表情\\01.spr" in normalized
    assert "ΤЦ" not in normalized


def test_pc_migration_keeps_international_literal_marker():
    candidates = {}
    from collections import defaultdict
    candidates = defaultdict(lambda: defaultdict(set))
    mobile = {
        "id": "face11", "original": "Lạnh", "source_original": "Lạnh",
        "language": "vi", "status": "未翻译", "note": "",
    }
    add_candidate(candidates, mobile, {"original": "$寒……"}, "ini_section_key")
    assert candidates["face11"]["ini_section_key"] == {"$寒……"}
