#!/usr/bin/env python3
import json, sys, tempfile
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from pak_core import extract_one
from localization_analyzer import analyze_folder

def main():
    out={}
    with tempfile.TemporaryDirectory(prefix='pakloc_build_verify_') as td:
        for raw in sys.argv[1:]:
            pak=Path(raw); dest=Path(td)/pak.stem
            _o,count,ok,fail,_methods,_types=extract_one(pak,dest,workers=15)
            records,_stats=analyze_folder(dest,pak.name,workers=15)
            langs=Counter(str(r.get('language') or 'other') for r in records)
            diamonds=sum(1 for r in records if '◈' in str(r.get('original','')))
            out[pak.name]={'records':len(records),'languages':dict(langs),'diamond_records':diamonds,
                           'entries':count,'extract_ok':ok,'extract_failed':fail}
    print(json.dumps(out,ensure_ascii=True,indent=2))

if __name__=='__main__':
    main()
