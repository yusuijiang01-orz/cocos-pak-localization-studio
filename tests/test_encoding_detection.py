import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from localization_analyzer import decode_best, is_resource_reference_bytes, _analyze_one_file

def test_short_utf8_vietnamese_class_name_is_detected():
    text,enc,lang,_score=decode_best('Đạo sĩ'.encode('utf-8'))
    assert text=='Đạo sĩ'
    assert enc=='utf-8'
    assert lang=='vi'

def test_visible_npc_name_column_keeps_short_names(tmp_path):
    path=tmp_path/'npc.tsv'
    path.write_text('Idnpc\tnpc_name\n1\tSứ Giả\n2\tVõ sư\n',encoding='utf-8')
    records,_stats=_analyze_one_file((str(path),'updatefs.pak'))
    assert [(r['line'],r['column'],r['original']) for r in records] == [
        (2,2,'Sứ Giả'),
        (3,2,'Võ sư'),
    ]

def test_utf16le_chinese_wins_over_legacy_codecs():
    text,enc,lang,score=decode_best('任务完成'.encode('utf-16-le'))
    assert text=='任务完成'
    assert enc=='utf-16-le'
    assert lang=='zh'

def test_utf16be_chinese_wins_over_legacy_codecs():
    text,enc,lang,score=decode_best('领取奖励'.encode('utf-16-be'))
    assert text=='领取奖励'
    assert enc=='utf-16-be'
    assert lang=='zh'

def test_plain_ascii_is_not_misdetected_as_utf16():
    text,enc,lang,score=decode_best(b'function GetSkillLevelData(levelname, data, level)')
    assert text.startswith('function GetSkillLevelData')
    assert not enc.startswith('utf-16')

def test_gbk_resource_path_is_filtered_before_utf16_guess():
    raw=b'\\spr\\item\\ibitem\\' + '玉佩\\青灵玉佩.spr'.encode('gb18030')
    assert is_resource_reference_bytes(raw)

def test_utf8_vietnamese_wins_over_mixed_mojibake():
    raw='Hoàng Ngọc x5|Lục Tùng Thạch x5|Vũ Khí Sơ Cấp x3'.encode('utf-8')
    text,enc,lang,score=decode_best(raw)
    assert text.startswith('Hoàng Ngọc')
    assert enc=='utf-8'
    assert lang=='vi'

def test_utf8_chinese_is_not_rescored_as_gb18030_or_mixed_legacy():
    for expected in ('十大高手','等级','狗纹盔甲','冰雪之力','它拥有北方冰雪的力量。'):
        text,enc,lang,_score=decode_best(expected.encode('utf-8'))
        assert text==expected
        assert enc=='utf-8'
        assert lang=='zh'

if __name__=='__main__':
    test_utf16le_chinese_wins_over_legacy_codecs()
    test_utf16be_chinese_wins_over_legacy_codecs()
    test_plain_ascii_is_not_misdetected_as_utf16()
    test_gbk_resource_path_is_filtered_before_utf16_guess()
    test_utf8_vietnamese_wins_over_mixed_mojibake()
    print('OK')
