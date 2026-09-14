# -*- coding: utf-8 -*-
"""把我直接翻译的 _zh_batch*.json 译文合并写回 XLSX"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import translate_xlsx_export as t

xlsx = Path(r'..\paks++\ui\xlsx_export\ui\ui_localization.xlsx')
bak = Path(r'..\paks++\ui\xlsx_export\ui\ui_localization.xlsx.bak')

rows = t.read_xlsx_export(bak)

# 收集所有译文
zh = {}
d = xlsx.parent
for f in sorted(d.glob('_zh_batch*.json')):
    data = json.loads(f.read_text(encoding='utf-8'))
    zh.update(data)

print(f'累计译文: {len(zh)} 条')

new_rows = []
missing = 0
for r in rows:
    rid = r['id']
    text = zh.get(rid)
    if text:
        new_rows.append({'id': rid, 'text': text})
    else:
        new_rows.append({'id': rid, 'text': r['text']})
        missing += 1

print(f'总行数: {len(rows)} | 已译: {len(rows)-missing} | 未译(保留原文): {missing}')

t.write_xlsx_export(xlsx, new_rows)
print(f'写回完成: {xlsx}')