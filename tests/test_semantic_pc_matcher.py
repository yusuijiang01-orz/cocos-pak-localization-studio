import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from semantic_pc_matcher import PcSemanticIndex, normalize_semantic_text
from collections import defaultdict
from pc_content_migrator import add_candidate, clean_existing_chinese, migrate


def test_semantic_normalization_removes_runtime_markup():
    assert normalize_semantic_text("#你获得<color=green>20000<color>经验和◈1◈钱") == "你获得20000经验和钱"


def test_semantic_index_accepts_unique_near_original():
    index = PcSemanticIndex([
        {"original": "#礼官：年轻人，这是给你的<color=green>夺宫刀<color>，请收好！"},
        {"original": "#前方瘴气弥漫，无法通行。你可在狱法山内寻找通行方法。"},
    ])
    match = index.match("礼官，年轻人，这是给你的夺宫刀，请收好")
    assert match is not None
    assert "夺宫刀" in match[0]


def test_semantic_index_rejects_short_or_ambiguous_generic_text():
    index = PcSemanticIndex([
        {"original": "#你获得任务奖励"},
        {"original": "#你获得活动奖励"},
    ])
    assert index.match("获得奖励") is None


def test_semantic_body_can_match_when_mobile_keeps_color_wrapper():
    index = PcSemanticIndex([{"original": "赤铜刀"}, {"original": "#赤铜刀"}])
    match = index.match("<c=yellow>赤铜刀<c>", "<c=yellow>Xích Đồng Đao<c>")
    assert match is not None
    assert normalize_semantic_text(match[0]) == "赤铜刀"


def test_trusted_primary_key_allows_natural_language_number_wording():
    candidates = defaultdict(lambda: defaultdict(set))
    mobile = {"id": "13403", "original": "Nhận 1 vũ khí cấp 60 <color=yellow>vũ khí<color>",
              "language": "vi", "status": "未翻译", "note": ""}
    pc = {"original": "#你领取了一把60级的<color=yellow>武器<color>！"}
    add_candidate(candidates, mobile, pc, "tsv_primary_key")
    assert candidates["13403"]["tsv_primary_key"] == {pc["original"]}


def test_clean_existing_chinese_is_never_offered_for_pc_replacement():
    candidates = defaultdict(lambda: defaultdict(set))
    mobile = {
        "id": "302", "original": "创建角色", "source_original": "创建角色",
        "language": "zh", "status": "未翻译", "note": "",
    }
    pc = {"original": "#天仙水(各取所需)"}
    assert clean_existing_chinese(mobile)
    add_candidate(candidates, mobile, pc, "cross_file_primary_key")
    assert candidates["302"]["cross_file_primary_key"] == set()


def test_cross_file_content_anchors_respect_pak_scope(tmp_path):
    pc_root = tmp_path / "pc"
    mobile_root = tmp_path / "mobile"
    output = tmp_path / "output"
    pc_file = pc_root / "extracted" / "serverlist" / "1000_AAAAAAAA.tsv"
    mobile_file = mobile_root / "extracted" / "updatefs" / "2000_BBBBBBBB.tsv"
    pc_file.parent.mkdir(parents=True)
    mobile_file.parent.mkdir(parents=True)
    pc_file.write_text("SID\tSTRING\n1\t哪吒\n2\t乾坤圈\n\t领取任务奖励\n3\t击败城外的妖怪\n", encoding="utf-8")
    mobile_file.write_text("SID\tSTRING\n1\t哪吒\n2\t乾坤圈\n\tNhận phần thưởng nhiệm vụ\n3\tĐánh bại yêu quái ngoài thành\n", encoding="utf-8")

    def record(record_id, pak, source_file, hash_id, line, text, language):
        return {
            "id": record_id, "pak": pak, "source_file": source_file,
            "hash": hash_id, "line": line, "column": 2,
            "original": text, "source_original": text,
            "language": language, "status": "未翻译", "note": "",
        }

    pc_records = [
        record("p1", "serverlist.pak", pc_file.name, "AAAAAAAA", 2, "哪吒", "zh"),
        record("p2", "serverlist.pak", pc_file.name, "AAAAAAAA", 3, "乾坤圈", "zh"),
        record("p4", "serverlist.pak", pc_file.name, "AAAAAAAA", 4, "领取任务奖励", "zh"),
        record("p3", "serverlist.pak", pc_file.name, "AAAAAAAA", 5, "击败城外的妖怪", "zh"),
    ]
    mobile_records = [
        record("m1", "updatefs.pak", mobile_file.name, "BBBBBBBB", 2, "哪吒", "zh"),
        record("m2", "updatefs.pak", mobile_file.name, "BBBBBBBB", 3, "乾坤圈", "zh"),
        record("m4", "updatefs.pak", mobile_file.name, "BBBBBBBB", 4, "Nhận phần thưởng nhiệm vụ", "vi"),
        record("m3", "updatefs.pak", mobile_file.name, "BBBBBBBB", 5, "Đánh bại yêu quái ngoài thành", "vi"),
        record("other", "settings.pak", "other.tsv", "CCCCCCCC", 2, "Nhiệm vụ", "vi"),
    ]
    for root, records in ((pc_root, pc_records), (mobile_root, mobile_records)):
        localization = root / "localization"
        localization.mkdir(parents=True)
        (localization / "text_records.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8"
        )

    report = migrate(pc_root, mobile_root, output, pak_scope="updatefs.pak")
    migrated = json.loads(Path(report["records_output"]).read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in migrated}
    assert report["cross_file_relations"] == 1
    assert report["paks"] == {"updatefs.pak": 2}
    assert by_id["m3"]["original"] == "击败城外的妖怪"
    assert by_id["m4"]["original"] == "领取任务奖励"
    assert "context_sequence" in by_id["m4"]["note"]
    assert by_id["other"]["original"] == "Nhiệm vụ"
