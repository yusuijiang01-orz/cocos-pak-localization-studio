from pathlib import Path
import sys, tempfile, shutil, json, struct
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from pak_core import extract_one
from localization_analyzer import decode_tcvn, decode_best, encode_legacy_text, encode_text_for_source, is_resource_reference, analyze_folder, normalize_text_resource_utf8, is_visible_tsv_header, is_visible_tsv_column, is_visible_ini_key
from tabular_converter import convert_tsv_file
from tsv_localization import export_tsv_localization, import_tsv_localization, apply_csv_to_records, restore_template, token_template, validate_translation, iter_translatable_cells
from script_translator import incomplete_translation
from pak_builder import materialize_records_to_modified_dir, rebuild_pak, nrv2b_compress, _read_index


def test_schema_fields_only_expose_player_visible_text():
    headers = [
        '名称', '道具种类', '具体类别', '详细类别', '动画文件名', '对应物件索引',
        '宽度', '高度', '说明文字', '五行属性', '价格', '等级', '是否叠放',
        '基础属性1类型', '基础属性1最小值', '需求属性1数值', '装备id', '套装id', 'ResId',
    ]
    assert [header for header in headers if is_visible_tsv_header(header)] == ['名称', '说明文字']
    assert is_visible_ini_key('Title')
    assert is_visible_ini_key('ButtonText')
    assert is_visible_ini_key('ToolTip')
    assert not is_visible_ini_key('ImageFile')
    assert not is_visible_ini_key('ResourcePath')
    assert not is_visible_ini_key('NpcId')
    assert is_visible_tsv_column('RewardName', '0012_0130A0F4.tsv')
    assert is_visible_tsv_column('TaskTips', '0377_27D8F94D.tsv')
    assert is_visible_tsv_column('SkillDesc', '2422_EC1243FF.tsv')
    assert is_visible_tsv_column('value', '0814_5325A29A.tsv')
    assert not is_visible_tsv_column('value', 'unknown.tsv')
    assert not is_visible_tsv_column('人物名称', '0999_64D8690E.tsv')
    assert not is_visible_tsv_column('Skill1', '0553_38EBCB17.tsv')


def test_file_aware_tsv_and_ini_boundaries(tmp_path):
    safe = tmp_path / '0377_27D8F94D.tsv'
    safe.write_text('MapID\tTaskTips\timage\n2\tNhiệm vụ nhận thưởng\t\\spr\\item\\x.spr\n', encoding='utf-8')
    rows = list(iter_translatable_cells(safe, 'updatefs.pak'))
    assert [(row['column'], row['source']) for row in rows] == [(2, 'Nhiệm vụ nhận thưởng')]

    protected = tmp_path / '0999_64D8690E.tsv'
    protected.write_text('人物名称\t资源文件路经\nNam giáp sĩ\t\\spr\\role\\nam.spr\n', encoding='utf-8')
    assert list(iter_translatable_cells(protected, 'updatefs.pak')) == []

    ini = tmp_path / 'ui.ini'
    ini.write_text('Title=Nhiệm vụ\nScriptName=Nhiệm vụ nguy hiểm\nImageFile=\\spr\\ui\\x.spr\n', encoding='utf-8')
    assert [(row['row'], row['source']) for row in iter_translatable_cells(ini, 'updatefs.pak')] == [(1, 'Nhiệm vụ')]


def test_materialize_unchanged_records_produces_no_buildable_files(tmp_path):
    extracted=tmp_path/'extracted'
    extracted.mkdir()
    (extracted/'0000_TEST.tsv').write_bytes(b'id\\tname\\n1\\tXin chao\\n')
    records_path=tmp_path/'records.json'
    records_path.write_text(json.dumps([{
        'id':'same','pak':'test.pak','source_file':'0000_TEST.tsv',
        'line':2,'column':2,'source_original':'Xin chao','original':'Xin chao',
        'language':'vi','status':'未翻译','encoding':'utf-8',
    }],ensure_ascii=False),encoding='utf-8')
    output=tmp_path/'modified'

    report=materialize_records_to_modified_dir(extracted,records_path,'test.pak',output)

    assert report['no_changes'] is True
    assert report['modified_records'] == 0
    assert report['changed_file_count'] == 0
    assert not (output/'0000_TEST.tsv').exists()


def test_rebuild_preserves_unchanged_stream_offsets_and_bytes(tmp_path):
    raw0=b'unchanged sprite bytes'*20
    raw1=b'old text value'*20
    packed0=nrv2b_compress(raw0)
    packed1=nrv2b_compress(raw1)
    # Store entry 1 physically before entry 0 to model the real archives.
    data=bytearray(b'PACK'+struct.pack('<II',2,0))
    off1=len(data); data.extend(packed1)
    off0=len(data); data.extend(packed0)
    index_offset=len(data)
    entries=[(0x11111111,off0,len(raw0),len(packed0),1),(0x22222222,off1,len(raw1),len(packed1),1)]
    for hid,off,real,packed,method in entries:
        data.extend(struct.pack('<III',hid,off,real)+packed.to_bytes(3,'little')+bytes([method]))
    struct.pack_into('<I',data,8,index_offset)
    original=tmp_path/'original.pak'; original.write_bytes(data)
    modified=tmp_path/'modified'; modified.mkdir()
    (modified/'0001_22222222.txt').write_bytes(b'new translated text')
    output=tmp_path/'output.pak'

    rebuild_pak(original,modified,['0001_22222222.txt'],output)

    rebuilt=output.read_bytes()
    _count,_idx,new_entries=_read_index(rebuilt)
    assert new_entries[0]['offset'] == off0
    assert rebuilt[off0:off0+len(packed0)] == packed0
    assert new_entries[1]['offset'] >= len(data)

def test_tcvn_sample():
    raw='Th¨ng cÊp ph¸p b¶o'.encode('latin1')
    assert decode_tcvn(raw)=='Thăng cấp pháp bảo'

def test_mixed_decode():
    s,enc,lang,_=decode_best('装备名称'.encode('gb18030'))
    assert s=='装备名称' and lang=='zh'
    raw='Th¨ng cÊp ph¸p b¶o'.encode('latin1')
    s,enc,lang,_=decode_best(raw)
    assert s=='Thăng cấp pháp bảo' and lang=='vi'

def test_mixed_gbk_tcvn_line():
    raw=b'Uy Phong L\xc9m Li\xd6t: '+ '杨戬的威风'.encode('gb18030') + b' D\xd1p lo\xb9n V\xb9n Ti\xaan Tr\xcbn'
    s,enc,lang,_=decode_best(raw)
    assert 'Uy Phong Lẫm Liệt' in s
    assert '杨戬的威风' in s
    assert 'Dẹp loạn Vạn Tiên Trận' in s
    assert enc=='mixed-gbk-tcvn3' and lang=='mixed'

def test_resource_path_filter():
    assert is_resource_reference(r'\Spr\Ui4\任务\按钮.spr','Image')
    assert not is_resource_reference('领取任务奖励','Text')

def test_convert_tsv_file(tmp_path):
    src=tmp_path/'sample.tsv'
    src.write_bytes(b'id\tname\n1\tTh\xa8ng c\xcap ph\xb8p b\xb6o\n')
    out=tmp_path/'sample.csv'
    report=convert_tsv_file(src,out)
    assert report['rows']==2
    text=out.read_text(encoding='utf-8-sig')
    assert 'id,name' in text
    assert 'Thăng cấp pháp bảo' in text

def test_tsv_localization_roundtrip(tmp_path):
    src_dir=tmp_path/'src'
    src_dir.mkdir()
    src=src_dir/'0007_0130A0F4.tsv'
    src.write_bytes(b'id\tname\n1\t<c=yellow>Th\xa8ng c\xcap ph\xb8p b\xb6o<c>\n')
    csv_dir=tmp_path/'csv_before'
    report=export_tsv_localization(src_dir,csv_dir,'updatefs.pak',workers=1)
    assert report['records']==1
    csv_path=csv_dir/'0007_0130A0F4.csv'
    text=csv_path.read_text(encoding='utf-8-sig')
    assert text.splitlines()[0]=='id,text,placeholders'
    assert ',Thăng cấp pháp bảo,' in text
    text=text.replace('Thăng cấp pháp bảo','升级法宝')
    csv_path.write_text(text,encoding='utf-8-sig')
    out_dir=tmp_path/'tsv_after'
    result=import_tsv_localization(src_dir,csv_dir,out_dir,workers=1)
    assert result['updated']==1
    assert b'<c=yellow>'+('升级法宝'.encode('utf-8'))+b'<c>' in (out_dir/'0007_0130A0F4.tsv').read_bytes()

def test_text_resource_localization_roundtrip_preserves_ini_keys(tmp_path):
    src_dir=tmp_path/'src'
    src_dir.mkdir()
    (src_dir/'config.ini').write_bytes(b'Name=Th\xa8ng c\xcap ph\xb8p b\xb6o\nPath=\\Spr\\Ui\\icon.spr\n')
    (src_dir/'notice.txt').write_text('Xin chào người chơi\n',encoding='utf-8')
    csv_dir=tmp_path/'csv_before'
    report=export_tsv_localization(src_dir,csv_dir,'settings.pak',workers=1)
    assert report['records']==2
    assert report['ini_files']==1
    assert report['txt_files']==1
    ini_csv=csv_dir/'config.csv'
    txt_csv=csv_dir/'notice.csv'
    assert 'Thăng cấp pháp bảo' in ini_csv.read_text(encoding='utf-8-sig')
    assert 'Xin chào người chơi' in txt_csv.read_text(encoding='utf-8-sig')
    ini_csv.write_text(ini_csv.read_text(encoding='utf-8-sig').replace('Thăng cấp pháp bảo','升级法宝'),encoding='utf-8-sig')
    txt_csv.write_text(txt_csv.read_text(encoding='utf-8-sig').replace('Xin chào người chơi','你好玩家'),encoding='utf-8-sig')
    out_dir=tmp_path/'text_after'
    result=import_tsv_localization(src_dir,csv_dir,out_dir,workers=1)
    assert result['updated']==2
    assert b'Name=' + '升级法宝'.encode('utf-8') in (out_dir/'config.ini').read_bytes()
    assert b'Path=\\Spr\\Ui\\icon.spr' in (out_dir/'config.ini').read_bytes()
    assert '你好玩家'.encode('utf-8') in (out_dir/'notice.txt').read_bytes()

def test_encode_text_for_source_forces_utf8_for_all_edited_text():
    assert encode_legacy_text('升级法宝')=='升级法宝'.encode('gbk')
    assert decode_tcvn(encode_legacy_text('Thăng cấp'))=='Thăng cấp'
    assert encode_legacy_text('A\u00a0B')==b'A B'
    assert encode_legacy_text('à á ò')==bytes((181,32,184,32,223))
    assert encode_text_for_source('中文','utf-8')=='中文'.encode('utf-8')
    assert encode_text_for_source('中文','tcvn3')=='中文'.encode('utf-8')
    assert encode_text_for_source('<color=Metal>被动防御辅助<color>','windows-1258') == '<color=Metal>被动防御辅助<color>'.encode('utf-8')
    assert encode_text_for_source('中文','mixed-gbk-tcvn3')=='中文'.encode('utf-8')
    assert encode_text_for_source('中文','gb18030')=='中文'.encode('utf-8')
    assert encode_text_for_source('中文','utf-16-be',b'\xa7\xb5o C\xac')=='中文'.encode('utf-8')
    assert encode_text_for_source('中文','utf-16-le',b'\xff\xfe'+'原文'.encode('utf-16-le'))=='中文'.encode('utf-8')

def test_whole_legacy_resource_is_normalized_to_utf8():
    raw=b'id\t' + encode_legacy_text('Thăng cấp') + b'\r\n2\t' + '中文'.encode('gbk') + b'\r\n'
    converted=normalize_text_resource_utf8(raw,'.tsv')
    text=converted.decode('utf-8')
    assert 'Thăng cấp' in text
    assert '中文' in text
    assert converted.endswith(b'\r\n')

def test_partial_dictionary_translation_is_rejected():
    assert incomplete_translation('S恶h Chư 侯 (khởi)')
    assert incomplete_translation('T出来ng 备橙破军')
    assert not incomplete_translation('所有玩家隐藏')
    assert not incomplete_translation('NPC攻击增加 10%')

def test_csv_sync_can_restore_rejected_translation_to_source(tmp_path):
    records=tmp_path/'records.json'; csv_dir=tmp_path/'csv'; csv_dir.mkdir()
    records.write_text('[{"id":"r1","pak":"p.pak","original":"错译","source_original":"越南原文"}]',encoding='utf-8')
    (csv_dir/'_tsv_localization_index.json').write_text('{"r1":{"source":"越南原文","export_text":"越南原文","row":1,"column":1,"template":"{TEXT}","tokens":[]}}',encoding='utf-8')
    (csv_dir/'a.csv').write_text('\ufeffid,text\nr1,越南原文\n',encoding='utf-8')
    result=apply_csv_to_records(records,csv_dir,'p.pak')
    assert result['updated']==1
    assert '"original":"越南原文"' in records.read_text(encoding='utf-8')

def test_analyzer_indexes_text_resource_files(tmp_path):
    src=tmp_path/'src'
    src.mkdir()
    (src/'0001.tsv').write_bytes(b'id\tname\n1\tTh\xa8ng c\xcap ph\xb8p b\xb6o\n')
    (src/'0002.ini').write_bytes(b'Name=Th\xa8ng c\xcap ph\xb8p b\xb6o\n')
    (src/'0003.txt').write_text('Xin chào người chơi\n',encoding='utf-8')
    (src/'0004.lua').write_text('print("Thăng cấp pháp bảo")',encoding='utf-8')
    records,stats=analyze_folder(src,'updatefs.pak',workers=1)
    assert records
    assert {r['source_file'] for r in records}=={'0001.tsv','0002.ini','0003.txt','0004.lua'}


def test_analyzer_excludes_runtime_only_records_from_studio_and_xlsx_source(tmp_path):
    src=tmp_path/'src'; src.mkdir()
    (src/'0001.txt').write_text(
        '<npcpos=Tạp hóa Thương,12,1551,3019>\n'
        r'\spr\item\equip\Đặc_biệt12.spr'+'\n'
        '[Btn_ItemBox]\n'
        'AddCommand("Shift+LButton", "", "Mouse_Force0()")\n'
        'Bái phỏng Tạp hóa Thương\n',
        encoding='utf-8'
    )
    records,_=analyze_folder(src,'updatefs.pak',workers=1)
    assert [r['original'] for r in records]==['Bái phỏng Tạp hóa Thương']

def test_lua_analyzer_and_materializer_only_change_visible_literals(tmp_path):
    extracted=tmp_path/'extracted'; extracted.mkdir()
    lua=extracted/'2239_DAD4973C.lua'
    lua.write_text('NewTask_TaskTrack("Mao Lư: ".."Bái phỏng Tạp hóa Thương") -- "không dịch chú thích"\n',encoding='utf-8')
    records,_=analyze_folder(extracted,'updatefs.pak',workers=1)
    lua_records=[r for r in records if r['source_file']==lua.name]
    assert [(r['line'],r['column'],r['original']) for r in lua_records]==[
        (1,1,'Mao Lư:'),(1,2,'Bái phỏng Tạp hóa Thương')
    ]
    for record in lua_records:
        record['source_original']=record['original']
        record['original']={'Mao Lư:':'茅庐：','Bái phỏng Tạp hóa Thương':'拜访杂货商'}[record['original']]
    records_path=tmp_path/'records.json'
    records_path.write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'modified'
    report=materialize_records_to_modified_dir(extracted,records_path,'updatefs.pak',out)
    result=(out/lua.name).read_text(encoding='utf-8')
    assert result=='NewTask_TaskTrack("茅庐：".."拜访杂货商") -- "không dịch chú thích"\n'
    assert report['changed_files']==[lua.name]


def test_lua_position_tags_are_hidden_and_outer_text_is_independent(tmp_path):
    extracted=tmp_path/'extracted'; extracted.mkdir()
    lua=extracted/'0102_0A24A485.lua'
    source='Track("Bái phỏng <npcpos=Tạp hóa Thương,12,1551,3019>")\n'
    lua.write_text(source,encoding='utf-8')
    records,_=analyze_folder(extracted,'updatefs.pak',workers=1)
    lua_records=[r for r in records if r['source_file']==lua.name]
    assert [(r['column'],r['original']) for r in lua_records]==[(1,'Bái phỏng')]
    translations={'Bái phỏng':'拜访'}
    for record in lua_records:
        record['source_original']=record['original']
        record['original']=translations[record['original']]
    records_path=tmp_path/'records.json'
    records_path.write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'modified'
    materialize_records_to_modified_dir(extracted,records_path,'updatefs.pak',out)
    assert (out/lua.name).read_text(encoding='utf-8') == (
        'Track("拜访<npcpos=Tạp hóa Thương,12,1551,3019>")\n'
    )


def test_lua_structure_skeleton_keeps_tags_and_coordinates():
    from lua_localization import lua_structure_skeleton
    source='Track("Đến <npcpos=Tạp hóa Thương,12,1551,3019>")'.encode()
    translated='Track("前往<npcpos=杂货铺,12,1551,3019>")'.encode()
    broken='Track("前往<npcpos=杂货铺,12,1551,3020>")'.encode()
    assert lua_structure_skeleton(source)!=lua_structure_skeleton(translated)
    assert lua_structure_skeleton(source)!=lua_structure_skeleton(broken)

def test_materialize_normalizes_all_official_text_files_even_without_translation(tmp_path):
    extracted=tmp_path/'extracted'; extracted.mkdir()
    changed=extracted/'0001.tsv'; unchanged=extracted/'0002.tsv'; csv_file=extracted/'0003.csv'
    changed.write_bytes(b'id\tname\n1\tXin chao\n')
    legacy=b'id\tname\n2\tTh\xa8ng c\xcap\n'
    unchanged.write_bytes(legacy)
    csv_file.write_bytes(b'id,name\n3,Th\xa8ng c\xcap\n')
    records=tmp_path/'records.json'
    records.write_text(json.dumps([{
        'id':'r1','pak':'test.pak','source_file':'0001.tsv','line':2,'column':2,
        'encoding':'utf-8','source_original':'Xin chao','original':'你好',
    }],ensure_ascii=False),encoding='utf-8')
    out=tmp_path/'modified'
    report=materialize_records_to_modified_dir(extracted,records,'test.pak',out)
    assert set(report['changed_files'])=={'0001.tsv','0002.tsv','0003.csv'}
    assert (out/'0002.tsv').read_bytes().decode('utf-8')=='id\tname\n2\tThăng cấp\n'
    assert (out/'0003.csv').read_bytes().decode('utf-8')=='id,name\n3,Thăng cấp\n'
    assert report['encoding']=='all-text-resources-utf8-v1'


def test_materialize_report_keeps_all_skipped_ids_beyond_detail_limit(tmp_path):
    extracted=tmp_path/'extracted'; extracted.mkdir()
    lua=extracted/'0102_0A24A485.lua'
    lua.write_text(''.join(f'Track("Nhiệm vụ {i} %s")\n' for i in range(135)),encoding='utf-8')
    records,_=analyze_folder(extracted,'updatefs.pak',workers=1)
    for record in records:
        record['source_original']=record['original']
        record['original']='#错误译文'
    records[0]['original']='合法译文 %s'
    records_path=tmp_path/'records.json'
    records_path.write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')

    report=materialize_records_to_modified_dir(extracted,records_path,'updatefs.pak',tmp_path/'modified')
    assert report['skipped_count']==134
    assert len(report['skipped'])==100
    assert len(report['skipped_ids'])==134
    assert len(set(report['skipped_ids']))==134

def test_validate_translation_keeps_tags():
    ok,reason=validate_translation('<c=yellow>Thăng cấp<c>','升级')
    assert not ok and '<c=yellow>' in reason

def test_token_template_restores_wrappers_and_inline_placeholders():
    meta=token_template('<c=yellow>Thăng cấp<c>')
    assert meta['text']=='Thăng cấp'
    restored,error=restore_template('升级',meta)
    assert error=='' and restored=='<c=yellow>升级<c>'
    meta=token_template('Nhận %s vật phẩm {ITEM_ID}')
    assert meta['text']=='Nhận ◈1◈ vật phẩm ◈2◈'
    restored,error=restore_template('获得 {P2} 道具 {P1}',meta)
    assert error=='' and restored=='获得 {ITEM_ID} 道具 %s'


def test_restore_template_accepts_safe_and_translator_altered_markers():
    meta=token_template('Nhận %s vật phẩm 10 từ $PLAYER_ID')
    assert meta['text']=='Nhận ◈1◈ vật phẩm ◈2◈ từ ◈3◈'
    restored,error=restore_template('从《P3》获得◈2◈件道具（P1）',meta)
    assert error==''
    assert restored=='从$PLAYER_ID获得10件道具%s'


def test_token_template_protects_entire_long_number():
    meta=token_template('#5000 vạn bạc')
    assert meta['text']=='◈1◈◈2◈ vạn bạc'
    restored,error=restore_template('◈1◈◈2◈万银两',meta)
    assert error=='' and restored=='#5000万银两'
    meta=token_template('19h cần x5 vật phẩm t72')
    assert meta['text']=='◈1◈h cần x◈2◈ vật phẩm t◈3◈'


def test_restore_template_repairs_spreadsheet_merged_placeholders():
    meta=token_template('A<enter><c=yel>B<c><enter><c=water>C<c>')
    translated='甲{P1}{P2}乙{P3}{P4P5}丙{P6}'
    restored,error=restore_template(translated,meta)
    assert error==''
    assert restored=='甲<enter><c=yel>乙<c><enter><c=water>丙<c>'


def test_restore_template_repairs_text_absorbed_into_placeholder():
    meta=token_template('Nhấn <c=yel>vào đây<c>')
    restored,error=restore_template('按{P1点击}{P2}',meta)
    assert error=='' and restored=='按<c=yel>点击<c>'


def test_restore_template_rejects_lost_placeholder():
    meta=token_template('Nhận %s vật phẩm {ITEM_ID}')
    restored,error=restore_template('获得 {P1} 道具',meta)
    assert restored is None and error=='missing placeholders: {P2}'

if __name__=='__main__':
    test_tcvn_sample(); test_mixed_decode(); test_mixed_gbk_tcvn_line(); test_resource_path_filter(); print('OK')


def test_parallel_worker_policy():
    import parallel_config
    old = parallel_config.os.cpu_count
    try:
        for cpus, expected in [(2,1),(4,3),(8,6),(16,12),(32,16)]:
            parallel_config.os.cpu_count=lambda c=cpus:c
            w=parallel_config.worker_count()
            assert w==expected, (cpus,w,expected)
            assert w < cpus*0.8 or cpus==1
    finally:
        parallel_config.os.cpu_count=old


def test_dollar_vietnamese_label_uses_placeholder_and_is_restored():
    meta = token_template('$Kênh hệ thống')
    assert meta['text'] == '◈1◈Kênh hệ thống'
    assert meta['dollar_spaced'] is False
    restored, error = restore_template('◈1◈系统频道', meta)
    assert error == ''
    assert restored == '$系统频道'
    variable = token_template('$PLAYER_ID')
    assert variable['dollar_spaced'] is False


def test_at_dollar_hash_are_always_placeholder_protected():
    meta = token_template('Nhận @ vật phẩm $ và # nhiệm vụ')
    assert meta['text'] == 'Nhận ◈1◈ vật phẩm ◈2◈ và ◈3◈ nhiệm vụ'
    restored, error = restore_template('获得◈1◈物品◈2◈和◈3◈任务', meta)
    assert error == ''
    assert restored == '获得@物品$和#任务'
