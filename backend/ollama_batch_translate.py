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
from tsv_localization import restore_template
from protected_segments import NATURAL_LATIN_RE, split_rows, assemble

# ---------- 常量 ----------
OLLAMA_BASE = "http://127.0.0.1:11435"
DEFAULT_BUCKET_SIZE = 25
DEFAULT_BUCKET_CHAR_LIMIT = 1800
RETRY_BUCKET_SIZE = 5
RETRY_BUCKET_CHAR_LIMIT = 1200
TEMPERATURE = 0.1
CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "cocos-pak-localization-studio" / "api-translator-config.json"

# ---------- Ollama 调用 ----------
def ollama_chat(messages: list[dict], model: str, base: str = OLLAMA_BASE,
               temperature: float = TEMPERATURE, timeout: int = 600,
               on_delta=None, think: bool = False,
               response_ids: list[str] | None = None) -> str:
    """Call Ollama's native chat API.

    The OpenAI-compatible endpoint does not consistently honor Qwen's
    ``/no_think`` prompt.  The native API exposes an explicit ``think`` flag,
    so batch translation defaults to disabling reasoning at the server.
    """
    url = base.rstrip("/") + "/api/chat"
    response_format: str | dict = "json"
    if response_ids:
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
        "format": response_format,
        "options": {"temperature": temperature},
    }
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


# ---------- 主流程 ----------
def run(workspace, pak, max_buckets=0, ollama_base=None, resume=True, progress=None,
        bucket_size=None, xlsx=None, control_path=None):
    """用 Ollama 批量翻译指定 PAK 的未翻译 XLSX。返回 report dict。
    progress 为可选回调，接收 dict（含 message / percent）。"""
    ws = Path(workspace)
    base = re.sub(r"\.pak$", "", pak, flags=re.I)
    default_xlsx = ws / "untranslated_xlsx" / base / f"{base}_localization.xlsx"
    xlsx_path = Path(xlsx) if xlsx else default_xlsx
    # Keep checkpoints tied to the selected workbook, otherwise continuing a
    # custom file could reuse IDs/translations from a different XLSX.
    ckpt_path = (ws / "untranslated_xlsx" / base / "ollama_checkpoint.json") if xlsx_path.resolve() == default_xlsx.resolve() else xlsx_path.with_name(xlsx_path.stem + ".ollama_checkpoint.json")
    records_mapping_path = ws / "untranslated_xlsx" / base / f"{base}_records_mapping.json"
    export_report_path = ws / "untranslated_xlsx" / base / "export_report.json"
    if export_report_path.exists():
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
    # qwen3:14b is stable at 25 rows. Larger buckets can return shifted or
    # repeated translations even when the item count looks correct.
    bucket_size = max(1, min(25, bucket_size))
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
        source_values = translations if values is None else values
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

    rows_in = iux.read_simple_xlsx(xlsx_path)
    for row in rows_in:
        row["id"] = str(row.get("id", "")).strip()
    duplicate_ids = sorted(rid for rid, count in Counter(row["id"] for row in rows_in if row["id"]).items() if count > 1)
    if duplicate_ids:
        raise ValueError(f"所选 XLSX 存在重复 ID，已阻止可能的错位导入：{duplicate_ids[:10]}")
    selected_ids = {row["id"] for row in rows_in if row["id"]}
    mapping_ids = {str(x).strip() for x in records_mapping}
    if selected_ids != mapping_ids:
        missing = sorted(mapping_ids - selected_ids)[:10]
        extra = sorted(selected_ids - mapping_ids)[:10]
        raise ValueError(
            f"所选 XLSX 与当前 {pak} 映射不匹配：XLSX ID {len(selected_ids):,} 个，映射 ID {len(mapping_ids):,} 个；"
            f"缺少 {missing}，多出 {extra}。请选取本次‘导出未翻译’产生的 XLSX。"
        )
    # The translated workbook is intentionally overwritten at the end of a run.
    # Reuse its original .bak as the source on resume, otherwise the translated
    # cells change the fingerprint and make a valid checkpoint look stale.
    backup = xlsx_path.with_name(xlsx_path.name + ".bak")
    saved_checkpoint = {}
    restored_from_backup = False
    if resume and ckpt_path.exists():
        try:
            saved_checkpoint = json.loads(ckpt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            saved_checkpoint = {}
    if saved_checkpoint and backup.exists():
        try:
            backup_rows = iux.read_simple_xlsx(backup)
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
    pending_rows = [row for row in rows_in if row["id"] not in translations]
    pending_buckets = make_dynamic_buckets(pending_rows, bucket_size)
    buckets = len(pending_buckets)
    primary_bucket_count = buckets
    retry_bucket_count = 0
    done.clear()
    _emit(f"本次续译：已保存 {len(translations):,} 条，脚本跳过 {len(executable_rows):,} 条，已有中文 {len(already_localized_rows):,} 条，待译 {len(pending_rows):,} 条，共 {buckets} 桶（每桶最多 {bucket_size} 条 / 约 {DEFAULT_BUCKET_CHAR_LIMIT} 字）")

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
        failure_key = f"{phase_name}:{b_idx}"
        bucket_t0 = time.time()
        segment_rows, segment_layouts = split_rows(rows_subset, compact=compact)
        msgs = build_bucket_prompt(system_prompt, segment_rows)
        if thinking:
            msgs = [{**m, 'content': m['content'].replace('/no_think', '')} for m in msgs]

        def _chat_with_heartbeat(request_messages, label="主请求", response_ids=None):
            """Keep Studio visibly alive while Ollama is processing one non-streaming request."""
            request_t0 = time.time()
            base_percent = round((b_idx / max(1, display_buckets)) * 100, 1)
            _emit(
                f"{phase_name}：正在翻译第 {b_idx + 1}/{display_buckets} 桶（{label}，{len(rows_subset)} 行）",
                percent=base_percent,
                current_bucket=b_idx + 1,
                total_buckets=display_buckets,
                bucket_rows=len(rows_subset),
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
                    [str(row["id"]) for row in segment_rows],
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
                    parsed_items = parse_json_array_block(sub_reply)
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
            current_rows.append({"id": rid, "text": translated if translated.strip() else source_row["text"]})
        overwrite_xlsx(xlsx_path, current_rows)

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
        retry_buckets = make_dynamic_buckets(
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
    }
    (xlsx_path.parent / "translation_report.json").write_text(
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
