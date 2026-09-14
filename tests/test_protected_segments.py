import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from protected_segments import split_rows, assemble
from localization_tm import protected_tokens, validate_tokens


def test_position_tag_is_fully_protected():
    source = '<npcpos=Tạp hóa Thương,12,1551,3019>'
    requests, layouts = split_rows([{'id': '1', 'text': source}])
    assert requests == []
    assert protected_tokens(source) == [source]
    assert not validate_tokens(source, '<npcpos=杂货铺,12,1551,3019>')[0]


def test_nonstandard_text_bearing_angle_span_is_also_immutable():
    source = '<Tên nhiệm vụ>Văn bản hiển thị'
    requests, layouts = split_rows([{'id': '1', 'text': source}])
    assert requests == [{'id': 's0', 'text': 'Văn bản hiển thị'}]
    assert protected_tokens(source)[0] == '<Tên nhiệm vụ>'
    assert not validate_tokens(source, '<任务名称>显示文本')[0]
    assert assemble(layouts, [{'id': 's0', 'text': '显示文本'}]) == [
        {'id': '1', 'text': '<Tên nhiệm vụ>显示文本'}]


def test_multiple_pos_tags_are_fully_protected_while_outer_text_translates():
    source = '<pos=Ngoài Sùng Thành,5,1796,2949>, <pos=Bắc Hải,6,1693,3159>'
    requests, layouts = split_rows([{'id': '1', 'text': source}])
    assert requests == []


def test_text_bearing_relative_resource_path_is_fully_protected():
    source = r'Ngoại hình: spr\item\equip\Đặc_biệt12.spr，点击查看'
    requests, layouts = split_rows([{'id': '1', 'text': source}], compact=True)
    assert len(requests) == 1
    assert r'spr\item\equip\Đặc_biệt12.spr' not in requests[0]['text']
    marker = next(iter(layouts['1']['markers']))
    result = assemble(layouts, [{'id': requests[0]['id'], 'text': f'装备外观：{marker}，点击查看'}])
    assert result == [{'id': '1', 'text': r'装备外观：spr\item\equip\Đặc_biệt12.spr，点击查看'}]


def test_chinese_named_resource_path_is_a_validation_token():
    from localization_tm import validate_tokens
    source = r'使用 \spr\item\equip\特殊12.spr'
    assert validate_tokens(source, source)[0]
    assert not validate_tokens(source, r'使用 \spr\item\equip\特别12.spr')[0]
from import_untranslated_xlsx import accept_translation
from export_untranslated_xlsx import has_natural_language_residual

def test_controls_never_enter_translation_and_restore_in_place():
    source = '-1=<sex>Xin chao<enter>%d'
    requests, layouts = split_rows([{'id': 'original', 'text': source}])
    assert requests == [{'id': 's0', 'text': 'Xin chao'}]
    output = assemble(layouts, [{'id': 's0', 'text': '你好'}])
    assert output == [{'id': 'original', 'text': '-1=<sex>你好<enter>%d'}]
    assert accept_translation(source, output[0]['text'])[0] == output[0]['text']

def test_missing_duplicate_or_reordered_results():
    requests, layouts = split_rows([{'id': 'x', 'text': 'Xin<enter>chao'}])
    assert assemble(layouts, [{'id':'s0', 'text':'你'}]) == []
    assert assemble(layouts, [{'id':'s0','text':'你'}, {'id':'s0','text':'错'}, {'id':'s1','text':'好'}]) == []
    assert assemble(layouts, [{'id':'s1','text':'好'}, {'id':'s0','text':'你'}])[0]['text'] == '你<enter>好'

def test_import_rejects_removed_real_tokens():
    assert accept_translation('Xin %d<sex>', '你好')[0] is None

def test_chinese_punctuation_and_hash_formats_are_not_sent_to_model():
    requests, layouts = split_rows([{'id': 'x', 'text': '生命：#d点，Xin chao。'}])
    assert requests == [{'id': 's0', 'text': '点，Xin chao。'}]
    assert assemble(layouts, [{'id': 's0', 'text': '点，你好。'}])[0]['text'] == '生命：#d点，你好。'

def test_exported_custom_hash_placeholder_stays_whole():
    requests, layouts = split_rows([{'id': 'x', 'text': 'Sinh lực: #d◈1◈+ điểm'}])
    assert requests == [{'id': 's0', 'text': 'Sinh lực:'}, {'id': 's1', 'text': 'điểm'}]
    assert assemble(layouts, [{'id':'s0','text':'生命：'}, {'id':'s1','text':'点'}])[0]['text'] == '生命： #d◈1◈+ 点'

def test_export_placeholder_is_restored_before_direct_acceptance():
    meta = {'tokens': [{'key':'P1','value':'1'}], 'template':'Sinh lực: #d{P1}+ điểm'}
    restored, reason = accept_translation('Sinh lực: #d1+ điểm', '生命： #d◈1◈+ 点', meta)
    assert reason == ''
    assert '#d1+' in restored
    assert '◈' not in restored

def test_exported_token_is_restored_even_when_marker_was_removed():
    meta = {'tokens': [{'key':'P1','value':'1%'}], 'template':'{TEXT}{P1}'}
    restored, reason = accept_translation('Kháng lôi +#d1%', '抗雷 +#d', meta)
    assert reason == ''
    assert restored == '抗雷 +#d1%'

def test_vietnamese_percent_is_not_a_printf_token():
    source = 'Tăng 30% phòng lôi và 5% xác suất'
    requests, layouts = split_rows([{'id': 'x', 'text': source}])
    assert requests == [{'id': 's0', 'text': source}]
    assert assemble(layouts, [{'id':'s0', 'text':'提高30%雷防和5%概率'}])[0]['text'] == '提高30%雷防和5%概率'

def test_custom_hash_placeholder_keeps_percent_suffix():
    requests, layouts = split_rows([{'id': 'x', 'text': '#d◈1◈-% sát thương'}])
    assert requests == [{'id': 's0', 'text': 'sát thương'}]
    assert assemble(layouts, [{'id':'s0', 'text':'伤害'}])[0]['text'] == '#d◈1◈-% 伤害'

def test_short_partial_vietnamese_is_detected_but_game_acronyms_are_allowed():
    assert has_natural_language_residual('$Ho完成任务。')
    assert not has_natural_language_residual('获得100点HP，寻找NPC')
    assert not has_natural_language_residual('法宝 x2，强化至 Lv10，封神 Mobile')
    assert not has_natural_language_residual('赠予玩家 zBINzzHIEPz')
    assert has_natural_language_residual('领取 nhiệm vụ 奖励')

def test_common_completion_phrase_is_translated_locally_with_prefix():
    requests, layouts = split_rows([{'id': 'x', 'text': '$Hoàn thành nhiệm vụ.'}])
    assert requests == []
    assert assemble(layouts, []) == [{'id': 'x', 'text': '$任务完成。'}]

def test_common_dollar_prefixed_ui_phrases_do_not_keep_vietnamese_stems():
    rows = [
        {'id': 'a', 'text': '$Thỉnh giáo'},
        {'id': 'b', 'text': '$Kháng tất cả +10%'},
        {'id': 'c', 'text': '$Phòng ngự tăng 140 điểm'},
    ]
    requests, layouts = split_rows(rows)
    assert requests == []
    assert assemble(layouts, []) == [
        {'id': 'a', 'text': '$请教'},
        {'id': 'b', 'text': '$所有抗性 +10%'},
        {'id': 'c', 'text': '$防御增加140点'},
    ]

def test_partial_vietnamese_stems_are_removed_from_model_spans():
    rows = [
        {'id': 'a', 'text': '$Kênh hệ thống'},
        {'id': 'b', 'text': '$Phụ cận'},
        {'id': 'c', 'text': '$Hoàn thành nhiệm vụ.'},
    ]
    requests, layouts = split_rows(rows)
    # The completion phrase is handled locally; the other two go to the model.
    assert [r['text'] for r in requests] == ['Kênh hệ thống', 'Phụ cận']
    assert assemble(layouts, [
        {'id': 's0', 'text': 'K系统频道'},
        {'id': 's1', 'text': 'Ph附近'},
    ]) == [
        {'id': 'a', 'text': '$系统频道'},
        {'id': 'b', 'text': '$附近'},
        {'id': 'c', 'text': '$任务完成。'},
    ]

def test_dollar_placeholder_is_restored_during_import():
    from tsv_localization import token_template
    meta = token_template('$Kênh hệ thống')
    restored, reason = accept_translation('$Kênh hệ thống', '◈1◈系统频道', meta)
    assert reason == ''
    assert restored == '$系统频道'

def test_compact_mode_keeps_one_request_per_source_row():
    source = r'Nhận #d vật phẩm từ \Spr\Ui4\item\01.spr, cấp ◈1◈.'
    requests, layouts = split_rows([{'id': 'x', 'text': source}], compact=True)
    assert len(requests) == 1
    masked = requests[0]['text']
    assert r'\Spr\Ui4\item\01.spr' not in masked
    translated = (masked.replace('Nhận ', '获得')
                        .replace(' vật phẩm từ ', '件物品，来自')
                        .replace(' cấp ', '等级')
                        .replace('.', '。'))
    assert assemble(layouts, [{'id': 's0', 'text': translated}]) == [
        {'id': 'x', 'text': r'获得#d件物品，来自\Spr\Ui4\item\01.spr,等级◈1◈。'}
    ]

def test_compact_mode_rejects_a_damaged_protected_marker():
    requests, layouts = split_rows([{'id': 'x', 'text': 'Xin <enter> chao'}], compact=True)
    assert len(requests) == 1
    assert assemble(layouts, [{'id': 's0', 'text': '你好'}]) == []

def test_bare_resource_extension_is_not_translated():
    requests, layouts = split_rows([{'id': 'x', 'text': 'spr'}], compact=True)
    assert requests == []
    assert assemble(layouts, []) == [{'id': 'x', 'text': 'spr'}]
