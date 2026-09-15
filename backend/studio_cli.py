#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, os, shutil, sys, traceback
from pathlib import Path
from pak_core import extract_one
from localization_analyzer import analyze_folder, write_outputs, normalize_text_resource_utf8
from tabular_converter import convert_tsv_tree
from tsv_localization import (
    export_tsv_localization, import_tsv_localization, apply_csv_to_records,
    export_text_resource_localization, import_text_resource_localization, apply_master_csv_to_records,
)
from script_translator import translate_csv_tree
from pak_builder import build_from_modified_dir, build_from_workspace, materialize_records_to_modified_dir
from resource_structure_audit import write_audit
from parallel_config import worker_count, logical_cpu_count, lower_process_priority, limit_process_cpu_affinity
from localization_tm import (
    seed_db, batch_translate, learn_record, learn_modified, stats,
    prepare_queue, queue_stats, queue_list, apply_tm_queue_batch,
)
from local_model import model_status, local_translate_queue_batch, model_translate_csv_tree
from merged_csv import (
    merge_csv_tree, merge_remaining_csv_tree, split_merged_csv,
    prepare_api_localization_package, apply_api_localization_package,
    prepare_api_localization_file, apply_api_localization_file,
)
from api_translator import fetch_models, translate_merged_csv, translate_csv_dir
from api_reviewer import review_records
from xlsx_localization import export_xlsx_mapping, export_xlsx_file_queue, export_full_xlsx, export_multi_pak_full_xlsx, export_multi_pak_glossary_xlsx, import_xlsx_to_modified, apply_xlsx_to_records, apply_multi_pak_full_xlsx_to_records, apply_xlsx_folder_to_records, restore_records_from_materialize_report
from export_untranslated_xlsx import run as export_untranslated_run
from import_untranslated_xlsx import run as import_untranslated_run
from ollama_batch_translate import run as ollama_translate_run, run_folder as ollama_translate_folder_run
from safe_pc_merge import run as safe_pc_merge_run

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

def emit(obj):
    print(json.dumps(obj,ensure_ascii=False),flush=True)


UTF8_NORMALIZE_SUFFIXES={'.tsv','.ini','.txt','.csv','.lua','.xml'}

def normalize_tree_utf8(root:Path):
    root=Path(root)
    changed=[]
    invalid=[]
    scanned=0
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in UTF8_NORMALIZE_SUFFIXES:
            continue
        scanned+=1
        before=p.read_bytes()
        after=normalize_text_resource_utf8(before,p.suffix.lower())
        if after!=before:
            p.write_bytes(after)
            changed.append(str(p.relative_to(root)))
        try:
            after.decode('utf-8')
        except UnicodeDecodeError as exc:
            invalid.append({'file':str(p.relative_to(root)),'offset':exc.start,'reason':str(exc)})
    report={'root':str(root),'encoding':'utf-8','scanned':scanned,'changed':len(changed),'changed_files':changed[:200],'strict_utf8_failures':len(invalid),'invalid_files':invalid[:100]}
    (root/'_utf8_normalize_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if invalid:
        examples=' | '.join(f"{item['file']}@{item['offset']}" for item in invalid[:20])
        raise ValueError(f'UTF-8 分析副本生成失败，已阻止分析和导出：{len(invalid)} 个文件仍含旧编码字节：{examples}')
    return report

def refresh_records_from_sources(records_path:Path,pak_sources:list[tuple[str,Path]]):
    """Add newly recognized visible records without overwriting existing edits."""
    records_path=Path(records_path)
    records=json.loads(records_path.read_text(encoding='utf-8'))
    by_location={
        (str(r.get('pak') or ''),str(r.get('source_file') or ''),int(r.get('line') or 0),int(r.get('column') or 1)):r
        for r in records
    }
    added=[]; scanned=0; per_pak={}
    for pak,folder in pak_sources:
        fresh,_stats=analyze_folder(Path(folder),pak,workers=worker_count())
        scanned+=len(fresh); pak_added=0
        for rec in fresh:
            key=(pak,str(rec.get('source_file') or ''),int(rec.get('line') or 0),int(rec.get('column') or 1))
            if key in by_location:
                existing=by_location[key]
                existing['_isPlayerVisible']=True
                existing.setdefault('source_original',existing.get('original',''))
                continue
            rec['source_original']=rec.get('original','')
            rec['_isPlayerVisible']=True
            records.append(rec);by_location[key]=rec;added.append(rec);pak_added+=1
        per_pak[pak]={'scanned':len(fresh),'added':pak_added}
    backup=''
    if added:
        candidate=records_path.with_name(records_path.name+'.before_visibility_refresh');suffix=0
        while candidate.exists():
            suffix+=1;candidate=records_path.with_name(records_path.name+f'.before_visibility_refresh.{suffix}')
        shutil.copy2(records_path,candidate);backup=str(candidate)
        records_path.write_text(json.dumps(records,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    return {'records_path':str(records_path),'records':len(records),'scanned':scanned,'added':len(added),'per_pak':per_pak,'backup':backup}

def import_paks(workspace:Path,paks:list[Path]):
    lower_process_priority()
    workers=worker_count()
    emit({'event':'progress','message':f'并行模式：{workers} workers / {logical_cpu_count()} logical CPUs（保留 4 个逻辑核心）'})
    workspace.mkdir(parents=True,exist_ok=True)
    extracted_root=workspace/'extracted'; extracted_root.mkdir(exist_ok=True)
    raw_root=workspace/'_raw_reference'; raw_root.mkdir(exist_ok=True)
    all_records=[]; aggregate={}
    imported=[]
    for pak in paks:
        emit({'event':'progress','message':f'正在解包 {pak.name}...'})
        originals_root=workspace/'original_paks'
        originals_root.mkdir(exist_ok=True)
        original_copy=originals_root/pak.name
        shutil.copy2(pak,original_copy)
        raw_target=raw_root/pak.stem
        target=extracted_root/pak.stem
        if raw_target.exists(): shutil.rmtree(raw_target)
        if target.exists(): shutil.rmtree(target)
        out,count,ok,fail,methods,types=extract_one(original_copy,raw_target,workers=workers)
        shutil.copytree(raw_target,target)
        utf8_report=normalize_tree_utf8(target)
        emit({'event':'progress','message':f'正在分析 {pak.name} 文本...'})
        records,stats=analyze_folder(target,pak.name,workers=workers)
        all_records.extend(records)
        aggregate[pak.name]={'entries':count,'success':ok,'failed':fail,'methods':methods,'types':types,'workers':workers,'text_stats':dict(stats),'utf8_normalize':utf8_report}
        imported.append({'pak':pak.name,'path':str(original_copy),'source_path':str(pak),'extracted':str(target),'raw_reference':str(raw_target),'entries':count,'success':ok,'failed':fail})
    report=write_outputs(workspace/'localization',all_records,aggregate)
    audit_path=workspace/'localization'/'resource_structure_audit.json'
    structure_audit=write_audit(extracted_root,audit_path)
    report['structure_audit']={
        'path':str(audit_path),
        'files':structure_audit['files'],
        'default':structure_audit['default'],
        'suffix_summary':structure_audit['suffix_summary'],
    }
    project={'version':'3A-HF4','workers':workers,'logical_cpus':logical_cpu_count(),'workspace':str(workspace),'paks':imported,'record_count':len(all_records),'report':report}
    (workspace/'project.json').write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding='utf-8')
    emit({'event':'done','project':project,'records_path':str(workspace/'localization'/'text_records.json')})
    return 0

def main(argv):
    lower_process_priority()
    limit_process_cpu_affinity()
    if len(argv)<2:
        emit({'event':'error','message':'Missing command'}); return 2
    try:
        cmd=argv[1]
        if cmd=='import':
            if len(argv)<4: raise ValueError('Usage: studio_cli.py import <workspace> <pak...>')
            return import_paks(Path(argv[2]),[Path(x) for x in argv[3:]])
        if cmd=='convert-tsv':
            if len(argv)!=4: raise ValueError('Usage: studio_cli.py convert-tsv <extracted_dir> <output_dir>')
            report=convert_tsv_tree(Path(argv[2]),Path(argv[3]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='normalize-utf8':
            if len(argv)!=3: raise ValueError('Usage: studio_cli.py normalize-utf8 <text_resource_dir>')
            report=normalize_tree_utf8(Path(argv[2]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-tsv-csv':
            if len(argv) not in (4,5): raise ValueError('Usage: studio_cli.py export-tsv-csv <extracted_dir> <csv_dir> [pak_name]')
            pak_name=argv[4] if len(argv)==5 else 'updatefs.pak'
            report=export_tsv_localization(Path(argv[2]),Path(argv[3]),pak_name,workers=worker_count())
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-text-csv':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py export-text-csv <extracted_dir> <output_dir> <base_name> <pak_name>')
            report=export_text_resource_localization(Path(argv[2]),Path(argv[3]),argv[4],argv[5],workers=worker_count())
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-xlsx':
            if len(argv) not in (6,7): raise ValueError('Usage: studio_cli.py export-xlsx <extracted_dir> <output_dir> <base_name> <pak_name> [records_json]')
            emit({'event':'progress','message':'正在扫描文本资源并生成 xlsx 映射…'})
            records_path=Path(argv[6]) if len(argv)==7 else None
            report=export_xlsx_mapping(Path(argv[2]),Path(argv[3]),argv[4],argv[5],workers=worker_count(),records_path=records_path)
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-xlsx-files':
            if len(argv) not in (5,6,7): raise ValueError('Usage: studio_cli.py export-xlsx-files <extracted_dir> <xlsx_dir> <pak_name> [records_json] [config_dir]')
            records_path=Path(argv[5]) if len(argv)==6 else None
            if len(argv)==7:
                records_path=Path(argv[5])
            report=export_xlsx_file_queue(Path(argv[2]),Path(argv[3]),argv[4],workers=worker_count(),records_path=records_path,progress=lambda p:emit({'event':'progress',**p}),metadata_dir=Path(argv[6]) if len(argv)==7 else None)
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-xlsx-full':
            if len(argv)!=8: raise ValueError('Usage: studio_cli.py export-xlsx-full <extracted_dir> <xlsx_dir> <base_name> <pak_name> <records_json> <config_dir>')
            emit({'event':'progress','message':'正在把全部玩家可见文本合并为一个 XLSX…'})
            report=export_full_xlsx(Path(argv[2]),Path(argv[3]),argv[4],argv[5],Path(argv[6]),metadata_dir=Path(argv[7]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-xlsx-multi-full':
            if len(argv)<7: raise ValueError('Usage: studio_cli.py export-xlsx-multi-full <records_json> <xlsx_dir> <base_name> <config_dir> [pak_name ...]')
            emit({'event':'progress','message':'正在把多个 PAK 的玩家可见文本合并为一个免占位符 XLSX…'})
            report=export_multi_pak_full_xlsx(Path(argv[2]),Path(argv[3]),argv[4],metadata_dir=Path(argv[5]),pak_names=argv[6:])
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-xlsx-multi-remaining':
            if len(argv)<7: raise ValueError('Usage: studio_cli.py export-xlsx-multi-remaining <records_json> <xlsx_dir> <base_name> <config_dir> [pak_name ...]')
            emit({'event':'progress','message':'正在生成多个 PAK 的剩余越南文去重 XLSX…'})
            report=export_multi_pak_full_xlsx(Path(argv[2]),Path(argv[3]),argv[4],metadata_dir=Path(argv[5]),pak_names=argv[6:],remaining_only=True)
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-xlsx-multi-glossary':
            if len(argv)<7: raise ValueError('Usage: studio_cli.py export-xlsx-multi-glossary <records_json> <xlsx_dir> <base_name> <config_dir> [pak_name ...]')
            emit({'event':'progress','message':'正在把多个 PAK 的安全可见词语导出为术语库 XLSX…'})
            report=export_multi_pak_glossary_xlsx(Path(argv[2]),Path(argv[3]),argv[4],metadata_dir=Path(argv[5]),pak_names=argv[6:])
            emit({'event':'done','report':report})
            return 0
        if cmd=='refresh-visible-records':
            if len(argv)<5 or (len(argv)-3)%2: raise ValueError('Usage: studio_cli.py refresh-visible-records <records_json> <pak_name> <extracted_dir> [pak_name extracted_dir ...]')
            pairs=[(argv[i],Path(argv[i+1])) for i in range(3,len(argv),2)]
            emit({'event':'progress','message':'正在增量扫描并补齐遗漏的玩家可见文本…'})
            report=refresh_records_from_sources(Path(argv[2]),pairs)
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='import-tsv-csv':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py import-tsv-csv <extracted_dir> <csv_dir> <out_dir>')
            report=import_tsv_localization(Path(argv[2]),Path(argv[3]),Path(argv[4]),workers=worker_count())
            emit({'event':'done','report':report})
            return 0
        if cmd=='merge-tsv-csv':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py merge-tsv-csv <input_csv_dir> <merged_csv> <mapping_json>')
            report=merge_csv_tree(Path(argv[2]),Path(argv[3]),Path(argv[4]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='merge-remaining-tsv-csv':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py merge-remaining-tsv-csv <input_csv_dir> <checkpoint_json> <merged_csv> <mapping_json>')
            report=merge_remaining_csv_tree(Path(argv[2]),Path(argv[3]),Path(argv[4]),Path(argv[5]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='split-merged-tsv-csv':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py split-merged-tsv-csv <merged_csv> <mapping_json> <output_csv_dir>')
            report=split_merged_csv(Path(argv[2]),Path(argv[3]),Path(argv[4]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='api-fetch-models':
            if len(argv)!=4: raise ValueError('Usage: studio_cli.py api-fetch-models <base_url> <api_key>')
            emit({'event':'done','result':{'models':fetch_models(argv[2],argv[3])}})
            return 0
        if cmd=='api-translate-merged-csv':
            if len(argv) not in (9,10): raise ValueError('Usage: studio_cli.py api-translate-merged-csv <input_csv> <output_csv> <base_url> <api_key> <model> <prompt_file> <batch_size> [db_path]')
            prompt=Path(argv[7]).read_text(encoding='utf-8') if Path(argv[7]).exists() else ''
            db=Path(argv[9]) if len(argv)==10 else None
            report=translate_merged_csv(Path(argv[2]),Path(argv[3]),argv[4],argv[5],argv[6],prompt,int(argv[8]),db_path=db,progress=lambda p:emit({'event':'progress',**p}))
            emit({'event':'done','report':report})
            return 0
        if cmd=='prepare-api-csv':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py prepare-api-csv <input_csv_dir> <package_dir> <base_name> <chunk_rows>')
            report=prepare_api_localization_package(Path(argv[2]),Path(argv[3]),argv[4],int(argv[5]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='prepare-api-localization-csv':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py prepare-api-localization-csv <input_csv> <package_dir> <base_name> <chunk_rows>')
            report=prepare_api_localization_file(Path(argv[2]),Path(argv[3]),argv[4],int(argv[5]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='api-translate-csv-dir':
            if len(argv) not in (9,10): raise ValueError('Usage: studio_cli.py api-translate-csv-dir <input_dir> <output_dir> <base_url> <api_key> <model> <prompt_file> <batch_size> [db_path]')
            prompt=Path(argv[7]).read_text(encoding='utf-8') if Path(argv[7]).exists() else ''
            db=Path(argv[9]) if len(argv)==10 else None
            report=translate_csv_dir(Path(argv[2]),Path(argv[3]),argv[4],argv[5],argv[6],prompt,int(argv[8]),db_path=db,progress=lambda p:emit({'event':'progress',**p}))
            emit({'event':'done','report':report})
            return 0
        if cmd=='apply-api-csv':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py apply-api-csv <package_dir> <translated_dir> <output_csv_dir>')
            report=apply_api_localization_package(Path(argv[2]),Path(argv[3]),Path(argv[4]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='apply-api-localization-csv':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py apply-api-localization-csv <package_dir> <translated_dir> <output_csv>')
            report=apply_api_localization_file(Path(argv[2]),Path(argv[3]),Path(argv[4]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='import-text-csv':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py import-text-csv <extracted_dir> <csv_path> <index_path> <out_dir>')
            report=import_text_resource_localization(Path(argv[2]),Path(argv[3]),Path(argv[4]),Path(argv[5]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='import-xlsx':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py import-xlsx <extracted_dir> <xlsx_path> <mapping_json> <out_dir>')
            emit({'event':'progress','phase':'xlsx-import','percent':1,'message':'正在读取译后 XLSX 和映射表…'})
            report=import_xlsx_to_modified(Path(argv[2]),Path(argv[3]),Path(argv[4]),Path(argv[5]),progress=lambda p:emit({'event':'progress',**p}))
            emit({'event':'done','report':report})
            return 0
        if cmd=='remap-xlsx':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py remap-xlsx <xlsx> <source_mapping> <target_mapping> <output_xlsx>')
            from xlsx_localization import remap_xlsx_by_source
            report=remap_xlsx_by_source(Path(argv[2]),Path(argv[3]),Path(argv[4]),Path(argv[5]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='recover-xlsx-from-modified':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py recover-xlsx-from-modified <translated_dir> <target_mapping> <output_xlsx>')
            from xlsx_localization import recover_xlsx_from_modified
            report=recover_xlsx_from_modified(Path(argv[2]),Path(argv[3]),Path(argv[4]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='apply-tsv-csv-records':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py apply-tsv-csv-records <records_json> <csv_dir> <pak_name>')
            report=apply_csv_to_records(Path(argv[2]),Path(argv[3]),argv[4])
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='apply-text-csv-records':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py apply-text-csv-records <records_json> <csv_path> <index_path> <pak_name>')
            report=apply_master_csv_to_records(Path(argv[2]),Path(argv[3]),Path(argv[4]),argv[5])
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='apply-xlsx-records':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py apply-xlsx-records <records_json> <xlsx_path> <mapping_json> <pak_name>')
            report=apply_xlsx_to_records(Path(argv[2]),Path(argv[3]),Path(argv[4]),argv[5],progress=lambda p:emit({'event':'progress',**p}))
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='apply-xlsx-multi-records':
            if len(argv)!=5: raise ValueError('Usage: studio_cli.py apply-xlsx-multi-records <records_json> <xlsx_path> <mapping_json>')
            report=apply_multi_pak_full_xlsx_to_records(Path(argv[2]),Path(argv[3]),Path(argv[4]),progress=lambda p:emit({'event':'progress',**p}))
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='apply-xlsx-folder-records':
            if len(argv) not in (5,6): raise ValueError('Usage: studio_cli.py apply-xlsx-folder-records <records_json> <xlsx_folder> <pak_name> [config_dir]')
            report=apply_xlsx_folder_to_records(Path(argv[2]),Path(argv[3]),argv[4],progress=lambda p:emit({'event':'progress',**p}),metadata_dir=Path(argv[5]) if len(argv)==6 else None)
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='restore-materialize-rejections':
            if len(argv)!=4: raise ValueError('Usage: studio_cli.py restore-materialize-rejections <records_json> <materialize_report_json>')
            report=restore_records_from_materialize_report(Path(argv[2]),Path(argv[3]))
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='api-review-records':
            if len(argv) not in (15,16): raise ValueError('Usage: studio_cli.py api-review-records <records_json> <pak_name> <output_dir> <base_url> <api_key> <model> <prompt_file> <batch_size> <db_path> <mode> <source_xlsx> <mapping_json> <extracted_dir> [selected_ids_json]')
            prompt=Path(argv[8]).read_text(encoding='utf-8') if Path(argv[8]).is_file() else ''
            force_ids=set(json.loads(Path(argv[15]).read_text(encoding='utf-8'))) if len(argv)==16 and Path(argv[15]).is_file() else set()
            report=review_records(Path(argv[2]),argv[3],Path(argv[4]),argv[5],argv[6],argv[7],prompt,int(argv[9]),Path(argv[10]),argv[11],Path(argv[12]),Path(argv[13]),Path(argv[14]),force_ids=force_ids,progress=lambda p:emit({'event':'progress',**p}))
            emit({'event':'done','report':report,'records_path':str(Path(argv[2]))})
            return 0
        if cmd=='translate-tsv-csv':
            if len(argv) not in (4,5,6): raise ValueError('Usage: studio_cli.py translate-tsv-csv <input_csv_dir> <output_csv_dir> [translate_py] [db_path]')
            script=Path(argv[4]) if len(argv)==5 else None
            if len(argv)==6:
                script=Path(argv[4])
                db=Path(argv[5])
            else:
                db=None
            report=translate_csv_tree(Path(argv[2]),Path(argv[3]),script,workers=worker_count(),db_path=db)
            emit({'event':'done','report':report})
            return 0
        if cmd=='build-pak':
            if len(argv) not in (6,7): raise ValueError('Usage: studio_cli.py build-pak <original_pak> <original_extracted_dir> <modified_dir> <output_pak> [--fast]')
            fast=len(argv)==7 and argv[6]=='--fast'
            report=build_from_modified_dir(Path(argv[2]),Path(argv[3]),Path(argv[4]),Path(argv[5]),workers=1,verify=not fast)
            emit({'event':'done','report':report})
            return 0
        if cmd=='materialize-records':
            if len(argv)!=6: raise ValueError('Usage: studio_cli.py materialize-records <extracted_dir> <records_json> <pak_name> <output_dir>')
            report=materialize_records_to_modified_dir(Path(argv[2]),Path(argv[3]),argv[4],Path(argv[5]))
            emit({'event':'done','report':report})
            return 0
        if cmd=='safe-pc-merge':
            if len(argv)<6: raise ValueError('Usage: studio_cli.py safe-pc-merge <pc_root> <workspace> <db_path> <pak> [manual_tsv ...]')
            report=safe_pc_merge_run(Path(argv[2]),Path(argv[3]),Path(argv[4]),argv[5],[Path(x) for x in argv[6:]],
                progress=lambda p:emit({'event':'progress','phase':'safe-pc-merge',**p}))
            emit({'event':'done','report':report})
            return 0
        if cmd=='export-untranslated-xlsx':
            if len(argv)!=4: raise ValueError('Usage: studio_cli.py export-untranslated-xlsx <workspace> <pak_or_all>')
            pak=argv[3] if argv[3].lower()!='all' else None
            emit({'event':'progress','phase':'ollama-export','percent':0,'message':'正在扫描并导出未翻译的越南文 XLSX（自动去重）…'})
            summary=export_untranslated_run(Path(argv[2]),pak)
            emit({'event':'done','report':summary})
            return 0
        if cmd=='ollama-translate':
            if len(argv) not in (4,5,6,7,8): raise ValueError('Usage: studio_cli.py ollama-translate <workspace> <pak> [max_buckets] [bucket_size] [xlsx] [control]')
            pak=argv[3]; max_buckets=int(argv[4]) if len(argv)>=5 else 0
            bucket_size=int(argv[5]) if len(argv)>=6 else None
            xlsx=Path(argv[6]) if len(argv)>=7 and argv[6] else None
            emit({'event':'progress','phase':'ollama','percent':0,'message':f'正在用本地 Ollama + 术语表批量翻译 {pak}…'})
            report=ollama_translate_run(Path(argv[2]),pak,max_buckets=max_buckets,bucket_size=bucket_size,xlsx=xlsx,
                control_path=Path(argv[7]) if len(argv)>7 else None,
                progress=lambda p:emit({'event':'progress','phase':'ollama',**p}))
            emit({'event':'done','report':report})
            return 0
        if cmd=='ollama-translate-folder':
            if len(argv) not in (5,6,7,8): raise ValueError('Usage: studio_cli.py ollama-translate-folder <workspace> <pak> <xlsx_folder> [bucket_size] [control] [config_dir]')
            report=ollama_translate_folder_run(Path(argv[2]),argv[3],Path(argv[4]),bucket_size=int(argv[5]) if len(argv)>=6 else None,control_path=Path(argv[6]) if len(argv)>=7 else None,metadata_dir=Path(argv[7]) if len(argv)>=8 else None,progress=lambda p:emit({'event':'progress','phase':'ollama',**p}))
            emit({'event':'done','report':report})
            return 0
        if cmd=='import-untranslated-xlsx':
            if len(argv) not in (4,5): raise ValueError('Usage: studio_cli.py import-untranslated-xlsx <workspace> <pak> [xlsx]')
            emit({'event':'progress','phase':'ollama-import','percent':0,'message':f'正在把译后 XLSX 导回 {argv[3]} 的 text_records.json…'})
            report=import_untranslated_run(Path(argv[2]),argv[3],Path(argv[4]) if len(argv)==5 else None)
            emit({'event':'done','report':report,'records_path':str(Path(argv[2])/'localization'/'text_records.json')})
            return 0
        if cmd=='seed-db':
            seed_db(Path(argv[2]))
            emit({'event':'done','result':stats(Path(argv[2]))})
            return 0
        if cmd=='batch-translate':
            ws=Path(argv[2]); pak=argv[3]; db=Path(argv[4])
            result=batch_translate(ws/'localization'/'text_records.json',pak,db)
            emit({'event':'done','result':result,'records_path':str(ws/'localization'/'text_records.json')})
            return 0
        if cmd=='learn-record':
            ws=Path(argv[2]); rid=argv[3]; db=Path(argv[4])
            result=learn_record(ws/'localization'/'text_records.json',rid,db)
            emit({'event':'done','result':result})
            return 0
        if cmd=='learn-modified':
            ws=Path(argv[2]); pak=argv[3]; db=Path(argv[4]); quality=argv[5] if len(argv)>5 else 'manual'
            result=learn_modified(ws/'localization'/'text_records.json',pak,db,quality)
            emit({'event':'done','result':result})
            return 0
        if cmd=='queue-prepare':
            ws=Path(argv[2]); pak=argv[3]; db=Path(argv[4])
            result=prepare_queue(ws/'localization'/'text_records.json',pak,db)
            emit({'event':'done','result':result})
            return 0
        if cmd=='queue-stats':
            emit({'event':'done','result':queue_stats(Path(argv[4]),argv[3])})
            return 0
        if cmd=='queue-list':
            status=argv[5] if len(argv)>5 else 'pending'
            limit=int(argv[6]) if len(argv)>6 else 500
            emit({'event':'done','result':queue_list(Path(argv[4]),argv[3],status,limit)})
            return 0
        if cmd=='queue-apply':
            ws=Path(argv[2]); pak=argv[3]; db=Path(argv[4]); batch=int(argv[5]) if len(argv)>5 else 200
            result=apply_tm_queue_batch(ws/'localization'/'text_records.json',pak,db,batch)
            emit({'event':'done','result':result,'records_path':str(ws/'localization'/'text_records.json')})
            return 0
        if cmd=='tm-stats':
            emit({'event':'done','result':stats(Path(argv[2]))})
            return 0
        if cmd=='model-status':
            emit({'event':'done','result':model_status(Path(argv[2]))})
            return 0
        if cmd=='model-translate-tsv-csv':
            if len(argv) not in (6,7,8): raise ValueError('Usage: studio_cli.py model-translate-tsv-csv <input_csv_dir> <output_csv_dir> <model_dir> <db_path> [translate.py] [batch_size]')
            script=None; batch=48
            if len(argv)==7:
                try: batch=int(argv[6])
                except ValueError: script=Path(argv[6])
            elif len(argv)==8:
                script=Path(argv[6]); batch=int(argv[7])
            result=model_translate_csv_tree(Path(argv[2]),Path(argv[3]),Path(argv[4]),Path(argv[5]),batch,lambda p:emit({'event':'progress',**p}),script)
            emit({'event':'done','report':result})
            return 0
        if cmd=='model-translate':
            ws=Path(argv[2]); pak=argv[3]; db=Path(argv[4]); model_dir=Path(argv[5]); batch=int(argv[6]) if len(argv)>6 else 24
            emit({'event':'progress','message':f'本地模型正在翻译 {pak}：最多 {batch} 个唯一文本…'})
            result=local_translate_queue_batch(ws/'localization'/'text_records.json',pak,db,model_dir,batch)
            emit({'event':'done','result':result,'records_path':str(ws/'localization'/'text_records.json')})
            return 0
        if cmd=='build':
            ws=Path(argv[2]); pak=argv[3]
            emit({'event':'progress','message':f'正在构建并验证 {pak}...'})
            result=build_from_workspace(ws,pak)
            emit({'event':'done','result':result})
            return 0
        raise ValueError(f'Unknown command: {cmd}')
    except Exception as e:
        emit({'event':'error','message':str(e),'trace':traceback.format_exc()}); return 1
if __name__=='__main__': raise SystemExit(main(sys.argv))
