#!/usr/bin/env python3
"""Repair diamond placeholders introduced by mixing two XLSX ID namespaces."""
from __future__ import annotations
import argparse, json, re, shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from xlsx_localization import _mapping_rows, _source_cell_meta, read_simple_xlsx, polish_text
from tsv_localization import restore_template, validate_translation

# The diamond is only an XLSX transport marker in this workflow.  Any diamond
# present in a target whose untouched source had none is import pollution,
# including Google's half-marker and repeated-diamond variants.
DIAMOND_RE = re.compile(r"◈", re.I)

def run(records_path: Path, pak: str, xlsx: Path, mapping_path: Path) -> dict:
    records=json.loads(records_path.read_text(encoding='utf-8'))
    mapping=json.loads(mapping_path.read_text(encoding='utf-8-sig'))
    rows=list(_mapping_rows(mapping))
    translations={}
    for row in read_simple_xlsx(xlsx):
        vals=row.get('_values') or []
        rid=str(row.get('id','') or (vals[0] if vals else '')).strip()
        text=row.get('text')
        if text is None: text=row.get('text_zh')
        if text is None and len(vals)>1: text=vals[1]
        if rid: translations[rid]=str(text or '')
    source_dir=Path(mapping.get('extracted_dir') or '')
    if not source_dir.is_dir():
        raise ValueError(f'映射表解包目录不存在：{source_dir}')
    by_loc={(str(r.get('source_file') or ''),int(r.get('line') or 0),int(r.get('column') or 0)):r
            for r in records if r.get('pak')==pak}
    bad={r.get('id') for r in records if r.get('pak')==pak and DIAMOND_RE.search(str(r.get('original','')))
         and not DIAMOND_RE.search(str(r.get('source_original','')))}
    recovered=reverted=0; reasons=Counter(); cache={}; touched=set()
    for xid,source,cells in rows:
        target=translations.get(str(xid),'')
        if target: target,_=polish_text(source,target,str(xid))
        for compact in cells:
            rel=compact.get('relative_path') or compact.get('source_file') or ''
            rec=by_loc.get((Path(rel).name,int(compact['row']),int(compact['column'])))
            if not rec or rec.get('id') not in bad: continue
            touched.add(rec.get('id'))
            try: meta=_source_cell_meta(source_dir,pak,compact,cache)
            except Exception as exc:
                reasons[f'meta: {exc}']+=1; continue
            restored,error=restore_template(target,meta) if target else (None,'empty translation')
            ok,why=validate_translation(meta.get('source',''),restored or '') if restored else (False,error)
            if restored and not error and ok and not DIAMOND_RE.search(restored):
                rec['original']=restored; rec['translation']=restored; rec['language']='zh'; rec['status']='已翻译'
                recovered+=1
            else:
                reasons[error or why or 'unusable translation']+=1
    # Any polluted record that cannot be associated safely with the full mapping
    # is reverted to its untouched Vietnamese source instead of being packed as a
    # plausible-looking but unrelated Chinese sentence.
    for rec in records:
        if rec.get('pak')!=pak or rec.get('id') not in bad: continue
        if DIAMOND_RE.search(str(rec.get('original',''))):
            src=str(rec.get('source_original',''))
            rec['original']=src; rec['translation']=''; rec['language']='vi'; rec['status']='未翻译'
            reverted+=1
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    backup=records_path.with_name(f'{records_path.name}.before_placeholder_repair_{pak[:-4]}_{stamp}')
    shutil.copy2(records_path,backup)
    records_path.write_text(json.dumps(records,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    remaining=sum(1 for r in records if r.get('pak')==pak and DIAMOND_RE.search(str(r.get('original','')))
                  and not DIAMOND_RE.search(str(r.get('source_original',''))))
    return {'pak':pak,'polluted_before':len(bad),'recovered_from_full_xlsx':recovered,
            'reverted_to_source':reverted,'remaining_pollution':remaining,'mapped_records':len(touched),
            'backup':str(backup),'reasons':dict(reasons)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('records',type=Path); ap.add_argument('pak')
    ap.add_argument('xlsx',type=Path); ap.add_argument('mapping',type=Path); a=ap.parse_args()
    print(json.dumps(run(a.records,a.pak,a.xlsx,a.mapping),ensure_ascii=True,indent=2))
if __name__=='__main__': main()
