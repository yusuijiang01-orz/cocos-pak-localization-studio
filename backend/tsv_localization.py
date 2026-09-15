#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, hashlib, json, re, shutil, sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from localization_analyzer import VI_WORDS, decode_best, encode_text_for_source, is_candidate_bytes, is_resource_reference, likely_translatable, score_text, is_localizable_text_path, is_visible_ini_key, is_visible_tsv_column
from parallel_config import worker_count
from lua_localization import iter_lua_text_parts, replace_lua_text_parts



# 占位符保护策略（按"更长/更具体优先"排序，避免短模式先吃掉长匹配的前缀）
# 顺序：HTML/富文本 → URL/URI → Windows/Unix 路径（绝对/相对/无扩展名深层目录）→
#       路径式资源引用（带扩展名白名单）→ printf/sformat → ${var}/$name/$func(args) →
#       {var} 模板 → @标识符 → 日期时间 → GUID/MD5/SHA/HEX长哈希 → 数字（等级/CD-KEY带单位）→
#       下划线 ID（字母/数字/中文，必须含 2+ 下划线或 2+ 大写）→ 常量宏 → 中文括号包裹变量 →
#       已替换的 ◈n◈ 菱形 → 单个 @/$/# 兜底 → 符号表情
PROTECTED_PATTERNS = [
    # 1) HTML/XML/Lua-rich-text 标签（含 <c=yellow>Tiến độ</c> 这种 Cocos 富文本彩色标签）
    r"</?[A-Za-z][A-Za-z0-9_:-]*(?:=[^>\r\n]*)?>",
    # 2) URL / URI（http(s) / ftp / file / custom scheme pak:// / tev:// 等，支持 query #anchor）
    r"(?:[A-Za-z][A-Za-z0-9+.\-]{0,15})://[^\s<>\"'）)】】\]》]+",
    # 3) 文件路径：
    #    (a) 绝对盘符 D:\\xxx 或 Unix /xxx，允许中文/空格/下划线，无扩展名也保护（\\script\\ui\\sub）
    #    (b) 目录片段 xxx\\yyy/zzz 至少 2 层分隔，或最后一节带扩展名
    r"(?:[A-Za-z]:)?[\\/][^\s<>\"':*?|\\/]+(?:[\\/][^\s<>\"':*?|\\/]+)+",
    # 4) 文件名带扩展名白名单（资源文件名未必带路径，单独保护）
    r"\b[A-Za-z0-9_.\-\u4e00-\u9fff]+\.(?:spr|bmp|png|jpe?g|gif|dds|tga|wav|mp3|ogg|mid|ani|cur|ico|ttf|fnt|otf|woff2?|ini|lua|txt|tsv|csv|xml|json|yml|yaml|pak|dat|bin|db|plist|csb|astc|pvr|pkm|ktx|mp4|webm)\b",
    # 5) printf / strftime / C# string.Format 占位符 %s %02d %-5.2f %.2f%% 等，以及 {0} {1} {name} 模板
    r"%%|%[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hljztL]*[sdifouxXeEFGgacpn]",
    r"\{[A-Za-z0-9_:.]+\}",
    # 6) ${var}  / $变量名 / $函数(参数列表) —— 参数允许逗号/引号/子占位符/数字/中文/嵌套括号 1 层
    r"\$\{[^{}\r\n]+\}",
    r"\$[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_.:\-\u4e00-\u9fff]*(?:\((?:[^()\r\n]*|\([^()\r\n]*\))*\))?(?![A-Za-z0-9_.:\-\u00c0-\u024f\u1e00-\u1eff\u4e00-\u9fff])",
    # 7) @符号（Lua/Cocos-Editor 引用，如 @%name 或 @id/xxx）
    r"@[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_.:/\-\u4e00-\u9fff]*",
    # 8) 日期 & 时间（例如 2026-08-29、2026/08/29、29/08/2026、10:30:55、10:30、活动结束 2026.08.29）
    r"\b\d{2,4}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:[日号])?(?:[\sT_]\d{1,2}[:：]\d{1,2}(?:[:：]\d{1,2})?)?\b",
    r"\b\d{1,2}:\d{1,2}(?::\d{1,2})?\b",
    # 9) GUID / UUID / 长 HEX（MD5 32 / SHA1 40 / SHA256 64 / 短资产 hash 如 12~31 位 hex / 0x 前缀）
    r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b",
    r"\b[0-9A-Fa-f]{12,128}\b",
    r"\b0[xX][0-9A-Fa-f]+\b",
    # 10) 数字 + 上下文（等级 Lv.LV/HM EXP VIP CD 秒/分/时 百分比 货币 伤害）
    #     例子：Lv.99 / VIP15 / EXP +1,234 / 5% / 999,999,999 金 / 30秒 / CD 12 小时 / 1.5倍
    r"(?<![\w\u4e00-\u9fff])(?:Lv\.?|LV\.?|VIP|EXP|HP|MP|ATK|DEF|CRI|EVA|ACC|CD|DMG|Q\d|S\d+|A\d|战力|等级|经验|金币|元宝|钻石|魂石|强化|秒|分|时|天|个月|年|倍|折|号|服|区|层|章|关|关卡|回合|波|排名|名次|排名|充值|消费|返利|折扣|CD-KEY|KEY|UID|ID)(?:\s*[:：=\-]?\s*)?[-+]?\d+(?:[.,]\d+)*(?:\s*[%‰万万亿亿])?(?![\w\u4e00-\u9fff])",
    # 10b) 所有数字兜底：包括 19h、x5、t72 等紧邻单位/字母的数值。
    # URL、路径、日期、哈希和完整标识符已由更靠前的长模式优先吞掉。
    r"[-+]?(?:\d{1,3}(?:[,， ]\d{3})+|\d+)(?:\.\d+)?%?",
    # 11) 下划线 ID（snake_case / CONST_CASE / 中文前缀_xxx 等）：必须 ≥2 个下划线或 ≥2 段大写，避免把"简单单词_HELLO"误抓
    r"\b[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*(?:_[A-Za-z0-9_\u4e00-\u9fff]+){2,}\b",
    r"\b[A-Z][A-Z0-9_]{3,}_[A-Z0-9_]+\b",
    r"\b_{2,}[A-Za-z0-9_\u4e00-\u9fff]+(?:_[A-Za-z0-9_\u4e00-\u9fff]+)*\b",
    # 12) 常量宏（全大写+下划线，必须 ≥4 字符，以避免把 URL / OK / HP / MP / EXP / CD 这种 2-3 字母的"通用缩写词"误抓；
    #     HP/MP/EXP/CD/UID/KEY 已经在前面的 #10 数字+上下文字典式规则里和数字一起被保护了，这里就不抓短宏了。）
    r"\b[A-Z][A-Z0-9_]{3,}\b",
    # INI runtime value selectors.  They must be captured as one token before
    # the generic number and single-# fallbacks split ``#d1+`` into pieces.
    r"#[A-Za-z]\d+[-+]?",
    # 13) 中文/全角括号包裹的"变量候选"整块保护：
    #     【…】〔…〕〖…〗《…》［…］（…）｛…｝<…> […] (…) {…}
    #     只抓以下 4 类"肯定是程序标记不是自然语言"的东西（防止把 (OB) 这种 2-3 字母缩写、《活动》《界面》这种自然文本误抓成占位符）：
    #       (a) $ 开头（Lua/LAPP 变量）
    #       (b) 数字 / ± / % 开头（数值区间 / 单位区间）
    #       (c) 含下划线 _ 或 点号 .（说明是标识符/路径/方法），并且前后至少 1 段字母/数字
    #       (d) 纯 1~2 位数字（占位符序号）
    r"(?:【|〔|〖|《|［|（|｛|<|\[|\(|\{)\s*(?:"
        r"\$[^\s】〕〗》］）｝>\]\)]{1,60}"
        r"|[-+%]?\d{1,3}(?:[.,， ]\d{3})*(?:\.\d+)?(?:\s*[%‰万亿年月号服区层章关回合波排名秒分时天个只件倍折]*)?"
        r"|[_A-Za-z0-9\u4e00-\u9fff]+(?:_[_A-Za-z0-9\u4e00-\u9fff]+)+"
        r"|[_A-Za-z0-9\u4e00-\u9fff]+\.[_A-Za-z0-9\u4e00-\u9fff.]+"
        r"|\d{1,2}"
    r")\s*(?:】|〕|〗|》|］|）|｝|>|\]|\)|\})",
    # 14) 已经被前面轮替换过的菱形占位符 ◈n◈（保留其内部空格/P 以防二次扫描）
    r"◈\s*(?:P\s*)?\d+\s*◈",
    # 15) 单个 @/$/# 兜底。较长的变量和引用仍由前面的规则整体保护。
    r"[@$#]",
    # 16) 表情/符号/富文本装饰符（规则里明确列出的全部保留）
    r"[♥♣♦♠★☆●■▲◆※◎◇□△▽◁▷◈✦✧☀☁⚔⚡⚘❄❀♪♫☎✉✓✔✕✖❤♡✔✘©®™℃℉‰→←↑↓⇧⇩⇦⇨▪•◘▫▬▲▼◄►◊○◐◑◒◓◔◕✪✫✬✭✮✯]+",
]
PROTECTED_RE = re.compile("|".join(f"(?:{p})" for p in PROTECTED_PATTERNS), re.UNICODE)
PLACEHOLDER_RE = re.compile(r"\{P(\d+)\}")
PLACEHOLDER_GROUP_RE = re.compile(r"\{((?:P\d+){2,})\}")
PLACEHOLDER_DAMAGED_RE = re.compile(r"\{(P\d+)([^\d{}][^{}]*)\}")
# 菱形占位符：◈1◈ / ◈1 ◈ / ◈ P1 ◈ / ◈P 01 ◈ —— 都是合法（翻译员可能加空格）
SAFE_PLACEHOLDER_RE = re.compile(r"◈\s*(?:P\s*)?(\d+)\s*◈", re.I | re.UNICODE)
# 括号占位符，分为 (A) 显式带 P：《P1》(P2)【P3】[P4]<P5>… 和 (B) 纯数字：《1》(2)【3】[4]<5>…〔6〕〖7］［8］｛9｝
ALTERED_PLACEHOLDER_P_RE = re.compile(
    r"(?:《|〈|（|｟|〘|〖|〔|【|［|｛|<|\[|\(|\{)\s*P\s*(\d+)\s*(?:》|〉|）|｠|〙|〗|〕|】|］|｝|>|\]|\)|\})",
    re.I | re.UNICODE,
)
ALTERED_PLACEHOLDER_NUM_RE = re.compile(
    r"(?:《|〈|（|｟|〘|〖|〔|【|［|｛|<|\[|\(|\{)\s*0*(\d+)\s*(?:》|〉|）|｠|〙|〗|〕|】|］|｝|>|\]|\)|\})",
    re.UNICODE,
)
# 单独的 {1} {02} 大括号数字占位符（常见 MT 直接把 P 吃掉变成纯数字花括号）
BRACED_NUM_RE = re.compile(r"\{\s*0*(\d+)\s*\}")
# 单独裸露的 ◈ 菱形数字（没闭合，翻译手误只画了左半边 / 右半边）→ 尽量救。
# Python 3.11 的 re lookbehind 必须固定宽度，所以拆成两个独立的 regex（分别在 normalize 阶段单独调用）。
LOOSE_DIAMOND_LEFT_RE = re.compile(r"◈\s*(?:P\s*)?0*(\d+)(?=\s*[^◈\d]|$)", re.I | re.UNICODE)   # ◈N 后面没有 ◈ 闭
LOOSE_DIAMOND_RIGHT_RE = re.compile(r"(?:^|(?<=[^◈\d]))\s*(?:P\s*)?0*(\d+)\s*◈", re.I | re.UNICODE)  # N◈ 前面没有 ◈ 开（通过上下文判断）
COLOR_TAG_RE = re.compile(r"</?(?:c|color)(?:=[^>\r\n]*)?>", re.I)
UTF8_AS_GBK_MOJIBAKE_RE = re.compile(
    r"(?:鈼[?堚]?|浠欑骇|娣峰厓|堟槦|鐜勬|琛ｆ|鏈濆|啗涓|瑙佺|閾犵|敳韬|榛戣|鎴戠|鐨|涓[\u4e00-\u9fff]|鍙[\u4e00-\u9fff])"
)
NUMBERED_KV_PREFIX_RE = re.compile(r"^(\s*-?\d+\s*=)")
LOCALIZABLE_SUFFIXES = {".tsv", ".ini", ".txt", ".lua"}
KV_RE = re.compile(rb"^([^=\r\n]{1,120}=\s*)(.+)$")


def protected_tokens(text: str) -> list[str]:
    seen = set()
    out = []
    for m in PROTECTED_RE.finditer(text or ""):
        token = m.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def validate_translation(source: str, target: str) -> tuple[bool, str]:
    target = str(target or "")
    if UTF8_AS_GBK_MOJIBAKE_RE.search(target):
        return False, "疑似 UTF-8 被按 GBK 解码后的乱码"
    source_prefix = NUMBERED_KV_PREFIX_RE.match(str(source or ""))
    if source_prefix and not target.startswith(source_prefix.group(1)):
        return False, f"编号键值前缀被改变: {source_prefix.group(1)!r}"
    def hard_runtime_token(token: str) -> bool:
        value = str(token or "").strip()
        # Numbers and localized level notation are translatable content, not
        # runtime identifiers. Structural validation still protects printf,
        # template variables, paths and rich-text tags separately.
        if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*%?", value):
            return False
        if re.fullmatch(r"(?:Lv\.?\s*\d+|\(N\.v\)|\(\d+\))", value, re.I):
            return False
        return True

    missing = [t for t in protected_tokens(source) if hard_runtime_token(t) and t not in target]
    if missing:
        return False, "missing protected tokens: " + " | ".join(missing[:8])
    return True, ""


def contains_cjk_text(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff\uf900-\ufaff]", str(text or "")))


def strip_color_tags_for_cjk(source: str, target: str) -> str:
    """Drop rich-text color tags around Chinese text for clients that hide colored CJK."""
    if not COLOR_TAG_RE.search(str(source or "")):
        return target
    if not contains_cjk_text(target):
        return target
    return COLOR_TAG_RE.sub("", str(target or ""))


def token_template(text: str) -> dict:
    source_text = str(text or "")
    tokens = []
    parts = []
    pos = 0
    for match in PROTECTED_RE.finditer(text or ""):
        if match.start() > pos:
            parts.append(text[pos:match.start()])
        key = f"P{len(tokens) + 1}"
        tokens.append({"key": key, "value": match.group(0)})
        parts.append("{" + key + "}")
        pos = match.end()
    parts.append(text[pos:])
    masked = "".join(parts)
    template = masked.replace("{", "{{").replace("}", "}}")
    for token in tokens:
        template = template.replace("{{" + token["key"] + "}}", "{" + token["key"] + "}")
    if tokens:
        inner = masked
        prefix = []
        suffix = []
        while prefix or inner.startswith("{P"):
            m = re.match(r"^\{P\d+\}", inner)
            if not m:
                break
            prefix.append(m.group(0))
            inner = inner[m.end():]
        while suffix or re.search(r"\{P\d+\}$", inner):
            m = re.search(r"\{P\d+\}$", inner)
            if not m:
                break
            suffix.insert(0, m.group(0))
            inner = inner[:m.start()]
        token_values = {"{" + token["key"] + "}": token["value"] for token in tokens}
        boundary = prefix + suffix
        has_required_symbol = any(token_values.get(marker) in ("@", "$", "#") for marker in boundary)
        if inner and not PLACEHOLDER_RE.search(inner) and not has_required_symbol:
            masked = inner
            template = "".join(prefix) + "{TEXT}" + "".join(suffix)
    # Curly placeholders are frequently rewritten by spreadsheet translators
    # (for example {P1} -> 《P1》).  The diamond form is deliberately simple,
    # language-neutral, and already part of the translator preservation rules.
    export_text = PLACEHOLDER_RE.sub(lambda m: f"◈{m.group(1)}◈", masked)
    return {"text": export_text, "tokens": tokens, "template": template, "dollar_spaced": False}


def normalize_placeholder_markers(translated: str, tokens: dict[str, str]) -> str:
    # Spreadsheet translators commonly merge adjacent protected markers, e.g.
    # ``{P6}{P7}`` -> ``{P6P7}``, or absorb following text into the braces,
    # e.g. ``{P5}点击`` -> ``{P5点击}``.  Normalize those deterministic forms
    # before restoring the original resource tokens.
    def canonical(number: str, original: str) -> str:
        key = "P" + str(int(number))  # int(...) 去掉前导零
        return "{" + key + "}" if key in tokens else original

    text = str(translated or "")
    # 1) 菱形 ◈N◈ / ◈P N ◈（优先处理，因为它是我们自己的正式输出格式）
    text = SAFE_PLACEHOLDER_RE.sub(lambda m: canonical(m.group(1), m.group(0)), text)
    # 2) 带 P 的变形括号：《P1》(P2)【P3】…
    text = ALTERED_PLACEHOLDER_P_RE.sub(lambda m: canonical(m.group(1), m.group(0)), text)
    # 3) 纯数字变形括号：《1》(2)【3】[4]<5>〔6〕〖7］［8］｛9｝（机器翻译最喜欢把 P 吃掉）
    text = ALTERED_PLACEHOLDER_NUM_RE.sub(lambda m: canonical(m.group(1), m.group(0)), text)
    # 4) {1} / {02} 纯数字大括号（也是 MT 常见"去掉 P"）
    text = BRACED_NUM_RE.sub(lambda m: canonical(m.group(1), m.group(0)), text)
    # 5) 松散半边菱形：◈N  或  N◈ —— 手误只画了一边（拆 2 条分别跑，绕开 Python 3.11 定宽 lookbehind）
    text = LOOSE_DIAMOND_LEFT_RE.sub(lambda m: canonical(m.group(1), m.group(0)), text)
    text = LOOSE_DIAMOND_RIGHT_RE.sub(lambda m: canonical(m.group(1), m.group(0)), text)
    # 6) 老式 {P6P7} / {P5汉字} 损坏吸收（保持原逻辑，兜底）
    text = PLACEHOLDER_GROUP_RE.sub(
        lambda m: "".join("{" + k + "}" for k in re.findall(r"P\d+", m.group(1))),
        text,
    )
    text = PLACEHOLDER_DAMAGED_RE.sub(
        lambda m: ("{" + m.group(1) + "}" + m.group(2))
        if m.group(1) in tokens
        else m.group(0),
        text,
    )
    return text


def restore_template(translated: str, meta: dict) -> tuple[str | None, str]:
    tokens = {x["key"]: x["value"] for x in meta.get("tokens", [])}
    template = meta.get("template") or "{TEXT}"
    if meta.get("dollar_spaced"):
        translated = re.sub(r"^\s*\$\s*", "", str(translated or ""), count=1)
    translated = normalize_placeholder_markers(translated, tokens)
    if "{TEXT}" in template:
        result = template.replace("{TEXT}", translated)
    else:
        result = translated
    absent = ["{" + key + "}" for key in tokens if "{" + key + "}" not in result]
    if absent:
        return None, "missing placeholders: " + " | ".join(absent[:8])
    missing = []

    def repl(match):
        key = "P" + match.group(1)
        value = tokens.get(key)
        if value is None:
            missing.append("{" + key + "}")
            return match.group(0)
        return value

    result = PLACEHOLDER_RE.sub(repl, result)
    if missing:
        return None, "unknown placeholders: " + " | ".join(missing)

    # ===== 二次兜底：如果 tokens 里有「按数字键」还没被还原到 result 的，
    #       就把 result 里所有 (数字) / 《数字》 / 【数字】 / ◈数字◈ / {数字}
    #       再按数字序号再替换一次，防止前面的正则没覆盖到 MT 新发明的花样。
    required = [("{" + k + "}", v) for k, v in tokens.items()]
    still_lost = [(k, v) for k, v in required if v not in result]
    if still_lost:
        def _num_repl(match):
            # match group 1 来自下面四个正则之一
            for g in range(1, match.lastindex + 1 if match.lastindex else 0):
                num = match.group(g)
                if not num:
                    continue
                key = f"P{int(num)}"
                if key in tokens and tokens[key] not in result:
                    return tokens[key]
            return match.group(0)

        # 按顺序：菱形 > 大括号 > 各类方括号/书名号 > 半角圆方角括号（别和内容的括号混淆）
        combos = [
            re.compile(r"◈\s*(?:P\s*)?0*(\d+)\s*◈", re.I),
            re.compile(r"\{\s*0*(\d+)\s*\}"),
            re.compile(r"(?:《|〈|〖|〘|｟)0*(\d+)(?:》|〉|〗|〙|｠)"),
            re.compile(r"(?:【|〔|［|｛)0*(\d+)(?:】|〕|］|｝)"),
            re.compile(r"(?:\[|<|\(|（)\s*0*(\d+)\s*(?:\]|>|）|\))"),
        ]
        # 最多重试 3 轮（同一位置多次被替换不会冲突，因为替换完 v 还没进 result 就表示这个 result 里没有，能再抓新的）
        for _ in range(3):
            changed = False
            for rx in combos:
                new_result = rx.sub(_num_repl, result)
                if new_result != result:
                    result = new_result
                    changed = True
            if not changed:
                break
        still_lost = [(k, v) for k, v in required if v not in result]

    if still_lost:
        lost = [v for _k, v in still_lost]
        return None, "missing restored tokens: " + " | ".join(lost[:8])
    return result, ""


def stable_cell_id(pak_name: str, source_file: str, row: int, column: int, source: str) -> str:
    payload = f"{pak_name}|{source_file}|{row}|{column}|{source}".encode("utf-8", "replace")
    return hashlib.sha1(payload).hexdigest()[:16]


def likely_short_vietnamese_name(text: str) -> bool:
    s = (text or "").strip().strip("#$ ")
    if len(s) < 3 or len(s) > 120:
        return False
    if re.search(r"[\u3400-\u9fff]", s):
        return False
    if re.fullmatch(r"[A-Za-z0-9_./\\:-]+", s):
        return False
    low = " " + s.lower() + " "
    return bool(re.search(r"[A-Za-zÀ-ỹ]", s)) and any(w in low for w in VI_WORDS)


def _iter_line_parts(path: Path, raw_line: bytes):
    if path.suffix.lower() == ".lua":
        for literal in iter_lua_text_parts(raw_line):
            yield literal.ordinal, literal.content, {"mode": "lua_string"}
        return
    cells = raw_line.split(b"\t")
    if path.suffix.lower() in (".ini", ".txt") and len(cells) == 1:
        match = KV_RE.match(raw_line)
        if match:
            yield 1, match.group(2), {"mode": "key_value", "prefix": match.group(1).decode("ascii", "replace")}
            return
    for col_no, cell in enumerate(cells, 1):
        yield col_no, cell, {"mode": "cell"}


def iter_translatable_cells(path: Path, pak_name: str):
    raw = path.read_bytes()
    visible_tsv_columns = set()
    if path.suffix.lower() == '.tsv':
        first = raw.splitlines()[:1]
        if first:
            for col_no, header_cell in enumerate(first[0].split(b'\t'), 1):
                header = decode_best(header_cell)[0].strip()
                if is_visible_tsv_column(header, path.name):
                    visible_tsv_columns.add(col_no)
    for row_no, raw_line in enumerate(raw.splitlines(), 1):
        if path.suffix.lower() == ".tsv" and row_no == 1:
            continue
        if path.suffix.lower() == ".ini" and re.match(rb"^\s*\[[^\]\r\n]{1,160}\]\s*$", raw_line):
            continue
        for col_no, cell, part_meta in _iter_line_parts(path, raw_line):
            if path.suffix.lower() in ('.ini', '.txt') and part_meta['mode'] == 'key_value':
                key = part_meta.get('prefix', '').split('=', 1)[0].strip()
                if not is_visible_ini_key(key):
                    continue
            if not is_candidate_bytes(cell):
                continue
            if path.suffix.lower() == '.tsv' and col_no not in visible_tsv_columns:
                continue
            text, enc, lang, score = decode_best(cell)
            if text.strip().lower() in {'abc', 'test', 'null', 'none', 'n/a'}:
                continue
            hinted_visible_column = path.suffix.lower() == '.tsv' and row_no > 1 and col_no in visible_tsv_columns and any(ord(ch) >= 128 for ch in text)
            # Full XLSX/CSV export is also the polishing round-trip, so include
            # already translated Chinese from structurally safe fields. The
            # separate "export untranslated" command performs the VI-only filter.
            if lang not in ("vi", "mixed", "zh") and not likely_short_vietnamese_name(text) and not hinted_visible_column:
                continue
            if is_resource_reference(text, ""):
                continue
            if not likely_translatable(text, lang):
                continue
            source_file = path.name
            yield {
                "id": stable_cell_id(pak_name, source_file, row_no, col_no, text),
                "source_file": path.name,
                "source_path": path.as_posix(),
                "row": row_no,
                "column": col_no,
                "mode": part_meta["mode"],
                "prefix": part_meta.get("prefix", ""),
                "encoding": enc,
                "language": lang,
                "confidence_score": score,
                "source": text,
                "template": token_template(text),
                "translation": "",
                "protected": " | ".join(protected_tokens(text)),
                "note": "",
            }


def export_one(args):
    src_s, src_root_s, out_root_s, pak_name = args
    src = Path(src_s)
    out_root = Path(out_root_s)
    rel = src.relative_to(Path(src_root_s)).with_suffix(".csv")
    dst = out_root / rel
    rows = list(iter_translatable_cells(src, pak_name))
    if not rows:
        return {"source": str(src), "output": None, "records": 0}
    dst.parent.mkdir(parents=True, exist_ok=True)
    has_placeholders = any(r["template"]["tokens"] for r in rows)
    fields = ["id", "text"] + (["placeholders"] if has_placeholders else [])
    with dst.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            placeholders = " | ".join(f'{t["key"]}={t["value"]}' for t in row["template"]["tokens"])
            out = {"id": row["id"], "text": row["template"]["text"]}
            if has_placeholders:
                out["placeholders"] = placeholders
            writer.writerow(out)
    index_rows = [
        {
            "id": row["id"],
            "source_file": row["source_file"],
            "source_path": row["source_path"],
            "row": row["row"],
            "column": row["column"],
            "mode": row["mode"],
            "prefix": row["prefix"],
            "source": row["source"],
            "export_text": row["template"]["text"],
            "tokens": row["template"]["tokens"],
            "template": row["template"]["template"],
            "encoding": row["encoding"],
        }
        for row in rows
    ]
    return {"source": str(src), "output": str(dst), "records": len(rows), "index": index_rows}


def export_tsv_localization(src_dir: Path, out_dir: Path, pak_name: str = "updatefs.pak", workers: int | None = None):
    files = sorted(p for p in src_dir.rglob("*") if is_localizable_text_path(p))
    wc = worker_count(workers)
    jobs = [(str(p), str(src_dir), str(out_dir), pak_name) for p in files]
    if wc <= 1 or len(jobs) < 4:
        results = [export_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=wc) as ex:
            results = list(ex.map(export_one, jobs, chunksize=1))
    report = {
        "source_dir": str(src_dir),
        "output_dir": str(out_dir),
        "pak": pak_name,
        "resource_files": len(files),
        "tsv_files": sum(1 for p in files if p.suffix.lower() == ".tsv"),
        "ini_files": sum(1 for p in files if p.suffix.lower() == ".ini"),
        "txt_files": sum(1 for p in files if p.suffix.lower() == ".txt"),
        "csv_files": sum(1 for r in results if r.get("output")),
        "records": sum(r.get("records", 0) for r in results),
        "workers": wc,
        "files": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {}
    for result in results:
        for row in result.pop("index", []):
            index[row["id"]] = row
    (out_dir / "_tsv_localization_index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out_dir / "_tsv_localization_export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _collect_text_resource_one(args):
    src_s, src_root_s, pak_name = args
    src = Path(src_s)
    src_root = Path(src_root_s)
    rel = src.relative_to(src_root).as_posix()
    rows = list(iter_translatable_cells(src, pak_name))
    index_rows = []
    csv_rows = []
    for row in rows:
        placeholders = " | ".join(f'{t["key"]}={t["value"]}' for t in row["template"]["tokens"])
        csv_rows.append({"id": row["id"], "text": row["template"]["text"], "placeholders": placeholders})
        index_rows.append({
            "id": row["id"],
            "source_file": row["source_file"],
            "source_path": row["source_path"],
            "relative_path": rel,
            "row": row["row"],
            "column": row["column"],
            "mode": row["mode"],
            "prefix": row["prefix"],
            "source": row["source"],
            "export_text": row["template"]["text"],
            "tokens": row["template"]["tokens"],
            "template": row["template"]["template"],
            "encoding": row["encoding"],
        })
    return {"source": str(src), "relative_path": rel, "records": len(rows), "rows": csv_rows, "index": index_rows}


def export_text_resource_localization(src_dir: Path, out_dir: Path, base_name: str, pak_name: str, workers: int | None = None):
    src_dir = Path(src_dir)
    out_dir = Path(out_dir)
    files = sorted(p for p in src_dir.rglob("*") if is_localizable_text_path(p))
    wc = worker_count(workers)
    jobs = [(str(p), str(src_dir), pak_name) for p in files]
    if wc <= 1 or len(jobs) < 4:
        results = [_collect_text_resource_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=wc) as ex:
            results = list(ex.map(_collect_text_resource_one, jobs, chunksize=1))

    rows = []
    index = {}
    touched_files = 0
    for result in results:
        if result.get("records", 0):
            touched_files += 1
        rows.extend(result.get("rows") or [])
        for item in result.get("index") or []:
            index[item["id"]] = item

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{base_name}_localization.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "text", "placeholders"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    index_path = out_dir / "_text_localization_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "source_dir": str(src_dir),
        "output_dir": str(out_dir),
        "csv": str(csv_path),
        "index": str(index_path),
        "pak": pak_name,
        "resource_files": len(files),
        "matched_files": touched_files,
        "tsv_files": sum(1 for p in files if p.suffix.lower() == ".tsv"),
        "ini_files": sum(1 for p in files if p.suffix.lower() == ".ini"),
        "txt_files": sum(1 for p in files if p.suffix.lower() == ".txt"),
        "records": len(rows),
        "workers": wc,
    }
    (out_dir / "_text_localization_export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def read_translation_csv(csv_path: Path, index: dict, include_id: bool = False, include_unchanged: bool = False):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_id = (row.get("id") or "").strip()
            meta = index.get(row_id, {})
            source = meta.get("source") or row.get("source") or row.get("original") or ""
            export_text = meta.get("export_text", source)
            target = row.get("text")
            if target is None:
                target = row.get("translation", "")
            if not row_id or target is None or (not include_unchanged and (not target or target == source or target == export_text)):
                continue
            row_no = meta.get("row") or row.get("row")
            col_no = meta.get("column") or row.get("column")
            if not row_no or not col_no:
                continue
            restored, error = restore_template(target, meta)
            item = (int(row_no), int(col_no), source, restored, error)
            yield (row_id, *item) if include_id else item


def _read_master_translation_csv(csv_path: Path, index: dict, include_unchanged: bool = False):
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "id" not in reader.fieldnames or "text" not in reader.fieldnames:
            raise ValueError("完整本地化 CSV 必须包含 id,text 两列")
        for row in reader:
            row_id = (row.get("id") or "").strip()
            meta = index.get(row_id, {})
            source = meta.get("source") or ""
            export_text = meta.get("export_text", source)
            target = row.get("text", "")
            if not row_id or (not include_unchanged and (not target or target == source or target == export_text)):
                continue
            row_no = meta.get("row")
            col_no = meta.get("column")
            if not row_no or not col_no:
                continue
            restored, error = restore_template(target, meta)
            yield row_id, int(row_no), int(col_no), source, restored, error, meta


def encode_cell(text: str, original: bytes, source_encoding: str = "") -> bytes:
    if not source_encoding:
        _source, source_encoding, _lang, _score = decode_best(original)
    return encode_text_for_source(text, source_encoding, original)


def import_one(args):
    src_s, src_root_s, csv_root_s, out_root_s, index = args
    src = Path(src_s)
    src_root = Path(src_root_s)
    csv_root = Path(csv_root_s)
    out_root = Path(out_root_s)
    rel = src.relative_to(src_root)
    csv_path = csv_root / rel.with_suffix(".csv")
    dst = out_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        return {"source": str(src), "output": None, "csv": None, "updated": 0, "skipped": 0, "risks": []}
    updates = {}
    risks = []
    skipped = 0
    for row_id, row_no, col_no, source_text, target_text, restore_error in read_translation_csv(csv_path, index, include_id=True):
        if restore_error or target_text is None:
            risks.append({"row": row_no, "column": col_no, "reason": restore_error or "restore failed"})
            skipped += 1
            continue
        ok, reason = validate_translation(source_text, target_text)
        if not ok:
            risks.append({"row": row_no, "column": col_no, "reason": reason})
            skipped += 1
            continue
        meta = index.get(row_id, {})
        updates[(row_no, col_no)] = (target_text, meta.get("encoding", ""), meta)
    out_lines = []
    updated = 0
    for row_no, raw_line in enumerate(src.read_bytes().splitlines(), 1):
        if src.suffix.lower() == ".lua":
            replacements = {}
            for (update_row, update_col), (target_text, source_encoding, _meta) in updates.items():
                if update_row != row_no:
                    continue
                literal = next((x for x in iter_lua_text_parts(raw_line) if x.ordinal == update_col), None)
                if literal is None:
                    risks.append({"row": row_no, "column": update_col, "reason": "Lua string missing"})
                    skipped += 1
                    continue
                replacements[update_col] = encode_cell(target_text, literal.content, source_encoding)
            new_line, applied = replace_lua_text_parts(raw_line, replacements)
            updated += len(applied)
            out_lines.append(new_line)
            continue
        cells = raw_line.split(b"\t")
        for col_no in range(1, len(cells) + 1):
            key = (row_no, col_no)
            if key in updates:
                target_text, source_encoding, meta = updates[key]
                if meta.get("mode") == "key_value":
                    match = KV_RE.match(raw_line)
                    original = match.group(2) if match else cells[col_no - 1]
                    encoded = encode_cell(target_text, original, source_encoding)
                    cells[col_no - 1] = (match.group(1) if match else b"") + encoded
                else:
                    cells[col_no - 1] = encode_cell(target_text, cells[col_no - 1], source_encoding)
                updated += 1
        out_lines.append(b"\t".join(cells))
    dst.write_bytes(b"\r\n".join(out_lines) + (b"\r\n" if out_lines else b""))
    return {"source": str(src), "output": str(dst), "csv": str(csv_path), "updated": updated, "skipped": skipped, "risks": risks[:100]}


def import_tsv_localization(src_dir: Path, csv_dir: Path, out_dir: Path, workers: int | None = None):
    wc = worker_count(workers)
    index_path = csv_dir / "_tsv_localization_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    index_by_file = {}
    for row_id, meta in index.items():
        index_by_file.setdefault(meta.get("source_file") or "", {})[row_id] = meta
    rels = sorted(p.relative_to(csv_dir).with_suffix("") for p in csv_dir.rglob("*.csv") if p.is_file() and not p.name.startswith("_"))
    files = []
    jobs = []
    for rel_no_suffix in rels:
        matches = [p for p in (src_dir / rel_no_suffix.parent).glob(rel_no_suffix.name + ".*") if p.is_file() and p.suffix.lower() in LOCALIZABLE_SUFFIXES]
        if not matches:
            continue
        src = matches[0]
        files.append(src)
        jobs.append((str(src), str(src_dir), str(csv_dir), str(out_dir), index_by_file.get(src.name, {})))
    if wc <= 1 or len(jobs) < 4:
        results = [import_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=wc) as ex:
            results = list(ex.map(import_one, jobs, chunksize=1))
    report = {
        "source_dir": str(src_dir),
        "csv_dir": str(csv_dir),
        "output_dir": str(out_dir),
        "resource_files": len(files),
        "tsv_files": sum(1 for p in files if p.suffix.lower() == ".tsv"),
        "ini_files": sum(1 for p in files if p.suffix.lower() == ".ini"),
        "txt_files": sum(1 for p in files if p.suffix.lower() == ".txt"),
        "lua_files": sum(1 for p in files if p.suffix.lower() == ".lua"),
        "updated": sum(r.get("updated", 0) for r in results),
        "skipped": sum(r.get("skipped", 0) for r in results),
        "risk_count": sum(len(r.get("risks", [])) for r in results),
        "workers": wc,
        "files": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_tsv_localization_import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def import_text_resource_localization(src_dir: Path, csv_path: Path, index_path: Path, out_dir: Path):
    src_dir = Path(src_dir)
    csv_path = Path(csv_path)
    index_path = Path(index_path)
    out_dir = Path(out_dir)
    if not csv_path.is_file():
        raise ValueError(f"未找到完整本地化 CSV：{csv_path}")
    if not index_path.is_file():
        raise ValueError(f"未找到完整本地化索引：{index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))

    by_file = {}
    skipped = 0
    risks = []
    for row_id, row_no, col_no, source_text, target_text, restore_error, meta in _read_master_translation_csv(csv_path, index, include_unchanged=False):
        if restore_error or target_text is None:
            risks.append({"id": row_id, "row": row_no, "column": col_no, "reason": restore_error or "restore failed"})
            skipped += 1
            continue
        ok, reason = validate_translation(source_text, target_text)
        if not ok:
            risks.append({"id": row_id, "row": row_no, "column": col_no, "reason": reason})
            skipped += 1
            continue
        rel = meta.get("relative_path") or meta.get("source_file")
        if not rel:
            skipped += 1
            continue
        by_file.setdefault(rel, {})[(row_no, col_no)] = (target_text, meta.get("encoding", ""), meta)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    changed_files = []
    updated = 0
    for rel, updates in by_file.items():
        src = src_dir / Path(rel)
        if not src.is_file():
            risks.append({"file": rel, "reason": "source file missing"})
            skipped += len(updates)
            continue
        dst = out_dir / src.name
        raw_lines = src.read_bytes().splitlines()
        file_updated = 0
        out_lines = []
        for row_no, raw_line in enumerate(raw_lines, 1):
            cells = raw_line.split(b"\t")
            for col_no in range(1, len(cells) + 1):
                key = (row_no, col_no)
                if key in updates:
                    target_text, source_encoding, meta = updates[key]
                    if meta.get("mode") == "key_value":
                        match = KV_RE.match(raw_line)
                        original = match.group(2) if match else cells[col_no - 1]
                        encoded = encode_cell(target_text, original, source_encoding)
                        cells[col_no - 1] = (match.group(1) if match else b"") + encoded
                    else:
                        cells[col_no - 1] = encode_cell(target_text, cells[col_no - 1], source_encoding)
                    file_updated += 1
            out_lines.append(b"\t".join(cells))
        if file_updated:
            dst.write_bytes(b"\r\n".join(out_lines) + (b"\r\n" if out_lines else b""))
            changed_files.append(src.name)
            updated += file_updated

    report = {
        "source_dir": str(src_dir),
        "csv": str(csv_path),
        "index": str(index_path),
        "output_dir": str(out_dir),
        "updated": updated,
        "changed_file_count": len(changed_files),
        "changed_files": changed_files,
        "skipped": skipped,
        "risk_count": len(risks),
        "risks": risks[:100],
    }
    (out_dir / "_text_localization_import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def apply_csv_to_records(records_path: Path, csv_dir: Path, pak_name: str):
    records = json.loads(records_path.read_text(encoding="utf-8"))
    index_path = csv_dir / "_tsv_localization_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    updates = {}
    skipped = 0
    for csv_path in sorted(p for p in csv_dir.rglob("*.csv") if p.is_file() and not p.name.startswith("_")):
        for row_id, row_no, col_no, source_text, target_text, restore_error in read_translation_csv(csv_path, index, include_id=True, include_unchanged=True):
            if restore_error or target_text is None:
                skipped += 1
                continue
            updates[row_id] = target_text
            meta = index.get(row_id, {})
            try:
                source_file = meta.get("source_file") or ""
                source = meta.get("source") or source_text
                for alt_col in dict.fromkeys([int(col_no), 0, 1]):
                    updates[stable_cell_id(pak_name, source_file, int(row_no), alt_col, source)] = target_text
            except Exception:
                pass
    updated = changed = 0
    for rec in records:
        if rec.get("pak") != pak_name:
            continue
        target = updates.get(rec.get("id"))
        if target is None:
            continue
        if rec.get("source_original") is None:
            rec["source_original"] = rec.get("original", "")
        if rec.get("original") != target:
            changed += 1
        rec["original"] = target
        rec["language"] = score_text(target)[1]
        rec["status"] = "已翻译" if target != rec.get("source_original", "") else "未翻译"
        updated += 1
    records_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"records_path": str(records_path), "csv_dir": str(csv_dir), "pak": pak_name, "updated": updated, "changed": changed, "skipped": skipped}


def apply_master_csv_to_records(records_path: Path, csv_path: Path, index_path: Path, pak_name: str):
    records = json.loads(Path(records_path).read_text(encoding="utf-8"))
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    updates = {}
    skipped = 0
    for row_id, _row_no, _col_no, _source_text, target_text, restore_error, _meta in _read_master_translation_csv(csv_path, index, include_unchanged=True):
        if restore_error or target_text is None:
            skipped += 1
            continue
        updates[row_id] = target_text
    updated = changed = 0
    for rec in records:
        if rec.get("pak") != pak_name:
            continue
        target = updates.get(rec.get("id"))
        if target is None:
            continue
        if rec.get("source_original") is None:
            rec["source_original"] = rec.get("original", "")
        if rec.get("original") != target:
            changed += 1
        rec["original"] = target
        rec["language"] = score_text(target)[1]
        rec["status"] = "已翻译" if target != rec.get("source_original", "") else "未翻译"
        updated += 1
    Path(records_path).write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"records_path": str(records_path), "csv": str(csv_path), "pak": pak_name, "updated": updated, "changed": changed, "skipped": skipped}


def main(argv):
    if len(argv) < 2:
        print("Usage: tsv_localization.py export <tsv-dir> <csv-dir> [pak-name] | import <tsv-dir> <csv-dir> <out-dir>")
        return 2
    if argv[1] == "export" and len(argv) in (4, 5):
        pak_name = argv[4] if len(argv) == 5 else "updatefs.pak"
        print(json.dumps(export_tsv_localization(Path(argv[2]), Path(argv[3]), pak_name), ensure_ascii=False))
        return 0
    if argv[1] == "export-text" and len(argv) == 6:
        print(json.dumps(export_text_resource_localization(Path(argv[2]), Path(argv[3]), argv[4], argv[5]), ensure_ascii=False))
        return 0
    if argv[1] == "import-text" and len(argv) == 6:
        print(json.dumps(import_text_resource_localization(Path(argv[2]), Path(argv[3]), Path(argv[4]), Path(argv[5])), ensure_ascii=False))
        return 0
    if argv[1] == "import" and len(argv) == 5:
        print(json.dumps(import_tsv_localization(Path(argv[2]), Path(argv[3]), Path(argv[4])), ensure_ascii=False))
        return 0
    print("Usage: tsv_localization.py export <tsv-dir> <csv-dir> [pak-name] | import <tsv-dir> <csv-dir> <out-dir>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
