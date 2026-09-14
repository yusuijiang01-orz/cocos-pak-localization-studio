# -*- coding: utf-8 -*-
"""把 xlsx_export 的 XLSX 导出为 JSONL 纯文本，便于直接读取翻译"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import translate_xlsx_export as t

xlsx = Path(r'..\paks++\ui\xlsx_export\ui\ui_localization.xlsx.bak')
rows = t.read_xlsx_export(xlsx)

out = Path(r'..\paks++\ui\xlsx_export\ui\_pending.jsonl')
with out.open('w', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

print(f'导出 {len(rows)} 条 -> {out}')

# 统计字符量
total = sum(len(r['text']) for r in rows)
print(f'总字符数: {total:,}')