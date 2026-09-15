#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ollama_batch_translate.py
=========================
用本地 Ollama (qwen3:14b) + 术语表提示词，批量翻译 untranslated_xlsx/
导出的 XLSX 文件，桶大小读取 Studio 翻译设置，temperature=0.1。

流程：
  1. 读取 workspace/untranslated_xlsx/<base>/<base>_localization.xlsx
  2. 用 user_config 里已存在的翻译提示词（含 CORE_GLOSSARY）
  3. 按 bucket 向 Ollama /chat/completions 发请求
  4. 每桶立刻写回 xlsx 内存对象 + checkpoint
  5. 全部完成后写回同路径 xlsx（覆盖或带 translated 后缀，默认覆盖会先备份）
  6. 之后可直接运行 import_untranslated_xlsx.py --pak <xxx>

用法：
  # 小桶冒烟测试 settings （跑 2 桶=200行）
  python backend/ollama_batch_translate.py --pak settings.pak --max-buckets 2

  # 完整跑 ui
  python backend/ollama_batch_translate.py --pak ui.pak

  # 完整跑 updatefs（预计 289 桶，可断点续跑）
  python backend/ollama_batch_translate.py --pak updatefs.pak
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from xml.etree import ElementTree as ET

import import_untranslated_xlsx as iux  # 共享 XLSX 读和校验逻辑
import export_untranslated_xlsx as eux  # 共享 XLSX 写（write_xlsx）
from tsv_localization import restore_template, token_template
from localization_analyzer import score_text
from protected_segments import NATURAL_LATIN_RE, split_rows, assemble
from xlsx_localization import read_simple_xlsx as read_full_xlsx

# ---------- 常量 ----------
OLLAMA_BASE = "http://127.0.0.1:11435"
DEFAULT_BUCKET_SIZE = 25
DEFAULT_BUCKET_CHAR_LIMIT = 3000
RETRY_BUCKET_SIZE = 5
RETRY_BUCKET_CHAR_LIMIT = 1200
TEMPERATURE = 0.1
TRANSLATEGEMMA_TERMS = (
    'Máy chủ=服务器; Nhiệm vụ=任务; Đẳng cấp=等级; Cấp=等级; Kinh nghiệm=经验; '
    'Trang bị=装备; Kỹ năng=技能; Môn phái=门派; Vũ khí=武器; Vật phẩm=物品; '
    'Tấn công=攻击; Phòng thủ=防御; Phòng ngự=防御; Sinh lực=生命; '
    'Lực chiến=战力; Bang hội=帮会; Đội ngũ=队伍; Danh vọng=声望.'
)
TRANSLATEGEMMA_PROFILE_PROMPT = """你是《封神/仙侠/武侠 MMORPG》越南语到简体中文的专业本地化翻译员。

翻译要求：
1. 完整翻译所有越南语自然语言，译文符合中国大陆玩家习惯，不得残留越南语或中越混合片段。
2. 人名、地名、技能名、装备名应使用自然的中文意译或音译；UI按钮简短，剧情对白自然。
3. 严格采用术语：Máy chủ=服务器；Nhiệm vụ=任务；Đẳng cấp/Cấp=等级；Kinh nghiệm=经验；Trang bị=装备；Kỹ năng=技能；Môn phái=门派；Vũ khí=武器；Vật phẩm=物品；Tấn công=攻击；Phòng thủ/Phòng ngự=防御；Sinh lực=生命；Lực chiến=战力；Bang hội=帮会；Đội ngũ=队伍；Danh vọng=声望。
4. ZXQROW加六位数字再加ZX是不可翻译、不可删除、不可移动的行边界，必须逐个原样输出且顺序一致。
5. <x数字/> 是程序保护的不可翻译占位符，代表原文中的控制符、颜色标签、变量、路径、文件名、换行或特殊标记。每个占位符必须原样输出一次，严禁翻译、改名、删除、复制、合并或调换顺序。
6. 只翻译行边界之间的自然语言，不添加解释、注释、Markdown、序号或额外内容。
7. 无法安全处理的内容也不得猜测修改保护标记；程序会负责拒收和缩小批次重试。"""
CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "cocos-pak-localization-studio" / "api-translator-config.json"


def multi_pak_live_index(mapping: dict) -> dict[str, dict[str, list]]:
    """Index a v7 mapping once so bucket updates do not scan every record."""
    index: dict[str, dict[str, list]] = {}
    for item in mapping.get("records", []):
        if not isinstance(item, list) or len(item) < 7:
            continue
        record_id = str(item[0] or "")
        skeleton = item[6]
        if not record_id or not isinstance(skeleton, list):
            continue
        for piece in skeleton:
            if isinstance(piece, list) and len(piece) >= 3 and piece[0] == "t":
                index.setdefault(str(piece[1]), {})[record_id] = item
    return index


def multi_pak_live_updates(mapping: dict, xlsx_ids, values: dict[str, str],
                           records_by_id: dict[str, dict] | None = None,
                           segment_index: dict[str, dict[str, list]] | None = None) -> list[dict]:
    """Return safe, progressively rebuilt Studio texts for a v7 multi-PAK XLSX.

    A v7 workbook deliberately keeps runtime tags, placeholders and literals out
    of the XLSX.  Its mapping stores those pieces in an exact skeleton instead.
    During an Ollama run we must rebuild that skeleton before sending an update
    to the renderer; emitting the raw segment alone makes the sidebar counters
    unable to identify the affected Studio record.  Unfinished segments retain
    their canonical source text.  This is display-only and never writes a PAK
    resource or ``text_records.json``.
    """
    records_by_id = records_by_id or {}
    wanted = {str(value) for value in xlsx_ids}
    if not wanted:
        return []

    segment_index = segment_index if segment_index is not None else multi_pak_live_index(mapping)
    affected: dict[str, list] = {}
    for segment_id in wanted:
        affected.update(segment_index.get(segment_id, {}))

    updates = []
    for record_id, item in affected.items():
        record = records_by_id.get(record_id)
        if record is not None and record.get("_isPlayerVisible", True) is not True:
            continue
        output = []
        valid = True
        for piece in item[6]:
            if not isinstance(piece, list) or len(piece) < 2:
                valid = False
                break
            if piece[0] in ("p", "k"):
                output.append(str(piece[1]))
            elif piece[0] == "t" and len(piece) >= 3:
                segment_id, segment_source = str(piece[1]), str(piece[2])
                # A missing / rejected model response is never rendered as an
                # empty string.  Keep the source span until a valid result is
                # available, preserving the record's runtime structure exactly.
                output.append(str(values.get(segment_id) or segment_source))
            else:
                valid = False
                break
        if valid:
            updates.append({"id": record_id, "text": "".join(output)})
    return updates


# ---------- Ollama 调用 ----------
def ollama_chat(messages: list[dict], model: str, base: str = OLLAMA_BASE,
               temperature: float = TEMPERATURE, timeout: int = 600,
               on_delta=None, think: bool = False,
               response_ids: list[str] | None = None,
               positional_count: int | None = None,
               raw_format: bool = False) -> str:
    """Call Ollama's native chat API.

    The OpenAI-compatible endpoint does not consistently honor Qwen's
    ``/no_think`` prompt.  The native API exposes an explicit ``think`` flag,
    so batch translation defaults to disabling reasoning at the server.
    """
    url = base.rstrip("/") + "/api/chat"
    response_format: str | dict | None = None if raw_format else "json"
    if not raw_format and positional_count is not None:
        response_format = {
            "type": "array",
            "minItems": positional_count,
            "maxItems": positional_count,
            "items": {"type": "string"},
        }
    elif not raw_format and response_ids:
        response_format = {
            "type": "array",
            "minItems": len(response_ids),
            "maxItems": len(response_ids),
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": list(response_ids)},
                    "text": {"type": "string"},
                },
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    payload = {
        "model": model,
        "messages": messages,
        "stream": bool(on_delta),
        "think": bool(think),
        "options": {"temperature": temperature},
    }
    if response_format is not None:
        payload["format"] = response_format
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not on_delta:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return str((data.get("message") or {}).get("content") or "")
            parts = []
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (event.get("message") or {}).get("content")
                if not content:
                    if event.get("done"):
                        break
                    continue
                parts.append(str(content))
                on_delta(str(content), "".join(parts))
                if event.get("done"):
                    break
            return "".join(parts)
    except urllib.error.HTTPError as e:
        print(f"  [ERR HTTP {e.code}] {e.read()[:500]}", file=sys.stderr)
        raise
    except Exception:
        raise


# ---------- 提示词 ----------
def load_translator_profile() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    profiles = cfg.get("profiles", [])
    # “Ollama 翻译”必须使用本地 profile，不能跟随当前云端 API profile。
    for p in profiles:
        if p.get("id") == "ollama" or str(p.get("name", "")).strip().lower() == "ollama":
            return p
    # 兼容旧配置：只在没有明确 Ollama profile 时，才按 Ollama 端口选本地地址。
    for p in profiles:
        base_url = str(p.get("baseUrl", ""))
        if ("127.0.0.1" in base_url or "localhost" in base_url) and ":11435" in base_url:
            return p
    active = cfg.get("activeProfileId")
    for p in profiles:
        if p.get("id") == active:
            return p
    return profiles[0] if profiles else {}


def build_bucket_prompt(system_prompt: str, rows: list[dict]) -> list[dict]:
    """构造一次批量翻译请求的消息：系统提示 + 固定 JSON 输出格式。"""
    ids = [r["id"] for r in rows]
    srcs = [r["text"] for r in rows]
    n = len(rows)
    expected_ids = ",".join(f'"{x}"' for x in ids)
    user_text = (
        "/no_think\n"
        "任务：把下面 JSON 数组里的 text 字段从越南语翻译成中文。\n"
        "=== 硬性规则 ===\n"
        "1) 数量：输入数组共 " + str(n) + " 项，你的输出数组也必须有恰好 " + str(n) + " 项。\n"
        "2) id 必须原样返回，且顺序、数量完全一致，id 集合必须是: [" + expected_ids + "]\n"
        "3) 术语严格按术语表；◈N◈ 占位符数量和内容完全保留；$#= 前缀保留；"
        "<c=green></c>、</c> 等颜色标签数量匹配；禁止省略符号。\n"
        "3.1) 译文自然语言部分必须全部为简体中文，不得残留越南语或拉丁字母；"
        "越南人名、地名、物品名必须意译或音译成汉字。代码和占位符除外。\n"
        "3.2) $、#、= 只有符号本身是控制前缀；紧随其后的 Thỉnh、Kháng、Phòng、Hoàn 等仍是越南语，"
        "必须完整翻译。严禁保留 Th、Kh、Ph、Ho 等半截音节。示例："
        "$Hoàn thành nhiệm vụ.→$任务完成。；$Thỉnh giáo→$请教；"
        "$Kháng tất cả +10%→$所有抗性 +10%；$Phòng ngự tăng 140 điểm→$防御增加140点。\n"
        "3.3) 除受保护的代码、路径、变量和通用全大写缩写外，译文中不得出现任何拉丁字母；"
        "无法确定专名时也必须按中文音译，不能输出越南语片段。\n"
        "4) 【输出格式·零容忍】你的整个回复只能是原始 JSON 数组，不能有任何解释、前言、收尾或 Markdown 代码块：\n"
        "[{\"id\":\"xxx\",\"text\":\"中文译文\"},...]\n"
        "5) 每条对象只能有两个字段：id 和 text。text 里的换行、引号请按 JSON 规范转义成 \\n \\\"。\n\n"
        "输入：\n"
        + json.dumps([{"id": ids[i], "text": srcs[i]} for i in range(len(rows))],
                      ensure_ascii=False)
        + "\n\n【再次提醒】直接输出原始 JSON 数组，不要代码块，不要别的话。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]


def build_positional_bucket_prompt(system_prompt: str, rows: list[dict]) -> list[dict]:
    """Faster Gemma prompt: array position replaces repeated JSON id fields."""
    source = [str(row.get("text", "")) for row in rows]
    user_text = (
        "/no_think\n把输入数组逐项从越南语翻译成简体中文。只返回 JSON 字符串数组；"
        f"输出必须恰好 {len(source)} 项，顺序与输入完全一致。"
        "◈N◈、$#= 前缀、颜色标签、路径、变量和代码必须原样保留；"
        "自然语言不得残留越南语。不要解释，不要 Markdown。\n\n输入：\n"
        + json.dumps(source, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]


def parse_positional_array(text: str, rows: list[dict]) -> list[dict]:
    values = json.loads(text.strip(), strict=False)
    if not isinstance(values, list) or len(values) != len(rows):
        actual = len(values) if isinstance(values, list) else "非数组"
        raise ValueError(f"位置数组数量不匹配：期望 {len(rows)}，实际 {actual}")
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("位置数组包含空值或非字符串")
    return [
        {"id": str(row.get("id", "")), "text": value}
        for row, value in zip(rows, values)
    ]


def build_translategemma_prompt(rows: list[dict], localization_prompt: str = '') -> list[dict]:
    """TranslateGemma's documented single-message format plus safe row framing."""
    framed = []
    for index, row in enumerate(rows):
        framed.append(f'ZXQROW{index:06d}ZX {str(row.get("text", ""))}')
    framed.append(f'ZXQROW{len(rows):06d}ZX')
    content = (
        'You are a professional Vietnamese (vi) to Chinese Simplified (zh-Hans) translator. '
        'Accurately convey the complete meaning using natural Chinese suitable for a wuxia/xianxia MMORPG. '
        'Produce only the Chinese translation, without explanations or commentary. '
        'Every token matching ZXQROW followed by six digits and ZX is an immutable row separator. '
        'Every self-closing tag like <x90000000/> is an immutable placeholder. '
        'Copy every separator and placeholder exactly once, in exactly the original order. '
        'Never translate, delete, rename, merge, or move them. Translate only natural language between them. '
        f'Use this terminology: {TRANSLATEGEMMA_TERMS}\n'
        'Additional mandatory game-localization requirements:\n'
        + (localization_prompt.strip() or TRANSLATEGEMMA_PROFILE_PROMPT)
        + '\n\n'
        'Please translate the following Vietnamese text into Chinese Simplified:\n\n'
        + ' '.join(framed)
    )
    return [{"role": "user", "content": content}]


def parse_translategemma_rows(text: str, rows: list[dict]) -> list[dict]:
    expected = [f'ZXQROW{index:06d}ZX' for index in range(len(rows) + 1)]
    found = re.findall(r'ZXQROW\d{6}ZX', text)
    if found != expected:
        raise ValueError(f'TranslateGemma 行边界不完整或错位：期望 {len(expected)}，实际 {len(found)}')
    output = []
    for index, row in enumerate(rows):
        start = text.index(expected[index]) + len(expected[index])
        end = text.index(expected[index + 1], start)
        value = text[start:end].strip()
        if not value:
            raise ValueError(f'TranslateGemma 第 {index + 1} 项为空')
        output.append({'id': str(row.get('id', '')), 'text': value})
    return output


def parse_json_array_block(text: str) -> list[dict]:
    """从模型回复里抓 JSON 数组，多层 fallback。
    策略依次：
      1) ```json 代码块里的 [ ... ]
      2) 整段找最外层 [ ... ] 直接 json.loads
      3) 修复裸数字 id -> 加引号 后 json.loads
      4) strict=False json 容忍转义
      5) ast.literal_eval (容忍 Python 字面量/单引号)
      6) 兜底：扫描所有 {...} 对象（}{ 之间补逗号），拼成数组
    """
    s = text.strip()
    candidates: list[str] = []

    # Some models return a single object for a one-row request even when an
    # array was requested. Accept it here; multi-row completeness is still
    # enforced later by the expected-ID checks.
    try:
        singleton = json.loads(s, strict=False)
        if isinstance(singleton, dict):
            normalized = _normalize_items([singleton])
            if normalized:
                return normalized
    except Exception:
        pass

    # 1. 代码块捕获（优先）
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", s)
    if m:
        candidates.append(m.group(1))
    # 额外：代码块可能没有结尾 ```，抓最后一段
    m2 = re.search(r"```(?:json)?\s*(\[[\s\S]*)$", s)
    if m2:
        tail = m2.group(1)
        last = tail.rfind("]")
        if last >= 0:
            candidates.append(tail[:last + 1])

    # 2. 裸 [ ... ] 捕获
    start = s.find("[")
    end = s.rfind("]")
    if start >= 0 and end > start:
        candidates.append(s[start:end + 1])

    last_err = None
    decoder = json.JSONDecoder(strict=False)
    for chunk in candidates:
        if not chunk:
            continue
        variants = [chunk]
        # Some local models emit JSON delimiters as \"value\" even though the
        # surrounding response is not itself a JSON string. Repair only that
        # single extra escaping layer; normal JSON remains the first choice.
        if r'\"' in chunk:
            variants.append(chunk.replace(r'\"', '"'))
        for candidate in variants:
            try:
                arr = json.loads(candidate)
                if isinstance(arr, list):
                    return _normalize_items(arr)
            except Exception as e:
                last_err = e
        # A. 严格 JSON
        try:
            arr = json.loads(chunk)
            if isinstance(arr, list):
                return _normalize_items(arr)
        except Exception as e:
            last_err = e
        # B. 修 id 数字裸写
        try:
            chunk2 = re.sub(r"(\{[^{}:]*?\"id\"\s*:\s*)(\d+)(\s*[,}])", r'\1"\2"\3', chunk)
            arr = json.loads(chunk2)
            if isinstance(arr, list):
                return _normalize_items(arr)
        except Exception as e:
            last_err = e
        # C. strict=False (容忍控制字符)
        try:
            arr, _idx = decoder.raw_decode(chunk)
            if isinstance(arr, list):
                return _normalize_items(arr)
        except Exception as e:
            last_err = e
        # D. ast.literal_eval (容忍单引号、尾逗号)
        try:
            arr = ast.literal_eval(chunk)
            if isinstance(arr, list):
                return _normalize_items(arr)
        except Exception as e:
            last_err = e

    # 6. 扫描对象流: 找最外 [ ] 后逐个 { ... } 平衡取
    #    再不行就在全文所有 {...} 中找含 id/text 的
    try:
        items = _scan_objects(s)
        if items:
            return _normalize_items(items)
    except Exception as e:
        last_err = e

    raise ValueError(f"回复中找不到 JSON 数组。最后错误: {last_err}; 原文前500: {repr(text[:500])}")


def _normalize_items(arr: list) -> list[dict]:
    result = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        rid = it.get("id") or it.get("ID") or it.get("mapping_id") or it.get("row_id") or it.get("编号")
        txt = (it.get("text") or it.get("translation") or it.get("translated")
               or it.get("译文") or it.get("zh") or it.get("中文"))
        if rid is None or txt is None:
            continue
        result.append({"id": str(rid), "text": str(txt)})
    return result


def _scan_objects(text: str) -> list[dict]:
    """暴力扫描文本中所有平衡的 {...}，用 ast.literal_eval / json 解析，保留含 id&text 的。"""
    out: list[dict] = []
    i = 0
    n = len(text)
    decoder = json.JSONDecoder(strict=False)
    while i < n:
        lb = text.find("{", i)
        if lb < 0:
            break
        # 找平衡 rb
        depth = 0
        in_str = False
        esc = False
        quote = ""
        rb = -1
        for j in range(lb, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == quote:
                    in_str = False
                continue
            if c in ("'", '"'):
                in_str = True
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    rb = j
                    break
        if rb < 0:
            break
        chunk = text[lb:rb + 1]
        obj = None
        for parser in (
            lambda c: json.loads(c),
            lambda c: json.loads(re.sub(r"(\{[^{}:]*?\"id\"\s*:\s*)(\d+)(\s*[,}])", r'\1"\2"\3', c)),
            lambda c: decoder.raw_decode(c)[0],
            lambda c: ast.literal_eval(c),
        ):
            try:
                obj = parser(chunk)
                break
            except Exception:
                continue
        if isinstance(obj, dict):
            rid = obj.get("id") or obj.get("ID") or obj.get("mapping_id") or obj.get("row_id") or obj.get("编号")
            txt = (obj.get("text") or obj.get("translation") or obj.get("translated")
                   or obj.get("译文") or obj.get("zh") or obj.get("中文"))
            if rid is not None and txt is not None:
                out.append({"id": str(rid), "text": str(txt)})
        i = rb + 1
    return out


# ---------- XLSX 写回 ----------
def overwrite_xlsx(original_path: Path, rows: list[dict], headers=None) -> None:
    """写一个全新的 id/text XLSX 覆盖原路径，headers 默认 ["id","text"]。"""
    eux.write_xlsx(original_path, rows, headers or ["id", "text"])


def write_json_atomic(path: Path, payload: dict) -> None:
    """Keep an interrupted run from leaving a truncated checkpoint."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)


def make_dynamic_buckets(rows: list[dict], row_limit: int,
                         char_limit: int = DEFAULT_BUCKET_CHAR_LIMIT) -> list[list[dict]]:
    """Respect the configured row maximum while keeping long prompts bounded."""
    row_limit = max(1, int(row_limit))
    char_limit = max(200, int(char_limit))
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for row in rows:
        # Include JSON/id overhead; one oversized row is intentionally isolated.
        cost = len(str(row.get('text', ''))) + len(str(row.get('id', ''))) + 32
        if current and (len(current) >= row_limit or current_chars + cost > char_limit):
            groups.append(current)
            current = []
            current_chars = 0
        current.append(row)
        current_chars += cost
        if len(current) >= row_limit or current_chars >= char_limit:
            groups.append(current)
            current = []
            current_chars = 0
    if current:
        groups.append(current)
    return groups


def make_file_ordered_buckets(rows: list[dict], row_limit: int,
                              char_limit: int = DEFAULT_BUCKET_CHAR_LIMIT) -> tuple[list[list[dict]], list[str]]:
    """Never mix source files in one request; process files in archive-name order."""
    by_file: dict[str, list[dict]] = {}
    for row in rows:
        by_file.setdefault(str(row.get('_queue_file') or '未分类文本'), []).append(row)
    file_order = sorted(by_file, key=lambda name: name.lower())
    buckets = []
    for name in file_order:
        buckets.extend(make_dynamic_buckets(by_file[name], row_limit, char_limit))
    return buckets, file_order


# ---------- 主流程 ----------
def run(workspace, pak, max_buckets=0, ollama_base=None, resume=True, progress=None,
        bucket_size=None, xlsx=None, control_path=None, metadata_dir=None):
    """用 Ollama 批量翻译指定 PAK 的未翻译 XLSX。返回 report dict。
    progress 为可选回调，接收 dict（含 message / percent）。"""
    ws = Path(workspace)
    base = re.sub(r"\.pak$", "", pak, flags=re.I)
    default_xlsx = ws / "untranslated_xlsx" / base / f"{base}_localization.xlsx"
    xlsx_path = Path(xlsx) if xlsx else default_xlsx
    is_default_xlsx = xlsx_path.resolve() == default_xlsx.resolve()
    metadata_path = Path(metadata_dir) if metadata_dir is not None else xlsx_path.parent
    metadata_path.mkdir(parents=True, exist_ok=True)
    # Keep checkpoints tied to the selected workbook, otherwise continuing a
    # custom file could reuse IDs/translations from a different XLSX.
    ckpt_path = (ws / "untranslated_xlsx" / base / "ollama_checkpoint.json") if is_default_xlsx else metadata_path / (xlsx_path.stem + ".ollama_checkpoint.json")
    custom_records_mapping = metadata_path / f'{xlsx_path.stem}_records_mapping.json'
    legacy_records_mapping = xlsx_path.with_name(f'{xlsx_path.stem}_records_mapping.json')
    multi_mapping_path = metadata_path / f'{xlsx_path.stem}_mapping.json'
    multi_mapping = {}
    if multi_mapping_path.exists():
        try:
            candidate = json.loads(multi_mapping_path.read_text(encoding='utf-8-sig'))
            if (
                candidate.get('mode') == 'multi-pak-out-of-band-skeleton' and candidate.get('version') == 7
            ) or (
                candidate.get('mode') == 'multi-pak-safe-term-glossary' and candidate.get('version') == 1
            ):
                multi_mapping = candidate
        except (OSError, ValueError, TypeError):
            multi_mapping = {}
    glossary_only = multi_mapping.get('mode') == 'multi-pak-safe-term-glossary'
    if not is_default_xlsx and custom_records_mapping.exists():
        records_mapping_path = custom_records_mapping
    elif not is_default_xlsx and legacy_records_mapping.exists():
        records_mapping_path = legacy_records_mapping
    else:
        records_mapping_path = ws / "untranslated_xlsx" / base / f"{base}_records_mapping.json"
    export_report_path = ws / "untranslated_xlsx" / base / "export_report.json"
    if is_default_xlsx and export_report_path.exists():
        export_report = json.loads(export_report_path.read_text(encoding="utf-8"))
        if export_report.get("status") == "empty" or int(export_report.get("unique_rows_xlsx") or 0) == 0:
            raise ValueError(export_report.get("message") or "没有可翻译的未翻译文本")
    if not xlsx_path.exists():
        raise FileNotFoundError(f"未找到 XLSX，请先运行“导出未翻译”：{xlsx_path}")

    profile = load_translator_profile()
    # Environment override is useful for safe quality trials without changing
    # the user's persistent Studio profile.
    model = os.environ.get("PAKLOC_OLLAMA_MODEL") or profile.get("model") or "qwen3:14b"
    system_prompt = profile.get("prompt") or "你是翻译助手。"
    configured_bucket_size = profile.get("batchSize") if bucket_size is None else bucket_size
    try:
        bucket_size = int(configured_bucket_size or DEFAULT_BUCKET_SIZE)
    except (TypeError, ValueError):
        bucket_size = DEFAULT_BUCKET_SIZE
    # Qwen's older JSON output is safest at 25 rows. Gemma4 handles the user's
    # configured 50-row batches reliably; the separate character budget still
    # splits long text automatically, and rejected rows fall back to 5-row
    # safety retries.
    model_bucket_cap = 25 if 'qwen' in str(model).lower() else 50
    bucket_size = max(1, min(model_bucket_cap, bucket_size))
    if ollama_base is None:
        ollama_base = str(profile.get("baseUrl") or OLLAMA_BASE)
        ollama_base = re.sub(r"/v1/?$", "", ollama_base).rstrip("/") or OLLAMA_BASE

    def _emit(message, percent=None, updates=None, **extra):
        if not progress:
            return
        payload = {"message": message}
        if percent is not None:
            payload["percent"] = percent
        if updates:
            payload["updates"] = updates
        payload.update(extra)
        progress(payload)

    records_mapping = {}
    if records_mapping_path.exists():
        records_mapping = json.loads(records_mapping_path.read_text(encoding="utf-8"))
    record_files = {}
    records_by_id = {}
    records_path = ws / 'localization' / 'text_records.json'
    if records_path.exists():
        for record in json.loads(records_path.read_text(encoding='utf-8')):
            record_id = str(record.get('id', ''))
            record_files[record_id] = str(record.get('source_file') or '未分类文本')
            records_by_id[record_id] = record
    multi_live_index = multi_pak_live_index(multi_mapping) if multi_mapping else {}

    def importable(rid, target):
        for entry in records_mapping.get(str(rid), []):
            if isinstance(entry, dict):
                restored, reason = iux.accept_translation(str(entry.get('source', '')), target, entry)
                if restored is None:
                    return False
        return True

    def natural_residual(text):
        return eux.has_natural_language_residual(text)

    def _live_updates(xlsx_ids, values=None) -> list[dict]:
        """Restore one deduplicated XLSX translation to every Studio record it represents."""
        if glossary_only:
            return []
        source_values = translations if values is None else values
        if multi_mapping:
            return multi_pak_live_updates(
                multi_mapping, xlsx_ids, source_values, records_by_id, multi_live_index
            )
        restored_by_id = {}
        for xid in xlsx_ids:
            translated = source_values.get(str(xid), "")
            if not translated:
                continue
            for entry in records_mapping.get(str(xid), []):
                if isinstance(entry, dict):
                    record_id = str(entry.get("id", ""))
                    source = str(entry.get("source", ""))
                    restored, error = iux.accept_translation(source, translated, entry)
                    if error or restored is None:
                        continue
                else:
                    record_id = str(entry)
                    restored = translated
                if record_id:
                    restored_by_id[record_id] = {"id": record_id, "text": restored}
        return list(restored_by_id.values())

    _emit(f"[{pak}] 模型：{model}  Ollama：{ollama_base}")
    _emit(f"提示词长度：{len(system_prompt)} chars")

    rows_in = read_full_xlsx(xlsx_path) if multi_mapping else iux.read_simple_xlsx(xlsx_path)
    if glossary_only and not any(str(row.get("id", "")).strip() for row in rows_in):
        mapping_rows = multi_mapping.get("rows") or []
        if len(mapping_rows) != len(rows_in):
            raise ValueError(
                f"术语库 XLSX 与映射行数不一致：XLSX {len(rows_in):,} 行，映射 {len(mapping_rows):,} 行。"
            )
        for row, mapped in zip(rows_in, mapping_rows):
            row["id"] = str(mapped[0]).strip()
    for row in rows_in:
        row["id"] = str(row.get("id", "")).strip()
    duplicate_ids = sorted(rid for rid, count in Counter(row["id"] for row in rows_in if row["id"]).items() if count > 1)
    if duplicate_ids:
        raise ValueError(f"所选 XLSX 存在重复 ID，已阻止可能的错位导入：{duplicate_ids[:10]}")
    selected_ids = {row["id"] for row in rows_in if row["id"]}
    mapping_ids = (
        {str(row[0]).strip() for row in multi_mapping.get('rows', []) if row}
        if multi_mapping else
        {str(x).strip() for x in records_mapping}
    )
    sparse_multi_mapping = bool(multi_mapping.get('remaining_only'))
    mapping_matches = (
        selected_ids.issubset(mapping_ids)
        and len(selected_ids) == int(multi_mapping.get('workbook_rows') or 0)
        if sparse_multi_mapping else selected_ids == mapping_ids
    )
    if not mapping_matches:
        missing = sorted(mapping_ids - selected_ids)[:10]
        extra = sorted(selected_ids - mapping_ids)[:10]
        raise ValueError(
            f"所选 XLSX 与配套映射不匹配：XLSX ID {len(selected_ids):,} 个，映射 ID {len(mapping_ids):,} 个；"
            f"缺少 {missing}，多出 {extra}。请选取本次‘导出未翻译’产生的 XLSX。"
        )
    # The translated workbook is intentionally overwritten at the end of a run.
    # Reuse its original .bak as the source on resume, otherwise the translated
    # cells change the fingerprint and make a valid checkpoint look stale.
    backup = xlsx_path.with_name(xlsx_path.name + ".bak") if is_default_xlsx else metadata_path / (xlsx_path.name + ".bak")
    saved_checkpoint = {}
    restored_from_backup = False
    if resume and ckpt_path.exists():
        try:
            saved_checkpoint = json.loads(ckpt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            saved_checkpoint = {}
    if saved_checkpoint and backup.exists():
        try:
            backup_rows = read_full_xlsx(backup) if multi_mapping else iux.read_simple_xlsx(backup)
            for row in backup_rows:
                row["id"] = str(row.get("id", "")).strip()
            backup_ids = {row["id"] for row in backup_rows if row["id"]}
            backup_fingerprint = hashlib.sha256(
                json.dumps(backup_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if backup_ids == selected_ids and backup_fingerprint == saved_checkpoint.get("input_fingerprint"):
                rows_in = backup_rows
                restored_from_backup = True
                _emit("[续跑] 已从 XLSX.bak 恢复原始越南文输入")
        except (OSError, ValueError, TypeError):
            pass

    input_fingerprint = hashlib.sha256(json.dumps(rows_in, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    total_rows = len(rows_in)
    buckets = (total_rows + bucket_size - 1) // bucket_size
    _emit(f"XLSX 行数: {total_rows:,}  |  Bucket: {bucket_size} | Buckets: {buckets}")
    if max_buckets:
        _emit(f"max-buckets={max_buckets}（冒烟测试）")

    # Load checkpoint. Completion is tracked by stable row IDs, not bucket
    # numbers, so changing bucket size no longer throws away finished work.
    done: set[int] = set()
    translations: dict[str, str] = {}
    if saved_checkpoint:
        saved_translations = {
            str(k): str(v) for k, v in (saved_checkpoint.get("translations") or {}).items()
            if str(k) in selected_ids and str(v).strip()
        }
        same_source = saved_checkpoint.get("input_fingerprint") == input_fingerprint
        if same_source:
            translations = saved_translations
            for b_idx in range(buckets):
                subset = rows_in[b_idx * bucket_size:min((b_idx + 1) * bucket_size, total_rows)]
                if subset and all(row["id"] in translations for row in subset):
                    done.add(b_idx)
            remaining = total_rows - len(translations)
            remaining_buckets = (remaining + bucket_size - 1) // bucket_size
            _emit(
                f"[续跑] 已恢复译文 {len(translations):,} 条，剩余 {remaining:,} 条（约 {remaining_buckets} 桶）",
                updates=_live_updates(translations.keys()),
            )
        else:
            _emit("[新任务] XLSX 原始内容已变化，忽略旧 checkpoint，防止翻译错位")

    if not restored_from_backup:
        shutil.copy2(xlsx_path, backup)

    # Repack only unfinished rows. Sparse holes in the original buckets must
    # not turn into hundreds of one-row requests after a restart.
    translations = {
        rid: value for rid, value in translations.items()
        if importable(rid, value) and not natural_residual(value)
    }
    # Reuse translations already accepted into Studio even when the selected
    # workbook is an older untranslated copy or comes from another export
    # folder. This is stronger than checkpoint-only resume and prevents valid
    # Google/manual/Ollama Chinese from being sent through the model again.
    reused_from_studio = 0
    for xlsx_id, entries in records_mapping.items():
        xlsx_id = str(xlsx_id)
        if xlsx_id in translations:
            continue
        candidates = Counter()
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            record = records_by_id.get(str(entry.get('id', '')))
            if not record or record.get('_isPlayerVisible', True) is not True:
                continue
            current = str(record.get('original') or '')
            source = str(record.get('source_original', entry.get('source', '')) or entry.get('source', ''))
            if not current or current == source or score_text(current)[1] != 'zh':
                continue
            portable = token_template(current)['text']
            candidates[portable] += 1
        if candidates:
            candidate = candidates.most_common(1)[0][0]
            if importable(xlsx_id, candidate):
                translations[xlsx_id] = candidate
                reused_from_studio += 1
    if reused_from_studio:
        _emit(f'[复用] 从 Studio 当前记录直接带入 {reused_from_studio:,} 条合格中文，不再调用 Ollama')
    # Old exports may still contain complete script expressions. Mark them as
    # unchanged/completed and overwrite any earlier model-corrupted checkpoint
    # value so they are never sent to Ollama or shown as a live translation.
    executable_rows = {
        str(row["id"]): str(row.get("text", ""))
        for row in rows_in
        if eux.looks_like_executable_code(str(row.get("text", "")))
    }
    translations.update(executable_rows)
    # Older exports can contain rows that are already Chinese but were selected
    # by the former over-strict Latin residual detector (Lv/x/Mobile/names).
    # Accept those rows unchanged on resume instead of wasting requests and
    # reporting them as failed forever.
    already_localized_rows = {
        str(row["id"]): str(row.get("text", ""))
        for row in rows_in
        if re.search(r"[\u3400-\u9fff]", str(row.get("text", "")))
        and not natural_residual(str(row.get("text", "")))
        and importable(str(row["id"]), str(row.get("text", "")))
    }
    translations.update(already_localized_rows)
    for row in rows_in:
        mapped_files = sorted({
            record_files.get(str(entry.get('id', '')), '')
            for entry in records_mapping.get(row["id"], []) if isinstance(entry, dict)
        } - {''})
        row['_queue_file'] = mapped_files[0] if mapped_files else str(row.get('source_file') or '未分类文本')
    pending_rows = [row for row in rows_in if row["id"] not in translations]
    pending_buckets, queue_files = make_file_ordered_buckets(pending_rows, bucket_size)
    buckets = len(pending_buckets)
    primary_bucket_count = buckets
    retry_bucket_count = 0
    done.clear()
    _emit(f"本次续译：按文件顺序处理 {len(queue_files)} 个文件；已保存/复用 {len(translations):,} 条，待译 {len(pending_rows):,} 条，共 {buckets} 桶（每桶最多 {bucket_size} 条 / 约 {DEFAULT_BUCKET_CHAR_LIMIT} 字）", total_files=len(queue_files), reused_from_studio=reused_from_studio)

    started_at = time.time()
    done_count = 0
    failed_buckets: dict[str, str] = {}   # phase:bucket_idx -> error message
    api_retry_limit = 2                   # 网络 / HTTP 错误重试 2 次
    parse_retry_limit = 0                 # 快速首轮：残缺项统一留到下轮

    def read_control():
        if control_path:
            try:
                return json.loads(Path(control_path).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        return {'stop': False, 'think': False}

    thinking = False

    def _process_bucket(b_idx: int, rows_subset: list[dict], *,
                        phase_name: str = "首轮", compact: bool = True,
                        phase_bucket_count: int | None = None) -> tuple[bool, str]:
        """单桶处理，最多重试 parse_retry_limit 次 / api_retry_limit 次。
        返回 (成功？:bool, 诊断信息:str)；失败则把原因写入 failed_buckets。"""
        nonlocal done_count
        expected_ids = {r["id"] for r in rows_subset}
        display_buckets = phase_bucket_count if phase_bucket_count is not None else buckets
        current_file = str(rows_subset[0].get('_queue_file') or '未分类文本') if rows_subset else '未分类文本'
        current_file_index = queue_files.index(current_file) + 1 if current_file in queue_files else 0
        failure_key = f"{phase_name}:{b_idx}"
        bucket_t0 = time.time()
        translate_gemma = 'translategemma' in str(model).lower()
        segment_rows, segment_layouts = split_rows(
            rows_subset, compact=compact,
            marker_style='xml' if translate_gemma else 'diamond',
        )
        fast_positional = 'gemma' in str(model).lower() and not translate_gemma and bool(segment_rows)
        if translate_gemma:
            msgs = build_translategemma_prompt(segment_rows, system_prompt)
        elif fast_positional:
            msgs = build_positional_bucket_prompt(system_prompt, segment_rows)
        else:
            msgs = build_bucket_prompt(system_prompt, segment_rows)
        if thinking:
            msgs = [{**m, 'content': m['content'].replace('/no_think', '')} for m in msgs]

        def _chat_with_heartbeat(request_messages, label="主请求", response_ids=None,
                                 positional_count=None, raw_format=False):
            """Keep Studio visibly alive while Ollama is processing one non-streaming request."""
            request_t0 = time.time()
            base_percent = round((b_idx / max(1, display_buckets)) * 100, 1)
            _emit(
                f"文件 {current_file_index}/{len(queue_files)} · {current_file} · 第 {b_idx + 1}/{display_buckets} 桶（{label}，{len(rows_subset)} 行）",
                percent=base_percent,
                current_bucket=b_idx + 1,
                total_buckets=display_buckets,
                bucket_rows=len(rows_subset),
                current_file=current_file,
                current_file_index=current_file_index,
                total_files=len(queue_files),
            )
            streamed_ids = set()
            streamed_values = {}

            last_scanned_closing_brace = -1

            def _on_delta(_delta, accumulated):
                # A translation becomes visible only after its complete JSON object has
                # arrived and passed the same placeholder checks used by final import.
                nonlocal last_scanned_closing_brace
                closing_brace = accumulated.rfind("}")
                if closing_brace <= last_scanned_closing_brace:
                    return
                last_scanned_closing_brace = closing_brace
                try:
                    complete_items = assemble(segment_layouts, _scan_objects(accumulated))
                except Exception:
                    return
                new_ids = []
                samples = []
                expected = {str(r.get("id", "")): str(r.get("text", "")) for r in rows_subset}
                for item in complete_items:
                    rid = str(item.get("id", "")).strip()
                    text = str(item.get("text", ""))
                    if not rid or rid in streamed_ids or rid not in expected:
                        continue
                    ok, _reason = iux.validate(expected[rid], text)
                    if not ok or natural_residual(text) or not importable(rid, text):
                        continue
                    streamed_ids.add(rid)
                    streamed_values[rid] = text
                    new_ids.append(rid)
                    samples.append(text)
                if new_ids:
                    _emit(
                        f"{phase_name}：第 {b_idx + 1}/{display_buckets} 桶 · 已实时显示 {len(streamed_ids)}/{len(rows_subset)} 条",
                        percent=base_percent,
                        updates=_live_updates(new_ids, streamed_values),
                        samples=samples[:3],
                        current_bucket=b_idx + 1,
                        total_buckets=display_buckets,
                        streamed_rows=len(streamed_ids),
                        bucket_rows=len(rows_subset),
                    )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    ollama_chat,
                    request_messages,
                    model=model,
                    base=ollama_base,
                    on_delta=_on_delta,
                    think=thinking,
                    response_ids=response_ids,
                    positional_count=positional_count,
                    raw_format=raw_format,
                )
                while True:
                    try:
                        return future.result(timeout=5)
                    except FutureTimeoutError:
                        waited = int(time.time() - request_t0)
                        _emit(
                            f"{phase_name}：第 {b_idx + 1}/{display_buckets} 桶（{label}）· 本次请求已等待 {waited} 秒",
                            percent=base_percent,
                            current_bucket=b_idx + 1,
                            total_buckets=display_buckets,
                            bucket_rows=len(rows_subset),
                            request_elapsed=waited,
                        )

        # ---- API 调用（网络错误可重试） ----
        reply = ""
        api_ok = False
        last_api_err = None
        for attempt in range(1, api_retry_limit + 2):
            try:
                reply = _chat_with_heartbeat(
                    msgs,
                    f"主请求 {attempt}/{api_retry_limit + 1}",
                    None if fast_positional else [str(row["id"]) for row in segment_rows],
                    len(segment_rows) if fast_positional else None,
                    translate_gemma,
                )
                api_ok = True
                break
            except Exception as e:
                last_api_err = f"{type(e).__name__}: {e}"
                _emit(f"  [API ERR attempt {attempt}/{api_retry_limit + 1}] bucket {b_idx + 1}: {last_api_err}")
                if attempt <= api_retry_limit:
                    time.sleep(2 * attempt)
                else:
                    break
        if not api_ok or not reply:
            msg = f"Ollama 调用失败: {last_api_err}"
            failed_buckets[failure_key] = msg
            done_count += 1
            return False, msg

        # ---- 解析（解析失败可换桶大小重试） ----
        arr: list[dict] = []
        last_parse_err = None
        # 先用原尺寸试一次，不行就缩小到原 1/5 再试一次，最后 1 条/请求兜底
        subsets_to_try: list[tuple[str, list[dict]]] = [("full", rows_subset)]

        for tag, sub in subsets_to_try:
            sub_msgs = msgs if tag == "full" else build_bucket_prompt(system_prompt, sub)
            sub_reply = reply if tag == "full" else None
            sub_ids_expected = {r["id"] for r in sub}
            for attempt in range(1, parse_retry_limit + 2):
                if sub_reply is None:
                    try:
                        sub_reply = _chat_with_heartbeat(
                            sub_msgs,
                            f"补译 {tag} {attempt}/{parse_retry_limit + 1}",
                            [str(row["id"]) for row in sub],
                        )
                    except Exception as e:
                        last_parse_err = f"{type(e).__name__}: {e}"
                        if attempt <= parse_retry_limit:
                            time.sleep(attempt)
                            continue
                        break
                try:
                    parsed_items = (
                        parse_translategemma_rows(sub_reply, segment_rows)
                        if tag == "full" and translate_gemma else
                        parse_positional_array(sub_reply, segment_rows)
                        if tag == "full" and fast_positional else
                        parse_json_array_block(sub_reply)
                    )
                    sub_arr = assemble(segment_layouts, parsed_items) if tag == "full" else parsed_items
                except Exception as e:
                    last_parse_err = f"{type(e).__name__}: {e}"
                    _emit(f"  [PARSE FAIL tag={tag} attempt={attempt}] bucket {b_idx + 1}: {last_parse_err}")
                    sub_reply = None   # 下次重试重新请求
                    if attempt <= parse_retry_limit:
                        time.sleep(attempt)
                        continue
                    else:
                        break
                # 过滤到仅包含本 sub 内 id 的条目
                filtered = [x for x in sub_arr if x.get("id") in sub_ids_expected]
                arr.extend(filtered)
                # 覆盖率够了就不再试
                covered = {x["id"] for x in filtered}
                if len(covered) >= len(sub_ids_expected):
                    break
                sub_reply = None   # 覆盖率不足，再调一次
            # 对 singles 模式：每次只含 1 条，需要单独一条一条调
            if tag == "singles" and len(rows_subset) > 1:
                # 上面的 singles 尝试是把所有行打包再发，现在真·逐条
                singles_out = []
                for one in rows_subset:
                    # 如果之前已经在 full/small1/small2 覆盖过，跳过
                    if one["id"] in {x.get("id") for x in arr}:
                        continue
                    one_msgs = build_bucket_prompt(system_prompt, [one])
                    one_reply = ""
                    for a2 in range(1, parse_retry_limit + 2):
                        try:
                            one_reply = _chat_with_heartbeat(
                                one_msgs,
                                f"单条补译 {a2}/{parse_retry_limit + 1}",
                                [str(one["id"])],
                            )
                        except Exception as e:
                            last_parse_err = f"单条API:{type(e).__name__}: {e}"
                            if a2 <= parse_retry_limit:
                                time.sleep(a2)
                                continue
                            break
                        try:
                            one_arr = parse_json_array_block(one_reply)
                        except Exception as e:
                            last_parse_err = f"单条PARSE:{type(e).__name__}: {e}"
                            if a2 <= parse_retry_limit:
                                time.sleep(a2)
                                continue
                            break
                        for x in one_arr:
                            if x.get("id") == one["id"]:
                                singles_out.append(x)
                                break
                    # 单条失败也记录最后错误
                arr.extend(singles_out)
            # 累积到 arr 的覆盖率足够就退出循环
            covered_all = {x["id"] for x in arr}
            if len(covered_all & expected_ids) >= len(expected_ids):
                break

        if not arr:
            msg = f"解析持续失败，最后错误: {last_parse_err}; 回复前400: {repr(reply[:400])}"
            _emit(f"  [主请求无有效结果] bucket {b_idx + 1}，自动改用 5 条小批补译")

        # ---- 写 translations ----
        ids_in_reply = set()
        accepted_ids = []
        rejected_reasons = Counter()
        source_by_id = {r["id"]: r["text"] for r in rows_subset}
        reply_id_counts = Counter(str(item.get("id", "")).strip() for item in arr)
        for item in arr:
            rid = str(item.get("id", "")).strip()
            text = str(item.get("text", ""))
            if rid not in expected_ids or reply_id_counts[rid] != 1:
                continue
            src = source_by_id.get(rid, "")
            ok, reason = iux.validate(src, text)
            if not ok:
                rejected_reasons[reason or "基础格式校验失败"] += 1
                continue
            if natural_residual(text):
                rejected_reasons["仍含可识别的越南语"] += 1
                continue
            if not importable(rid, text):
                rejected_reasons["占位符还原或映射校验失败"] += 1
                continue
            if ok:
                ids_in_reply.add(rid)
                translations[rid] = text
                accepted_ids.append(rid)

        missing = expected_ids - ids_in_reply
        if missing:
            _emit(f"  [WARN] bucket {b_idx + 1}: 缺少 {len(missing)} 个 id，如 {list(missing)[:3]}")
            # 个别 id 缺失也不算整桶失败（后续重跑会补）
        dt = time.time() - bucket_t0
        rejected_count = len(expected_ids - set(accepted_ids))
        # Persist valid rows immediately. Rejected rows are regrouped after the
        # first pass, avoiding one full-prompt request per rejected row.
        if rejected_count:
            details = "；".join(f"{reason}×{count}" for reason, count in rejected_reasons.most_common(3))
            if not details and missing:
                details = f"模型缺少ID×{len(missing)}"
            failed_buckets[failure_key] = (
                f"已保存合格译文，剩余 {rejected_count} 条等待安全小桶补译"
                + (f"（{details}）" if details else "")
            )
        else:
            failed_buckets.pop(failure_key, None)
        done_count += 1
        prog = (b_idx + 1) / max(1, display_buckets) * 100
        tok = len(reply) / max(1, dt)
        _emit(
            f"{phase_name} [{prog:5.1f}%] bucket {b_idx + 1}/{display_buckets} done in {dt:.1f}s | ~{tok:.0f} chars/s | cache {len(translations):,}",
            percent=round(prog, 1),
            updates=_live_updates(accepted_ids),
        )
        return True, ""

    def save_current_xlsx() -> None:
        current_rows = []
        for source_row in rows_in:
            rid = source_row["id"]
            translated = translations.get(rid, "")
            if glossary_only:
                row = dict(source_row)
                row["id"] = rid
                row["text"] = translated if translated.strip() else source_row["text"]
                current_rows.append(row)
            else:
                current_rows.append({
                    key: value for key, value in {
                        "id": rid,
                        "pak": source_row.get("pak", ""),
                        "source_file": source_row.get("source_file", ""),
                        "text": translated if translated.strip() else source_row["text"],
                    }.items()
                })
        headers = multi_mapping.get("headers") if glossary_only else (["id", "pak", "source_file", "text"] if multi_mapping else ["id", "text"])
        overwrite_xlsx(xlsx_path, current_rows, headers=headers)

    def save_checkpoint(total_buckets: int, phase: str) -> None:
        write_json_atomic(ckpt_path, {
            "pak": pak,
            "model": model,
            "bucket_size": bucket_size,
            "temperature": TEMPERATURE,
            "done_buckets": [],
            "total_buckets": total_buckets,
            "phase": phase,
            "translations": translations,
            "input_fingerprint": input_fingerprint,
            "failed_buckets": failed_buckets,
        })

    stop_requested = False
    for b, bucket_rows in enumerate(pending_buckets):
        control = read_control()
        if control.get('stop'):
            stop_requested = True
            save_checkpoint(primary_bucket_count, "stopped")
            save_current_xlsx()
            _emit(f'Ollama 已停止，断点和已完成译文已保存：{xlsx_path}', stopped=True, xlsx=str(xlsx_path))
            break
        thinking = bool(control.get('think', False))
        if max_buckets and done_count >= max_buckets:
            _emit("[STOP] 达到 max-buckets，正常退出")
            break
        if not bucket_rows:
            done_count += 1
            continue
        _process_bucket(b, bucket_rows, phase_name="首轮", compact=True,
                        phase_bucket_count=primary_bucket_count)
        save_checkpoint(primary_bucket_count, "primary")
        if read_control().get('stop'):
            stop_requested = True
            save_checkpoint(primary_bucket_count, "stopped")
            save_current_xlsx()
            _emit(f'Ollama 已停止，断点和已完成译文已保存：{xlsx_path}', stopped=True, xlsx=str(xlsx_path))
            break

    # Do not turn every rejected row into one or two immediate model calls. Once
    # the efficient first pass is complete, regroup only unresolved rows into
    # small safe buckets and send the full prompt once per group.
    retry_rows = [row for row in pending_rows if row["id"] not in translations]
    if retry_rows and not stop_requested and (not max_buckets or done_count < max_buckets):
        retry_buckets, _retry_files = make_file_ordered_buckets(
            retry_rows,
            min(RETRY_BUCKET_SIZE, bucket_size),
            RETRY_BUCKET_CHAR_LIMIT,
        )
        retry_bucket_count = len(retry_buckets)
        failed_buckets.clear()
        _emit(
            f"首轮完成：剩余 {len(retry_rows):,} 条，重新组成 {retry_bucket_count} 个安全小桶补译"
        )
        for b, bucket_rows in enumerate(retry_buckets):
            control = read_control()
            if control.get('stop'):
                stop_requested = True
                save_checkpoint(primary_bucket_count + retry_bucket_count, "stopped")
                save_current_xlsx()
                _emit(f'Ollama 已停止，断点和已完成译文已保存：{xlsx_path}', stopped=True, xlsx=str(xlsx_path))
                break
            thinking = bool(control.get('think', False))
            if max_buckets and done_count >= max_buckets:
                _emit("[STOP] 达到 max-buckets，正常退出")
                break
            _process_bucket(b, bucket_rows, phase_name="安全补译", compact=False,
                            phase_bucket_count=retry_bucket_count)
            save_checkpoint(primary_bucket_count + retry_bucket_count, "retry")
            if read_control().get('stop'):
                stop_requested = True
                save_checkpoint(primary_bucket_count + retry_bucket_count, "stopped")
                save_current_xlsx()
                _emit(f'Ollama 已停止，断点和已完成译文已保存：{xlsx_path}', stopped=True, xlsx=str(xlsx_path))
                break

    translated_count = sum(1 for r in rows_in if translations.get(r["id"], "").strip())
    save_current_xlsx()

    elapsed = time.time() - started_at
    report = {
        "pak": pak,
        "xlsx": str(xlsx_path),
        "model": model,
        "ollama_base": ollama_base,
        "bucket_size": bucket_size,
        "temperature": TEMPERATURE,
        "total_rows": total_rows,
        "translated_rows": translated_count,
        "remaining_rows": total_rows - translated_count,
        "done_buckets": done_count,
        "failed_buckets": len(failed_buckets),
        "failed_bucket_details": {str(k): v for k, v in failed_buckets.items()},
        "status": "failed" if translated_count == 0 and failed_buckets else ("partial" if failed_buckets or translated_count < total_rows else "complete"),
        "total_buckets": primary_bucket_count + retry_bucket_count,
        "elapsed_seconds": round(elapsed, 1),
        "checkpoint": str(ckpt_path),
        "stopped": stop_requested,
        "reused_from_studio": reused_from_studio,
        "glossary_only": glossary_only,
    }
    report_path = (
        xlsx_path.parent / "translation_report.json"
        if is_default_xlsx
        else metadata_path / (xlsx_path.stem + "_translation_report.json")
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _emit("=== 批量翻译完成 ===")
    _emit(f"已译 {translated_count:,} / 剩余 {total_rows - translated_count:,}")
    if failed_buckets:
        _emit(f"仍有失败桶 {len(failed_buckets)} 个（合格结果已保存，下次自动续跑剩余项）")
        for k, v in list(failed_buckets.items())[:5]:
            _emit(f"  - {k}: {v[:120]}")
    if translated_count == 0 and failed_buckets:
        first_error = next(iter(failed_buckets.values()))
        raise RuntimeError(f"Ollama 翻译失败：0/{total_rows} 条成功。{first_error}")
    return report


def run_folder(workspace, pak, folder, bucket_size=None, control_path=None, progress=None, metadata_dir=None) -> dict:
    """Translate every per-file workbook in a folder, in filename order."""
    folder = Path(folder)
    if metadata_dir is not None:
        metadata_dir = Path(metadata_dir)
    else:
        adjacent_config = folder.parent / 'config'
        metadata_dir = adjacent_config if adjacent_config.is_dir() else folder
    workbooks = sorted(
        path for path in folder.glob('*_localization.xlsx')
        if not path.name.endswith('.bak')
    )
    if not workbooks:
        raise ValueError(f'文件夹中没有找到 *_localization.xlsx：{folder}')
    completed_before = []
    pending_workbooks = []
    for workbook in workbooks:
        report_path = metadata_dir / (workbook.stem + '_translation_report.json')
        complete = False
        if report_path.exists():
            try:
                previous = json.loads(report_path.read_text(encoding='utf-8'))
                # A later manual/Google edit invalidates the completion marker
                # and must be processed again. Otherwise a complete report is
                # authoritative and the workbook never re-enters run().
                complete = (
                    previous.get('status') == 'complete'
                    and int(previous.get('remaining_rows') or 0) == 0
                    and report_path.stat().st_mtime_ns >= workbook.stat().st_mtime_ns
                )
            except (OSError, ValueError, TypeError):
                complete = False
        if complete:
            completed_before.append(workbook)
        else:
            pending_workbooks.append(workbook)
    # File-level priority comes from the current Studio record database, not
    # from filename guesses. Workbooks whose source files still contain VI or
    # mixed text run first; Chinese-only workbooks stay behind them and are
    # normally satisfied by the reuse/skip logic inside run().
    vietnamese_by_file = Counter()
    records_path = Path(workspace) / 'localization' / 'text_records.json'
    if records_path.exists():
        try:
            for record in json.loads(records_path.read_text(encoding='utf-8')):
                if record.get('pak') != pak or record.get('_isPlayerVisible', True) is not True:
                    continue
                if score_text(str(record.get('original') or ''))[1] in {'vi', 'mixed'}:
                    vietnamese_by_file[str(record.get('source_file') or '')] += 1
        except (OSError, ValueError, TypeError):
            vietnamese_by_file.clear()

    def source_files_for_workbook(workbook: Path) -> list[str]:
        mapping_path = metadata_dir / (workbook.stem + '_mapping.json')
        if mapping_path.exists():
            try:
                mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
                names = [Path(str(value)).name for value in mapping.get('files') or [] if str(value)]
                if names:
                    return names
            except (OSError, ValueError, TypeError):
                pass
        suffix = '_localization.xlsx'
        return [workbook.name[:-len(suffix)]] if workbook.name.endswith(suffix) else [workbook.stem]

    def vietnamese_count(workbook: Path) -> int:
        return sum(vietnamese_by_file.get(name, 0) for name in source_files_for_workbook(workbook))

    pending_workbooks.sort(key=lambda workbook: (
        0 if vietnamese_count(workbook) > 0 else 1,
        workbook.name.lower(),
    ))
    prioritized_files = sum(1 for workbook in pending_workbooks if vietnamese_count(workbook) > 0)
    reports = []
    failed = []
    stopped = False
    completed_count = len(completed_before)
    workbook_rows = {}
    for workbook in workbooks:
        try:
            workbook_rows[workbook] = max(1, len(iux.read_simple_xlsx(workbook)))
        except Exception:
            workbook_rows[workbook] = 1
    total_queue_rows = sum(workbook_rows.values())
    completed_queue_rows = sum(workbook_rows[workbook] for workbook in completed_before)
    if progress:
        next_name = pending_workbooks[0].name if pending_workbooks else '无'
        progress({
            'percent': round(completed_queue_rows * 100 / max(1, total_queue_rows), 1),
            'message': f'续跑检查完成：已完成并跳过 {completed_count}/{len(workbooks)}；越南文优先队列 {prioritized_files} 个；下一个：{next_name}',
            'current_file': next_name,
            'current_file_index': completed_count + 1 if pending_workbooks else len(workbooks),
            'total_files': len(workbooks),
            'completed_files': completed_count,
            'skipped_completed_files': completed_count,
            'prioritized_vietnamese_files': prioritized_files,
            'completed_rows': completed_queue_rows,
            'total_rows': total_queue_rows,
        })
    processed_queue_rows = completed_queue_rows
    for pending_index, workbook in enumerate(pending_workbooks, 1):
        index = completed_count + pending_index
        if progress:
            priority_label = '越南文优先' if vietnamese_count(workbook) > 0 else '复用/复查'
            progress({'percent': round(processed_queue_rows * 100 / max(1, total_queue_rows), 1), 'message': f'{priority_label} {index}/{len(workbooks)}：{workbook.name}（已完成 {completed_count}）', 'current_file': workbook.name, 'current_file_index': index, 'total_files': len(workbooks), 'completed_files': completed_count, 'skipped_completed_files': len(completed_before), 'prioritized_vietnamese_files': prioritized_files, 'completed_rows': processed_queue_rows, 'total_rows': total_queue_rows})
        try:
            def forward_file_progress(item, i=index, name=workbook.name, base_rows=processed_queue_rows, file_rows=workbook_rows[workbook]):
                if not progress:
                    return
                try:
                    file_percent = max(0.0, min(100.0, float(item.get('percent') or 0)))
                except (TypeError, ValueError):
                    file_percent = 0.0
                global_rows = base_rows + file_rows * file_percent / 100
                progress({**item, 'file_percent': file_percent, 'percent': round(global_rows * 100 / max(1, total_queue_rows), 2), 'message': f'文件 {i}/{len(workbooks)} · {name} · {item.get("message", "")}', 'current_file': name, 'current_file_index': i, 'total_files': len(workbooks), 'completed_rows': round(global_rows), 'total_rows': total_queue_rows})

            report = run(workspace, pak, resume=True, progress=forward_file_progress if progress else None, bucket_size=bucket_size, xlsx=workbook, control_path=control_path, metadata_dir=metadata_dir)
            reports.append(report)
            if report.get('status') == 'complete' and not report.get('stopped'):
                completed_count += 1
            if report.get('stopped'):
                stopped = True
                break
        except Exception as exc:
            failed.append({'file': workbook.name, 'error': str(exc)})
        finally:
            processed_queue_rows += workbook_rows[workbook]
    if not reports and failed:
        first = failed[0]
        raise ValueError(
            f'没有任何 XLSX 完成翻译。配置目录：{metadata_dir}；'
            f'首个失败：{first["file"]}：{first["error"]}'
        )
    return {'folder': str(folder.resolve()), 'config_dir': str(metadata_dir.resolve()), 'total_files': len(workbooks), 'completed_files': completed_count, 'skipped_completed_files': len(completed_before), 'processed_files': len(reports), 'remaining_files': max(0, len(workbooks) - completed_count), 'prioritized_vietnamese_files': prioritized_files, 'failed_files': len(failed), 'failures': failed[:50], 'stopped': stopped, 'reports': reports}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\paks++")
    ap.add_argument("--pak", required=True, help="settings.pak / ui.pak / updatefs.pak / script.pak")
    ap.add_argument("--max-buckets", type=int, default=0, help="0=全部")
    ap.add_argument("--no-resume", action="store_true", help="不从 checkpoint 续跑")
    ap.add_argument("--ollama", default=None)
    ap.add_argument("--bucket-size", type=int, default=None)
    args = ap.parse_args()

    def _print(p):
        print(f"  {p.get('message', '')}", flush=True)

    report = run(args.workspace, args.pak, max_buckets=args.max_buckets,
                 ollama_base=args.ollama, resume=not args.no_resume, progress=_print,
                 bucket_size=args.bucket_size)
    print()
    print("==== 批量翻译完成 ====")
    for k, v in report.items():
        print(f"  {k}: {v}")
    print()
    print(f"下一步：导入结果")
    print(f"  python backend/import_untranslated_xlsx.py --pak {args.pak}")


if __name__ == "__main__":
    main()
