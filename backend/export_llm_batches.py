#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export remaining untranslated strings as LLM batch files."""
import json, os, re, sys

def main():
    base = r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++\enhanced_migration"
    inp = os.path.join(base, "remaining_for_llm.json")
    out_dir = os.path.join(base, "llm_batches")
    os.makedirs(out_dir, exist_ok=True)

    d = json.load(open(inp, encoding="utf-8"))

    vi_chars = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
    code_re = re.compile(r'^(function |local |if |for |while |return |AI_|TASK_|instence_|gItem |SetTime|AddNormal|Msg2Cur|AddGlobal|WriteLog|TopMessage|\[)')

    vi_items = []
    for item in d["strings"]:
        s = item["source"].strip()
        if not s:
            continue
        if code_re.match(s):
            continue
        if not any(c in s.lower() for c in vi_chars):
            continue
        vi_items.append(item)

    total_batches = (len(vi_items) + 99) // 100
    print("Vietnamese text for LLM:", len(vi_items))
    print("Estimated batches (100/batch):", total_batches)

    # Export as batch JSON files
    batch_size = 100
    last_batch = 0
    for i in range(0, len(vi_items), batch_size):
        batch = vi_items[i:i + batch_size]
        batch_num = i // batch_size + 1
        last_batch = batch_num
        batch_data = {
            "batch": batch_num,
            "total_batches": total_batches,
            "items": [{"id": item["id"], "source": item["source"]} for item in batch],
        }
        path = os.path.join(out_dir, "batch_%04d.json" % batch_num)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch_data, f, ensure_ascii=False, indent=1)

    print("Exported %d batch files to %s" % (last_batch, out_dir))

    # Also export as single TSV for Studio import
    tsv_path = os.path.join(out_dir, "all_remaining.tsv")
    with open(tsv_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("id\tsource\n")
        for item in vi_items:
            s = item["source"].replace("\t", " ").replace("\n", " ").replace("\r", "")
            f.write("%d\t%s\n" % (item["id"], s))
    print("Also exported TSV: %s (%d rows)" % (tsv_path, len(vi_items)))

if __name__ == "__main__":
    main()
