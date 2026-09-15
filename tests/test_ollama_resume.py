import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import ollama_batch_translate as obt


@pytest.mark.parametrize(('model', 'requested', 'expected_calls'), [
    ('gemma4:latest', 50, [40]),
    ('qwen3:14b', 50, [25, 15]),
])
def test_model_specific_bucket_cap_is_actually_used(tmp_path, monkeypatch, model, requested, expected_calls):
    folder = tmp_path / 'untranslated_xlsx' / 'settings'
    folder.mkdir(parents=True)
    rows = [{'id': str(i), 'text': f'Xin {i}'} for i in range(40)]
    obt.overwrite_xlsx(folder / 'settings_localization.xlsx', rows)
    (folder / 'settings_records_mapping.json').write_text(
        json.dumps({str(i): [] for i in range(40)}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {
        'model': model, 'batchSize': requested,
    })
    calls = []

    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append(len(batch))
        if 'gemma' in model:
            assert kwargs['positional_count'] == len(batch)
            return json.dumps(['中文' for _row in batch], ensure_ascii=False)
        return json.dumps([{'id': row['id'], 'text': '中文'} for row in batch], ensure_ascii=False)

    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    report = obt.run(tmp_path, 'settings.pak', bucket_size=requested, resume=False)
    assert calls == expected_calls
    assert report['remaining_rows'] == 0


def test_dynamic_buckets_limit_rows_and_total_text_size():
    rows = [
        {'id': '1', 'text': 'a' * 20},
        {'id': '2', 'text': 'b' * 20},
        {'id': '3', 'text': 'c' * 300},
        {'id': '4', 'text': 'd' * 300},
        {'id': '5', 'text': 'e' * 300},
    ]
    groups = obt.make_dynamic_buckets(rows, row_limit=20, char_limit=400)
    assert [len(group) for group in groups] == [2, 1, 1, 1]
    assert [row['id'] for group in groups for row in group] == ['1', '2', '3', '4', '5']


def test_parser_repairs_one_extra_quote_escape_layer():
    reply = r'[{"id":"s0","text":\"任务完成\"}]'
    assert obt.parse_json_array_block(reply) == [{'id': 's0', 'text': '任务完成'}]


def test_parser_accepts_single_object_response():
    reply = '{"id":"s0","text":"任务完成"}'
    assert obt.parse_json_array_block(reply) == [{'id': 's0', 'text': '任务完成'}]


def test_positional_parser_restores_ids_by_order_and_rejects_wrong_count():
    rows = [{'id': 's0'}, {'id': 's1'}]
    assert obt.parse_positional_array('["任务完成","领取奖励"]', rows) == [
        {'id': 's0', 'text': '任务完成'}, {'id': 's1', 'text': '领取奖励'},
    ]
    with pytest.raises(ValueError, match='数量不匹配'):
        obt.parse_positional_array('["任务完成"]', rows)


def test_translategemma_prompt_and_parser_use_strict_safe_boundaries():
    rows = [{'id': 's0', 'text': '<x90000000/>Hoàn thành nhiệm vụ.'},
            {'id': 's1', 'text': 'Nhận thưởng'}]
    messages = obt.build_translategemma_prompt(rows)
    assert len(messages) == 1 and messages[0]['role'] == 'user'
    assert 'ZXQROW000000ZX' in messages[0]['content']
    assert '<x90000000/>' in messages[0]['content']
    reply = ('ZXQROW000000ZX <x90000000/>任务完成。 '
             'ZXQROW000001ZX 领取奖励 ZXQROW000002ZX')
    assert obt.parse_translategemma_rows(reply, rows) == [
        {'id': 's0', 'text': '<x90000000/>任务完成。'},
        {'id': 's1', 'text': '领取奖励'},
    ]
    with pytest.raises(ValueError, match='行边界'):
        obt.parse_translategemma_rows(reply.replace('ZXQROW000001ZX', ''), rows)


def test_resume_repacks_holes_and_survives_workbook_overwrite(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'updatefs'
    folder.mkdir(parents=True)
    workbook = folder / 'updatefs_localization.xlsx'
    rows = [{'id': str(i), 'text': 'Xin chao'} for i in range(6)]
    obt.overwrite_xlsx(workbook, rows)
    # Fingerprint exactly the parsed workbook, as production does.
    parsed = obt.iux.read_simple_xlsx(workbook)
    fingerprint = hashlib.sha256(json.dumps(parsed, ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    (folder / 'updatefs_records_mapping.json').write_text(
        json.dumps({str(i): [] for i in range(6)}), encoding='utf-8')
    (folder / 'ollama_checkpoint.json').write_text(json.dumps({
        'input_fingerprint': fingerprint, 'bucket_size': 2,
        'done_buckets': [0, 1, 2],
        'translations': {'0': '你好', '2': '你好', '4': '你好'},
    }), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    calls = []

    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append([r['id'] for r in batch])
        return json.dumps([{'id': r['id'], 'text': '你好'} for r in batch])

    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    report = obt.run(tmp_path, 'updatefs.pak', bucket_size=3, max_buckets=1)
    assert calls == [['s0', 's1', 's2']]
    saved = json.loads((folder / 'ollama_checkpoint.json').read_text(encoding='utf-8'))
    assert set(saved['translations']) == set('012345')
    assert report['remaining_rows'] == 0
    calls.clear()
    report = obt.run(tmp_path, 'updatefs.pak', bucket_size=1)
    assert calls == []
    assert report['total_buckets'] == 0
    assert report['translated_rows'] == 6


def test_new_export_replaces_stale_backup_for_next_resume(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'updatefs'
    folder.mkdir(parents=True)
    workbook = folder / 'updatefs_localization.xlsx'
    obt.overwrite_xlsx(workbook, [{'id': '1', 'text': 'Moi'}])
    obt.overwrite_xlsx(Path(str(workbook) + '.bak'), [{'id': 'old', 'text': 'Cu'}])
    (folder / 'updatefs_records_mapping.json').write_text(json.dumps({'1': []}), encoding='utf-8')
    (folder / 'ollama_checkpoint.json').write_text(json.dumps({
        'input_fingerprint': 'stale', 'translations': {'old': '旧'}
    }), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    calls = []
    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append(batch)
        return json.dumps([{'id': r['id'], 'text': '新'} for r in batch])
    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    obt.run(tmp_path, 'updatefs.pak')
    assert obt.iux.read_simple_xlsx(Path(str(workbook) + '.bak')) == [{'id': '1', 'text': 'Moi'}]
    calls.clear()
    report = obt.run(tmp_path, 'updatefs.pak')
    assert calls == []
    assert report['remaining_rows'] == 0


def test_duplicate_workbook_ids_are_rejected(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'updatefs'
    folder.mkdir(parents=True)
    obt.overwrite_xlsx(folder / 'updatefs_localization.xlsx', [
        {'id': '1', 'text': 'Mot'}, {'id': '1', 'text': 'Hai'}])
    (folder / 'updatefs_records_mapping.json').write_text(json.dumps({'1': []}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    try:
        obt.run(tmp_path, 'updatefs.pak')
        assert False, 'duplicate IDs should fail'
    except ValueError as exc:
        assert '重复 ID' in str(exc)


def test_empty_main_reply_automatically_retries_in_small_batch(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'settings'
    folder.mkdir(parents=True)
    obt.overwrite_xlsx(folder / 'settings_localization.xlsx', [
        {'id': '1', 'text': 'Xin chao'}, {'id': '2', 'text': 'Nhiem vu'}])
    (folder / 'settings_records_mapping.json').write_text(
        json.dumps({'1': [], '2': []}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    calls = []
    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append(batch)
        if len(calls) == 1:
            return '[]'
        return json.dumps([{'id': r['id'], 'text': '中文'} for r in batch])
    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    report = obt.run(tmp_path, 'settings.pak', bucket_size=20)
    assert len(calls) == 2
    assert len(calls[1]) == 2
    assert report['translated_rows'] == 2
    assert report['remaining_rows'] == 0


def test_dropped_compact_markers_fall_back_to_safe_single_row_segments(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'settings'
    folder.mkdir(parents=True)
    source = '#Chào <c=g>Thiên Ngô<c>!'
    obt.overwrite_xlsx(folder / 'settings_localization.xlsx', [{'id': '1', 'text': source}])
    (folder / 'settings_records_mapping.json').write_text(json.dumps({'1': []}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test', 'prompt': 'translate'})
    calls = []
    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append(batch)
        if len(calls) == 1:
            return '[{"id":"s0","text":"#你好，天吴！"}]'
        return json.dumps([{'id': row['id'], 'text': '你好' if row['id'] == 's0' else '天吴'} for row in batch], ensure_ascii=False)
    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    report = obt.run(tmp_path, 'settings.pak', bucket_size=20, resume=False)
    assert len(calls) == 2
    assert report['translated_rows'] == 1
    assert obt.iux.read_simple_xlsx(folder / 'settings_localization.xlsx') == [
        {'id': '1', 'text': '#你好 <c=g>天吴<c>!'}
    ]


def test_rejected_rows_are_regrouped_instead_of_retried_one_by_one(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'settings'
    folder.mkdir(parents=True)
    rows = [{'id': str(i), 'text': f'Xin chao {i}'} for i in range(10)]
    obt.overwrite_xlsx(folder / 'settings_localization.xlsx', rows)
    (folder / 'settings_records_mapping.json').write_text(
        json.dumps({str(i): [] for i in range(10)}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    calls = []

    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append([r['id'] for r in batch])
        if len(calls) == 1:
            return '[]'
        return json.dumps([{'id': r['id'], 'text': '你好'} for r in batch])

    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    report = obt.run(tmp_path, 'settings.pak', bucket_size=20, resume=False)
    assert [len(call) for call in calls] == [10, 5, 5]
    assert report['translated_rows'] == 10
    assert report['remaining_rows'] == 0


def test_truncated_json_is_not_retried_as_a_network_error(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'settings'
    folder.mkdir(parents=True)
    rows = [{'id': str(i), 'text': f'Xin chao {i}'} for i in range(6)]
    obt.overwrite_xlsx(folder / 'settings_localization.xlsx', rows)
    (folder / 'settings_records_mapping.json').write_text(
        json.dumps({str(i): [] for i in range(6)}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    calls = []

    def fake_chat(messages, **kwargs):
        batch = json.loads(messages[1]['content'].split('输入：\n')[1].split('\n\n')[0])
        calls.append([r['id'] for r in batch])
        if len(batch) > 5:
            return '[{"id":"s0","text":"未闭合'
        return json.dumps([{'id': r['id'], 'text': '你好'} for r in batch])

    monkeypatch.setattr(obt, 'ollama_chat', fake_chat)
    report = obt.run(tmp_path, 'settings.pak', bucket_size=20, resume=False)
    assert [len(call) for call in calls] == [6, 5, 1]
    assert report['translated_rows'] == 6
    assert report['remaining_rows'] == 0


def test_stop_request_saves_partial_result_without_calling_next_bucket(tmp_path, monkeypatch):
    folder = tmp_path / 'untranslated_xlsx' / 'settings'
    folder.mkdir(parents=True)
    workbook = folder / 'settings_localization.xlsx'
    obt.overwrite_xlsx(workbook, [
        {'id': '1', 'text': 'Xin chao'}, {'id': '2', 'text': 'Nhiem vu'}])
    (folder / 'settings_records_mapping.json').write_text(
        json.dumps({'1': [], '2': []}), encoding='utf-8')
    control = folder / 'ollama_control.json'
    control.write_text(json.dumps({'stop': True, 'think': False}), encoding='utf-8')
    monkeypatch.setattr(obt, 'load_translator_profile', lambda: {'model': 'test'})
    calls = []
    monkeypatch.setattr(obt, 'ollama_chat', lambda *args, **kwargs: calls.append(1))

    report = obt.run(
        tmp_path, 'settings.pak', bucket_size=1, resume=False,
        control_path=str(control))

    assert calls == []
    assert report['stopped'] is True
    assert report['translated_rows'] == 0
    assert report['remaining_rows'] == 2
    checkpoint = json.loads((folder / 'ollama_checkpoint.json').read_text(encoding='utf-8'))
    assert checkpoint['phase'] == 'stopped'
    assert obt.iux.read_simple_xlsx(workbook) == [
        {'id': '1', 'text': 'Xin chao'}, {'id': '2', 'text': 'Nhiem vu'}]
