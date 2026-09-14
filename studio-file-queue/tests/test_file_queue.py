import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import ollama_batch_translate
from localization_analyzer import analyze_folder
from ollama_batch_translate import make_file_ordered_buckets
from xlsx_localization import (
    apply_multi_pak_full_xlsx_to_records,
    apply_xlsx_folder_to_records,
    apply_xlsx_to_records,
    export_full_xlsx,
    export_multi_pak_full_xlsx,
    export_xlsx_file_queue,
    read_simple_xlsx,
    write_simple_xlsx,
)


def test_multi_pak_full_xlsx_keeps_runtime_tokens_out_of_google_cells(tmp_path):
    records_path = tmp_path / 'records.json'
    records = [
        {'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
         '_isPlayerVisible': True, 'source_original': 'Nhận $player\n<c=red>%02d</c> tại 12:30 \\spr\\ui\\x.spr',
         'original': 'Nhận $player\n<c=red>%02d</c> tại 12:30 \\spr\\ui\\x.spr'},
        {'id': 'b', 'pak': 'settings.pak', 'source_file': '2.tsv', 'line': 3, 'column': 2,
         '_isPlayerVisible': True, 'source_original': 'Cấp {level}: 100%', 'original': 'Cấp {level}: 100%'},
        {'id': 'c', 'pak': 'settings.pak', 'source_file': '2.tsv', 'line': 4, 'column': 2,
         '_isPlayerVisible': True, 'source_original': '#<Chiến>Kháng Long/Hộ Giáp',
         'original': '#<Chiến>Kháng Long/Hộ Giáp'},
    ]
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(records_path, tmp_path / 'xlsx', 'all', tmp_path / 'config', ['ui.pak', 'settings.pak'])
    rows = read_simple_xlsx(Path(report['xlsx']))
    cells = [row['text'] for row in rows]
    for forbidden in ('$player', '\n', '<c=red>', '</c>', '<Chiến>', '#', '/', '%02d', '12:30', r'\spr\ui\x.spr', '{level}', '100', '◈'):
        assert all(forbidden not in cell for cell in cells)
    assert 'Chiến' in cells
    mapping = json.loads(Path(report['mapping']).read_text(encoding='utf-8'))
    assert mapping['version'] == 7
    assert mapping['paks'] == ['ui.pak', 'settings.pak']
    assert mapping['placeholder_strategy'] == 'out-of-band-exact-skeleton-v1'


def test_multi_pak_full_xlsx_does_not_prefill_injected_runtime_literals(tmp_path):
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([{
        'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
        '_isPlayerVisible': True, 'source_original': 'Trang bị',
        # A damaged historical translation must never leak back into a fresh
        # Google-facing workbook.
        'original': 'AAAAAAA',
    }], ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(
        records_path, tmp_path / 'xlsx', 'all', tmp_path / 'config', ['ui.pak']
    )
    rows = read_simple_xlsx(Path(report['xlsx']))
    assert len(rows) == 1
    assert {key: rows[0][key] for key in ('pak', 'source_file', 'text')} == {
        'pak': 'ui.pak', 'source_file': '1.ini', 'text': 'Trang bị',
    }


def test_multi_pak_import_restores_exact_tokens_and_fills_missing_rows(tmp_path):
    records_path = tmp_path / 'records.json'
    records = [
        {'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
         '_isPlayerVisible': True, 'source_original': 'Nhận $player <c=red>%02d</c>', 'original': 'Nhận $player <c=red>%02d</c>'},
        {'id': 'b', 'pak': 'settings.pak', 'source_file': '2.tsv', 'line': 3, 'column': 2,
         '_isPlayerVisible': True, 'source_original': 'Cấp {level}: 100%', 'original': 'Cấp {level}: 100%'},
    ]
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(records_path, tmp_path / 'xlsx', 'all', tmp_path / 'config', ['ui.pak', 'settings.pak'])
    rows = read_simple_xlsx(Path(report['xlsx']))
    # Translate one natural segment; remove the other row to simulate a sparse
    # residual workbook. Import must preserve the omitted source segment.
    translated = [{**rows[0], 'text': '领取 '}]
    write_simple_xlsx(Path(report['xlsx']), translated, headers=['id', 'pak', 'source_file', 'text'])
    imported = apply_multi_pak_full_xlsx_to_records(records_path, Path(report['xlsx']), Path(report['mapping']))
    final = {row['id']: row['original'] for row in json.loads(records_path.read_text(encoding='utf-8'))}
    assert '$player' in final['a'] and '<c=red>' in final['a'] and '%02d' in final['a'] and '</c>' in final['a']
    assert '{level}' in final['b'] and '100%' in final['b']
    assert '◈' not in final['a'] + final['b']
    assert imported['missing_segments'] == 0
    assert imported['remaining_vietnamese_records'] >= 1
    assert imported['untranslated_allowed'] is True


def test_multi_pak_import_rejects_chinese_text_with_runtime_punctuation(tmp_path):
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([{
        'id': 'a', 'pak': 'settings.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
        '_isPlayerVisible': True, 'source_original': '$Hệ phái:#s1-',
        'original': '$Hệ phái:#s1-',
    }], ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(
        records_path, tmp_path / 'xlsx', 'all', tmp_path / 'config', ['settings.pak']
    )
    rows = read_simple_xlsx(Path(report['xlsx']))
    assert [row['text'] for row in rows] == ['Hệ phái:']
    rows[0]['text'] = '学校/派系：'
    write_simple_xlsx(Path(report['xlsx']), rows, headers=['id', 'pak', 'source_file', 'text'])
    imported = apply_multi_pak_full_xlsx_to_records(records_path, Path(report['xlsx']), Path(report['mapping']))
    final = json.loads(records_path.read_text(encoding='utf-8'))[0]['original']
    assert final == '$Hệ phái:#s1-'
    assert imported['rejected_segments'] == 1


def test_remaining_workbook_is_sparse_and_preserves_existing_chinese(tmp_path):
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([
        {'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
         '_isPlayerVisible': True, 'source_original': 'Trang bị', 'original': '装备'},
        {'id': 'b', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 3, 'column': 1,
         '_isPlayerVisible': True, 'source_original': 'Nhiệm vụ', 'original': 'Nhiệm vụ'},
    ], ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(
        records_path, tmp_path / 'remaining', 'remaining', tmp_path / 'config',
        ['ui.pak'], remaining_only=True,
    )
    rows = read_simple_xlsx(Path(report['xlsx']))
    assert report['unique_rows'] == 2
    assert report['workbook_rows'] == 1
    assert [row['text'] for row in rows] == ['Nhiệm vụ']
    rows[0]['text'] = '任务'
    write_simple_xlsx(Path(report['xlsx']), rows, headers=['id', 'pak', 'source_file', 'text'])
    imported = apply_multi_pak_full_xlsx_to_records(
        records_path, Path(report['xlsx']), Path(report['mapping'])
    )
    final = {row['id']: row['original'] for row in json.loads(records_path.read_text(encoding='utf-8'))}
    assert final == {'a': '装备', 'b': '任务'}
    assert imported['remaining_vietnamese_records'] == 0
    assert imported['remaining_vietnamese_unique_segments'] == 0
    assert imported['xlsx_rows'] == 1
    assert imported['recognized_xlsx_rows'] == 1
    assert imported['translated_xlsx_rows'] == 1
    assert imported['unchanged_xlsx_rows'] == 0
    assert Path(imported['import_report']).is_file()


def test_remaining_workbook_without_google_changes_is_rejected_without_touching_records(tmp_path):
    records_path = tmp_path / 'records.json'
    original = [{
        'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
        '_isPlayerVisible': True, 'source_original': 'Nhiệm vụ', 'original': 'Nhiệm vụ',
    }]
    records_path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(
        records_path, tmp_path / 'remaining', 'remaining', tmp_path / 'config',
        ['ui.pak'], remaining_only=True,
    )
    before = records_path.read_bytes()
    with pytest.raises(ValueError, match='未检测到谷歌译文'):
        apply_multi_pak_full_xlsx_to_records(
            records_path, Path(report['xlsx']), Path(report['mapping'])
        )
    assert records_path.read_bytes() == before
    assert not records_path.with_name(records_path.name + '.before_multi_xlsx_import').exists()


def test_remaining_workbook_does_not_reexport_source_when_current_is_chinese(tmp_path):
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([
        {'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
         '_isPlayerVisible': True, 'source_original': '<Chiến>Kháng 24', 'original': '<战斗>抵抗 24'},
        {'id': 'b', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 3, 'column': 1,
         '_isPlayerVisible': True, 'source_original': '<Chiến>Nhiệm vụ 24', 'original': '<Chiến>Nhiệm vụ 24'},
    ], ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(
        records_path, tmp_path / 'remaining', 'remaining', tmp_path / 'config',
        ['ui.pak'], remaining_only=True,
    )
    cells = [row['text'] for row in read_simple_xlsx(Path(report['xlsx']))]
    assert '战斗' not in cells and '抵抗' not in cells
    assert sorted(cell.strip() for cell in cells) == ['Chiến', 'Nhiệm vụ']


def test_import_rejects_number_reinserted_inside_google_text_cell(tmp_path):
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([{
        'id': 'a', 'pak': 'ui.pak', 'source_file': '1.ini', 'line': 2, 'column': 1,
        '_isPlayerVisible': True, 'source_original': 'Đánh lần 24', 'original': 'Đánh lần 24',
    }], ensure_ascii=False), encoding='utf-8')
    report = export_multi_pak_full_xlsx(
        records_path, tmp_path / 'xlsx', 'all', tmp_path / 'config', ['ui.pak']
    )
    rows = read_simple_xlsx(Path(report['xlsx']))
    assert all('24' not in row['text'] for row in rows)
    rows[0]['text'] = '打第1次'
    write_simple_xlsx(Path(report['xlsx']), rows, headers=['id', 'pak', 'source_file', 'text'])
    imported = apply_multi_pak_full_xlsx_to_records(
        records_path, Path(report['xlsx']), Path(report['mapping'])
    )
    assert json.loads(records_path.read_text(encoding='utf-8'))[0]['original'] == 'Đánh lần 24'
    assert imported['rejected_unique_segments'] == 1


def export_visible_queue(source, xlsx_dir, config_dir, pak='updatefs.pak'):
    records, _stats = analyze_folder(source, pak, workers=1)
    records_path = source.parent / '_visible_records.json'
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    return export_xlsx_file_queue(
        source, xlsx_dir, pak, records_path=records_path, metadata_dir=config_dir
    )


def test_file_queue_is_sorted_and_never_mixes_files():
    rows = [
        {'id': '3', 'text': 'c', '_queue_file': '0200_b.lua'},
        {'id': '1', 'text': 'a', '_queue_file': '0001_a.tsv'},
        {'id': '2', 'text': 'b', '_queue_file': '0001_a.tsv'},
    ]
    buckets, files = make_file_ordered_buckets(rows, 1, 1000)
    assert files == ['0001_a.tsv', '0200_b.lua']
    assert [[row['id'] for row in bucket] for bucket in buckets] == [['1'], ['2'], ['3']]
    assert all(len({row['_queue_file'] for row in bucket}) == 1 for bucket in buckets)


def test_xlsx_export_creates_one_workbook_per_source_file(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / '0001.tsv').write_text('Name\tImage\nNhiệm vụ\t\\spr\\ui\\x.spr\n', encoding='utf-8')
    (source / '0002.ini').write_text(';一次任务\nTitle=Trang bị\nImageFile=\\spr\\ui\\y.spr\n', encoding='utf-8')
    (source / '0003.tsv').write_text('ResId\tPath\n1\t\\spr\\role\\z.spr\n', encoding='utf-8')
    xlsx_dir = tmp_path / 'xlsx'
    config_dir = tmp_path / 'config'
    report = export_visible_queue(source, xlsx_dir, config_dir)
    workbooks = sorted(xlsx_dir.rglob('*.xlsx'))
    assert report['exported_files'] == 2
    assert all(path.parent == xlsx_dir for path in workbooks)
    assert [path.name for path in workbooks] == [
        '0001.tsv_localization.xlsx',
        '0002.ini_localization.xlsx',
    ]
    assert not any('0003.tsv' in str(path) for path in workbooks)
    assert all(path.suffix == '.xlsx' for path in xlsx_dir.iterdir())
    assert list(config_dir.glob('*_mapping.json'))
    assert (config_dir / '_file_queue_export_report.json').is_file()
    assert report['excluded_not_player_visible'] >= 1
    mapping = json.loads((config_dir / '0001.tsv_localization_mapping.json').read_text(encoding='utf-8'))
    assert mapping['scope'] == 'player-visible-only'
    assert mapping['version'] == 6


def test_ini_visible_labels_are_not_lost_when_key_name_has_no_text_hint(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'attributes.ini').write_text(
        'durability_v=$Độ bền:#d1-\n'
        'requirelevel=$Đẳng cấp yêu cầu:#d1-\n'
        'requireseries=$Hệ phái:#s1-\n'
        'ImageFile=\\spr\\ui\\item.spr\n',
        encoding='utf-8',
    )
    records, _stats = analyze_folder(source, 'settings.pak', workers=1)
    originals = {record['original'] for record in records}
    assert {'$Độ bền:#d1-', '$Đẳng cấp yêu cầu:#d1-', '$Hệ phái:#s1-'} <= originals
    assert not any('item.spr' in value for value in originals)


def test_full_xlsx_is_one_player_visible_workbook_with_source_filenames(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / '0001.tsv').write_text('Name\tImage\nNhiệm vụ\t\\spr\\ui\\x.spr\n', encoding='utf-8')
    (source / '0002.ini').write_text('Title=Trang bị\nImageFile=\\spr\\ui\\y.spr\n', encoding='utf-8')
    records, _stats = analyze_folder(source, 'updatefs.pak', workers=1)
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    report = export_full_xlsx(
        source, tmp_path / 'full_xlsx', 'updatefs_player_visible_full',
        'updatefs.pak', records_path, metadata_dir=tmp_path / 'config',
    )
    workbooks = list((tmp_path / 'full_xlsx').glob('*.xlsx'))
    assert len(workbooks) == 1
    rows = read_simple_xlsx(workbooks[0])
    assert rows
    assert all({'id', 'source_file', 'text'} <= set(row) for row in rows)
    assert {name for row in rows for name in row['source_file'].split(' | ')} == {'0001.tsv', '0002.ini'}
    assert not any('ImageFile' in row['text'] or '\\spr\\' in row['text'] for row in rows)
    assert report['mode'] == 'single-player-visible-workbook'
    assert report['scope'] == 'player-visible-only'
    assert report['includes_source_file_column'] is True


def test_full_xlsx_import_ignores_tampered_filename_and_uses_stable_mapping(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / '0001.ini').write_text('Title=Trang bị\n', encoding='utf-8')
    records, _stats = analyze_folder(source, 'updatefs.pak', workers=1)
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    report = export_full_xlsx(
        source, tmp_path / 'full_xlsx', 'updatefs_player_visible_full',
        'updatefs.pak', records_path, metadata_dir=tmp_path / 'config',
    )
    workbook = Path(report['xlsx'])
    rows = read_simple_xlsx(workbook)
    # Simulate Google changing both the informational filename and all headers.
    write_simple_xlsx(workbook, [{
        '编号': rows[0]['id'],
        '来源文件': '谷歌错误地修改了文件名.ini',
        '译文': '装备',
    }], ['编号', '来源文件', '译文'])
    imported = apply_xlsx_to_records(
        records_path, workbook, Path(report['mapping_json']), 'updatefs.pak'
    )
    saved = json.loads(records_path.read_text(encoding='utf-8'))
    changed = [row for row in saved if row.get('original') == '装备']
    assert imported['changed'] == 1
    assert len(changed) == 1
    assert changed[0]['source_file'] == '0001.ini'


def test_flat_export_disambiguates_equal_basenames(tmp_path):
    source = tmp_path / 'source'
    (source / 'a').mkdir(parents=True)
    (source / 'b').mkdir(parents=True)
    (source / 'a' / 'same.ini').write_text('Title=Trang bị\n', encoding='utf-8')
    (source / 'b' / 'same.ini').write_text('Title=Nhiệm vụ\n', encoding='utf-8')
    export_visible_queue(source, tmp_path / 'xlsx', tmp_path / 'config')
    assert sorted(path.name for path in (tmp_path / 'xlsx').glob('*.xlsx')) == [
        'a__same.ini_localization.xlsx',
        'b__same.ini_localization.xlsx',
    ]
    assert not [path for path in (tmp_path / 'xlsx').iterdir() if path.is_dir()]


def test_ollama_folder_runs_every_workbook_in_filename_order(tmp_path, monkeypatch):
    xlsx_dir = tmp_path / 'xlsx'
    config_dir = tmp_path / 'config'
    xlsx_dir.mkdir()
    config_dir.mkdir()
    for name in ('b.tsv_localization.xlsx', 'a.ini_localization.xlsx'):
        (xlsx_dir / name).write_bytes(b'fixture')
    called = []

    def fake_run(workspace, pak, **kwargs):
        assert kwargs['metadata_dir'] == config_dir
        called.append(kwargs['xlsx'].name)
        return {'status': 'complete', 'stopped': False}

    monkeypatch.setattr(ollama_batch_translate, 'run', fake_run)
    report = ollama_batch_translate.run_folder(tmp_path, 'updatefs.pak', xlsx_dir)
    assert called == ['a.ini_localization.xlsx', 'b.tsv_localization.xlsx']
    assert report['completed_files'] == 2
    assert report['failed_files'] == 0


def test_ollama_folder_progress_is_global_row_weighted_and_never_resets(tmp_path, monkeypatch):
    xlsx_dir = tmp_path / 'xlsx'
    config_dir = tmp_path / 'config'
    xlsx_dir.mkdir()
    config_dir.mkdir()
    write_simple_xlsx(xlsx_dir / 'a.ini_localization.xlsx', [{'id': 'a', 'text': 'Mot'}], ['id', 'text'])
    write_simple_xlsx(xlsx_dir / 'b.tsv_localization.xlsx', [
        {'id': 'b1', 'text': 'Hai'}, {'id': 'b2', 'text': 'Ba'}, {'id': 'b3', 'text': 'Bon'},
    ], ['id', 'text'])
    events = []

    def fake_run(workspace, pak, **kwargs):
        kwargs['progress']({'percent': 0, 'message': 'start'})
        kwargs['progress']({'percent': 50, 'message': 'half'})
        kwargs['progress']({'percent': 100, 'message': 'done'})
        return {'status': 'complete', 'remaining_rows': 0, 'stopped': False}

    monkeypatch.setattr(ollama_batch_translate, 'run', fake_run)
    report = ollama_batch_translate.run_folder(
        tmp_path, 'updatefs.pak', xlsx_dir, metadata_dir=config_dir,
        progress=events.append,
    )
    percents = [float(event['percent']) for event in events]
    completed_rows = [float(event['completed_rows']) for event in events]
    assert percents == sorted(percents)
    assert completed_rows == sorted(completed_rows)
    assert events[-1]['completed_rows'] == 4
    assert events[-1]['total_rows'] == 4
    assert report['completed_files'] == 2


def test_ollama_folder_skips_completed_unchanged_workbook(tmp_path, monkeypatch):
    xlsx_dir = tmp_path / 'xlsx'
    config_dir = tmp_path / 'config'
    xlsx_dir.mkdir()
    config_dir.mkdir()
    done = xlsx_dir / 'a.ini_localization.xlsx'
    pending = xlsx_dir / 'b.tsv_localization.xlsx'
    done.write_bytes(b'done')
    pending.write_bytes(b'pending')
    (config_dir / 'a.ini_localization_translation_report.json').write_text(json.dumps({
        'status': 'complete', 'remaining_rows': 0,
    }), encoding='utf-8')
    called = []

    def fake_run(workspace, pak, **kwargs):
        called.append(kwargs['xlsx'].name)
        return {'status': 'complete', 'remaining_rows': 0, 'stopped': False}

    monkeypatch.setattr(ollama_batch_translate, 'run', fake_run)
    report = ollama_batch_translate.run_folder(
        tmp_path, 'updatefs.pak', xlsx_dir, metadata_dir=config_dir
    )
    assert called == ['b.tsv_localization.xlsx']
    assert report['completed_files'] == 2
    assert report['skipped_completed_files'] == 1
    assert report['processed_files'] == 1
    assert report['remaining_files'] == 0


def test_ollama_folder_prioritizes_workbooks_with_current_vietnamese(tmp_path, monkeypatch):
    xlsx_dir = tmp_path / 'xlsx'
    config_dir = tmp_path / 'config'
    localization_dir = tmp_path / 'localization'
    xlsx_dir.mkdir()
    config_dir.mkdir()
    localization_dir.mkdir()
    (xlsx_dir / 'a.ini_localization.xlsx').write_bytes(b'chinese')
    (xlsx_dir / 'z.tsv_localization.xlsx').write_bytes(b'vietnamese')
    (localization_dir / 'text_records.json').write_text(json.dumps([
        {'id': 'zh', 'pak': 'updatefs.pak', 'source_file': 'a.ini', 'original': '装备'},
        {'id': 'vi', 'pak': 'updatefs.pak', 'source_file': 'z.tsv', 'original': 'Trang bị'},
    ], ensure_ascii=False), encoding='utf-8')
    called = []

    def fake_run(workspace, pak, **kwargs):
        called.append(kwargs['xlsx'].name)
        return {'status': 'complete', 'remaining_rows': 0, 'stopped': False}

    monkeypatch.setattr(ollama_batch_translate, 'run', fake_run)
    report = ollama_batch_translate.run_folder(
        tmp_path, 'updatefs.pak', xlsx_dir, metadata_dir=config_dir
    )
    assert called == ['z.tsv_localization.xlsx', 'a.ini_localization.xlsx']
    assert report['prioritized_vietnamese_files'] == 1


def test_ollama_reuses_current_studio_chinese_from_stale_workbook(tmp_path, monkeypatch):
    xlsx_dir = tmp_path / 'xlsx'
    config_dir = tmp_path / 'config'
    localization_dir = tmp_path / 'localization'
    xlsx_dir.mkdir()
    config_dir.mkdir()
    localization_dir.mkdir()
    workbook = xlsx_dir / 'sample_localization.xlsx'
    write_simple_xlsx(workbook, [{'id': 'x1', 'text': 'Trang bị'}], ['id', 'text'])
    (config_dir / 'sample_localization_records_mapping.json').write_text(json.dumps({
        'x1': [{
            'id': 'r1', 'source': 'Trang bị', 'tokens': [],
            'template': '{TEXT}', 'export_text': 'Trang bị',
        }],
    }, ensure_ascii=False), encoding='utf-8')
    (localization_dir / 'text_records.json').write_text(json.dumps([{
        'id': 'r1', 'pak': 'updatefs.pak', 'source_file': 'sample.tsv',
        'source_original': 'Trang bị', 'original': '装备', '_isPlayerVisible': True,
    }], ensure_ascii=False), encoding='utf-8')

    def must_not_call_ollama(*args, **kwargs):
        raise AssertionError('current Studio Chinese should bypass Ollama')

    monkeypatch.setattr(ollama_batch_translate, 'ollama_chat', must_not_call_ollama)
    report = ollama_batch_translate.run(
        tmp_path, 'updatefs.pak', xlsx=workbook, metadata_dir=config_dir
    )
    assert report['status'] == 'complete'
    assert report['reused_from_studio'] == 1
    assert report['total_buckets'] == 0


def test_ollama_accepts_multi_pak_remaining_mapping_and_preserves_columns(tmp_path, monkeypatch):
    xlsx_dir = tmp_path / 'remaining_xlsx'
    config_dir = tmp_path / 'config'
    localization_dir = tmp_path / 'localization'
    xlsx_dir.mkdir()
    config_dir.mkdir()
    localization_dir.mkdir()
    workbook = xlsx_dir / 'all_paks_player_visible_remaining_localization.xlsx'
    write_simple_xlsx(workbook, [{
        'id': 's1', 'pak': 'settings.pak', 'source_file': '0085.tsv', 'text': '装备',
    }], ['id', 'pak', 'source_file', 'text'])
    (config_dir / 'all_paks_player_visible_remaining_localization_mapping.json').write_text(
        json.dumps({
            'version': 7, 'mode': 'multi-pak-out-of-band-skeleton',
            'remaining_only': True, 'workbook_rows': 1,
            'rows': [['s1', 'Trang bị', 'Trang bị'], ['already-done', 'Nhiệm vụ', '任务']],
            'records': [],
        }, ensure_ascii=False), encoding='utf-8',
    )
    (localization_dir / 'text_records.json').write_text('[]', encoding='utf-8')

    def must_not_call_ollama(*args, **kwargs):
        raise AssertionError('already Chinese row should bypass Ollama')

    monkeypatch.setattr(ollama_batch_translate, 'ollama_chat', must_not_call_ollama)
    report = ollama_batch_translate.run(
        tmp_path, 'settings.pak', xlsx=workbook, metadata_dir=config_dir
    )
    assert report['status'] == 'complete'
    rows = read_simple_xlsx(workbook)
    assert {key: rows[0][key] for key in ('id', 'pak', 'source_file', 'text')} == {
        'id': 's1', 'pak': 'settings.pak', 'source_file': '0085.tsv', 'text': '装备',
    }


def test_folder_import_applies_only_changed_translation(tmp_path):
    source = tmp_path / 'source'
    output = tmp_path / 'xlsx'
    config = tmp_path / 'config'
    source.mkdir()
    (source / '0001.ini').write_text('Title=Trang bị\n', encoding='utf-8')
    export_visible_queue(source, output, config)
    mapping_path = config / '0001.ini_localization_mapping.json'
    mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
    row_id, source_text, cells = mapping['rows'][0]
    _file_index, row_no, column_no = cells[0]
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([{
        'id': 'record-1',
        'pak': 'updatefs.pak',
        'source_file': '0001.ini',
        'line': row_no,
        'column': column_no,
        'source_original': 'Trang bị',
        'original': 'Trang bị',
    }], ensure_ascii=False), encoding='utf-8')
    write_simple_xlsx(
        output / '0001.ini_localization.xlsx',
        [{'id': row_id, 'text': '装备'}],
        ['id', 'text'],
    )
    report = apply_xlsx_folder_to_records(records_path, output, 'updatefs.pak')
    records = json.loads(records_path.read_text(encoding='utf-8'))
    assert report['imported_files'] == 1
    assert report['changed'] == 1
    assert records[0]['original'] == '装备'
    assert records[0]['status'] == '已翻译'
    assert records[0]['_isPlayerVisible'] is True
    assert all(path.suffix == '.xlsx' for path in output.iterdir())


def test_folder_import_rejects_translation_that_build_would_reject(tmp_path):
    source = tmp_path / 'source'
    output = tmp_path / 'xlsx'
    config = tmp_path / 'config'
    source.mkdir()
    (source / '0001.ini').write_text('Title=Trang bị\\n\n', encoding='utf-8')
    export_visible_queue(source, output, config)
    mapping = json.loads((config / '0001.ini_localization_mapping.json').read_text(encoding='utf-8'))
    row_id, _source_text, cells = mapping['rows'][0]
    _file_index, row_no, column_no = cells[0]
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([{
        'id': 'record-1', 'pak': 'updatefs.pak', 'source_file': '0001.ini',
        'line': row_no, 'column': column_no,
        'source_original': 'Trang bị\\n', 'original': 'Trang bị\\n',
    }], ensure_ascii=False), encoding='utf-8')
    write_simple_xlsx(output / '0001.ini_localization.xlsx', [{'id': row_id, 'text': '装备'}], ['id', 'text'])
    report = apply_xlsx_folder_to_records(records_path, output, 'updatefs.pak')
    records = json.loads(records_path.read_text(encoding='utf-8'))
    assert report['rejected'] == 1
    assert report['changed'] == 0
    assert records[0]['original'] == 'Trang bị\\n'
