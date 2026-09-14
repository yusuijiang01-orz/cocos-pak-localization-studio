import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from resource_structure_audit import audit_tree


def test_structure_audit_defaults_unknown_and_binary_to_blocked(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"Taqp hoa Thuong\x00\xff")
    (tmp_path / "a.xyz").write_text("Vietnamese-looking data", encoding="utf-8")
    report = audit_tree(tmp_path)
    assert report["suffix_summary"][".bin"]["policies"] == {"blocked": 1}
    assert report["suffix_summary"][".xyz"]["policies"] == {"blocked": 1}


def test_structure_audit_separates_plain_and_structured_txt(tmp_path):
    (tmp_path / "plain.txt").write_text("Xin chào người chơi\n", encoding="utf-8")
    (tmp_path / "mixed.txt").write_text("Name=Xin chào\n<color=red>Text", encoding="utf-8")
    report = audit_tree(tmp_path)
    assert report["plain_text_files"] == ["plain.txt"]
    mixed = {item["file"]: item for item in report["mixed_files"]}
    assert mixed["mixed.txt"]["structures"]["key_value_lines"] == 1
    assert mixed["mixed.txt"]["structures"]["tag_lines"] == 1
