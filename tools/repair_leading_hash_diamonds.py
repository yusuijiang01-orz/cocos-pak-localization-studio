#!/usr/bin/env python3
import json, shutil
from datetime import datetime
from pathlib import Path

p=Path(__file__).resolve().parents[1]/'paks++'/'localization'/'text_records.json'
records=json.loads(p.read_text(encoding='utf-8')); changed=0
for r in records:
    src=str(r.get('source_original','')); cur=str(r.get('original',''))
    if src.startswith('#') and cur.startswith('◈') and not cur.startswith('◈1◈'):
        fixed='#'+cur[1:]
        r['original']=fixed
        if r.get('translation'): r['translation']=fixed
        changed+=1
stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
backup=p.with_name(f'{p.name}.before_leading_hash_repair_{stamp}')
shutil.copy2(p,backup)
p.write_text(json.dumps(records,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print(json.dumps({'changed':changed,'backup':str(backup)},ensure_ascii=True))
