# -*- coding: utf-8 -*-
"""把 api_translator.py 里最新的 CORE_GLOSSARY 同步到用户配置所有 profile 的 prompt 术语表段。"""
import ast, json, re, os

ROOT = os.path.dirname(os.path.abspath(__file__))
API_TRANS = os.path.join(ROOT, "api_translator.py")
CFG = r"C:\Users\admin\AppData\Roaming\cocos-pak-localization-studio\api-translator-config.json"

START = "核心术语表（全文必须严格遵守，保证术语统一，不得另译或音译替换）：\n"
END = "\n\n翻译要求："

src = open(API_TRANS, encoding="utf-8").read()
m = re.search(r"CORE_GLOSSARY = \((.*?)\)\n\n", src, re.S)
assert m, "cannot find CORE_GLOSSARY block"
glossary = ast.literal_eval("(" + m.group(1) + ")")

data = json.load(open(CFG, encoding="utf-8"))
changed = 0
for p in data.get("profiles", []):
    pr = p.get("prompt", "")
    if START in pr and END in pr:
        prefix = pr.split(START)[0] + START
        suffix = END + pr.split(END, 1)[1]
        new_pr = prefix + glossary + suffix
        if new_pr != pr:
            p["prompt"] = new_pr
            changed += 1

json.dump(data, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("patched %d profiles; glossary_len=%d" % (changed, len(glossary)))