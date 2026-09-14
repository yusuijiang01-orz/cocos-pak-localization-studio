#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from api_translator import (
    CORE_GLOSSARY,
    CJK_RE,
    MOJIBAKE_RE,
    SUSPICIOUS_TARGET_RE,
    VI_MARK_RE,
    VI_WORD_RE,
    _call_translate,
    _validate_translation,
)
from localization_analyzer import score_text
from localization_tm import normalize_key
from tsv_localization import validate_translation
from xlsx_localization import (
    _mapping_rows,
    _source_cell_meta,
    read_simple_xlsx,
    write_simple_xlsx,
)

REVIEW_VERSION = 1
REVIEW_PROMPT = (
    "你是中国大陆游戏本地化审校员，负责根据越南语原文对现有简体中文译文做二次审校与润色。"
    "\n\n核心术语表（全文术语必须与之一致）：\n"
    + CORE_GLOSSARY
    + "\n\n审校要求：\n"
    "1. 优先输出自然、简洁、符合中国大陆玩家习惯的简体中文；若当前译文未翻译、中越混杂或机翻生硬，直接重译为合格译文。\n"
    "2. 统一人物、门派、装备、技能、界面等术语（严格套用上方术语表）；不得编造原文不存在的信息。\n"
    "3. 不得改动原文中的占位符、标签、路径、代码、数字与特殊符号（如 ◈1◈、♥ ★ 等），数量与顺序保持一致。\n"
    "4. 输入中每项 text 形如 越南语原文：... 当前译文：...，请只返回审校后的简体中文。\n"
    "5. 严格只返回 JSON 数组：[{\"id\":\"原ID\",\"text\":\"审校后的简体中文\"}]，id 一一对应且不可遗漏，不要解释。"
)

LATIN_KEEP_RE = re.compile(r"\b(?:NPC|PK|PVP|PVE|Pet|VIP|Boss|GM|OTP|SMS|App|ID|Lv|HP|MP|EXP|UI|URL|CDN|PAK|INI|TXT|TSV|CSV)\b", re.I)
VI_ASCII_RE = re.compile(
    r"\b(?:phi|phong|binh|trung|dao|bao|giap|than|thien|long|huyen|quyen|"
    r"linh|tuong|quan|chien|trang|nhiem|nang|thanh|nhan|thuong|khong|"
    r"nguoi|dung|kinh|cap|ho|lua|tien|kiem|hoa|thuy|dia|ma)\b",
    re.I,
)
BAD_CHINESE_RE = re.compile(r"(?:玩立即|你忘了密码吗|点击确认进行确认|袋礼能动天|在美国购买任何东西)")
HAN_SPACE_RE = re.compile(r"[\u3400-\u9fff]\s+[\u3400-\u9fff]")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(str(x or "") for x in parts).encode("utf-8", "replace")).hexdigest()


def review_risks(source: str, current: str) -> list[str]:
    source = str(source or "").strip()
    current = str(current or "").strip()
    reasons = []
    language = score_text(current)[1]
    if not current:
        reasons.append("空译文")
    if language == "mixed" or (CJK_RE.search(current) and (VI_MARK_RE.search(current) or VI_WORD_RE.search(current))):
        reasons.append("越南文残留")
    if MOJIBAKE_RE.search(current):
        reasons.append("乱码")
    if SUSPICIOUS_TARGET_RE.search(current) or BAD_CHINESE_RE.search(current):
        reasons.append("已知劣质译文")
    latin = LATIN_KEEP_RE.sub(" ", current)
    if CJK_RE.search(current) and (VI_MARK_RE.search(latin) or VI_ASCII_RE.search(latin)):
        reasons.append("中越混合")
    if len(HAN_SPACE_RE.findall(current)) >= 2:
        reasons.append("异常汉字空格")
    source_plain = re.sub(r"<[^>]+>|\$?\{[^}]+\}|%\w", "", source).strip()
    current_plain = re.sub(r"<[^>]+>|\$?\{[^}]+\}|%\w", "", current).strip()
    if CJK_RE.search(current_plain) and len(source_plain) >= 8 and len(current_plain) <= 1:
        reasons.append("疑似译文截断")
    return list(dict.fromkeys(reasons))


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def _review_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS api_review_cache(
        cache_key TEXT PRIMARY KEY,
        source_text TEXT NOT NULL,
        input_text TEXT NOT NULL,
        target_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    db.commit()
    return db


def _write_review_log(path: Path, items: dict) -> None:
    rows = []
    for item in sorted(items.values(), key=lambda x: (x.get("status") == "待审校", x.get("id", ""))):
        rows.append({
            "id": item.get("id", ""),
            "source": item.get("source", ""),
            "before": item.get("before", ""),
            "text": item.get("text", ""),
            "status": item.get("status", ""),
            "risk": "、".join(item.get("reasons") or []),
        })
    write_simple_xlsx(path, rows, ["id", "source", "before", "text", "status", "risk"], "API审校")


def _xlsx_values(path: Path) -> tuple[list[str], dict[str, str]]:
    order = []
    values = {}
    for row in read_simple_xlsx(path):
        raw = row.get("_values") or []
        row_id = str(row.get("id", "") or (raw[0] if raw else "")).strip()
        text = row.get("text")
        if text is None:
            text = row.get("text_zh")
        if text is None or text == "":
            text = raw[1] if len(raw) > 1 else ""
        if row_id:
            order.append(row_id)
            values[row_id] = str(text or "")
    return order, values


def _write_full_xlsx(path: Path, order: list[str], values: dict[str, str]) -> None:
    temp = path.with_name(path.stem + ".tmp.xlsx")
    write_simple_xlsx(temp, [{"id": row_id, "text": values.get(row_id, "")} for row_id in order], ["id", "text"])
    temp.replace(path)


def _call_review_resilient(base_url, api_key, model, prompt, rows, notify=None):
    """Translate a batch without letting one timeout or malformed reply abort the review."""
    if not rows:
        return [], 0, {}
    try:
        result = _call_translate(base_url, api_key, model, prompt, rows, 0.1, timeout=180)
        returned = {str(item.get("id", "")) for item in result if isinstance(item, dict)}
        missing = [row for row in rows if str(row.get("id", "")) not in returned]
        if missing and len(rows) > 1:
            raise ValueError(f"模型漏回 {len(missing)} / {len(rows)} 个 ID")
        return result, 1, {}
    except Exception as exc:
        if len(rows) == 1:
            row_id = str(rows[0].get("id", ""))
            return [], 1, {row_id: str(exc)}
        middle = max(1, len(rows) // 2)
        if notify:
            notify(f"当前批次响应异常，正在自动拆分为 {middle} + {len(rows) - middle} 条重试")
        left, left_calls, left_errors = _call_review_resilient(base_url, api_key, model, prompt, rows[:middle], notify)
        right, right_calls, right_errors = _call_review_resilient(base_url, api_key, model, prompt, rows[middle:], notify)
        return left + right, 1 + left_calls + right_calls, {**left_errors, **right_errors}


def review_records(
    records_path: Path,
    pak_name: str,
    output_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    batch_size: int,
    db_path: Path,
    mode: str = "risk",
    source_xlsx: Path | None = None,
    mapping_path: Path | None = None,
    extracted_dir: Path | None = None,
    force_ids: set[str] | None = None,
    progress=None,
) -> dict:
    records_path = Path(records_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    force_ids = {str(value) for value in (force_ids or set()) if str(value)}
    selected_mode = bool(force_ids)
    checkpoint_path = output_dir / ("review_selection_checkpoint.json" if selected_mode else "review_checkpoint.json")
    reviewed_xlsx = output_dir / f"{Path(pak_name).stem}_reviewed.xlsx"
    review_log = output_dir / f"{Path(pak_name).stem}_{'selection_' if selected_mode else ''}review_log.xlsx"
    selection_signature = _hash(*sorted(force_ids)) if selected_mode else ""
    signature = _hash(model, prompt, mode, selection_signature)[:20]
    checkpoint = {"version": REVIEW_VERSION, "pak": pak_name, "signature": signature, "mode": mode, "items": {}}
    if checkpoint_path.is_file():
        try:
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8-sig"))
            if saved.get("version") == REVIEW_VERSION and saved.get("pak") == pak_name:
                checkpoint = saved
        except Exception:
            pass
    if checkpoint.get("signature") != signature:
        checkpoint = {"version": REVIEW_VERSION, "pak": pak_name, "signature": signature, "mode": mode, "items": {}}

    records = json.loads(records_path.read_text(encoding="utf-8"))
    source_xlsx = Path(source_xlsx) if source_xlsx else None
    mapping_path = Path(mapping_path) if mapping_path else None
    extracted_dir = Path(extracted_dir) if extracted_dir else None
    workbook_source = reviewed_xlsx if reviewed_xlsx.is_file() else source_xlsx
    if not workbook_source or not workbook_source.is_file():
        raise ValueError("未找到最近导入的译后 XLSX，请先执行“导入润色”")
    xlsx_order, xlsx_texts = _xlsx_values(workbook_source)
    record_to_mapping = {}
    if mapping_path and mapping_path.is_file() and extracted_dir and extracted_dir.is_dir():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8-sig"))
        line_cache = {}
        for mapping_id, _source, cells in _mapping_rows(mapping):
            for cell in cells:
                try:
                    meta = _source_cell_meta(extracted_dir, pak_name, cell, line_cache)
                    record_to_mapping[meta["id"]] = mapping_id
                except Exception:
                    continue
        for record in records:
            mapping_id = record_to_mapping.get(record.get("id"))
            if mapping_id:
                xlsx_texts[mapping_id] = str(record.get("original") or "")
    groups = {}
    for index, record in enumerate(records):
        if record.get("pak") != pak_name:
            continue
        if selected_mode and str(record.get("id", "")) not in force_ids:
            continue
        source = str(record.get("source_original") or record.get("original") or "")
        current = str(record.get("original") or "")
        reasons = ["手动重新审核"] if selected_mode else review_risks(source, current)
        if not selected_mode and mode != "all" and not reasons:
            continue
        if not selected_mode and record.get("review_signature") == signature and record.get("review_status") == "通过":
            continue
        key = _hash(normalize_key(source), normalize_key(current))[:24]
        group = groups.setdefault(key, {"id": key, "source": source, "before": current, "text": current, "reasons": reasons, "indexes": []})
        group["indexes"].append(index)

    items = checkpoint.setdefault("items", {})
    for key, group in groups.items():
        items.setdefault(key, {k: v for k, v in group.items() if k != "indexes"} | {"status": "待审校", "count": len(group["indexes"])})
        for record_index in group["indexes"]:
            records[record_index]["review_status"] = "有风险"
    pending = [group for key, group in groups.items() if items.get(key, {}).get("status") != "通过"]
    total = len(pending)
    effective_batch = max(1, min(30, int(batch_size or 20)))
    db = _review_db(Path(db_path))
    reviewed = cached = failed = api_calls = api_error_count = 0

    def emit(percent, message, updates=None, samples=None, upcoming_ids=None):
        if progress:
            progress({
                "phase": "api-review",
                "percent": percent,
                "message": message,
                "updates": updates or [],
                "samples": samples or [],
                "upcoming_ids": upcoming_ids if upcoming_ids is not None else [],
                "current": reviewed,
                "total": total,
            })

    scope_label = "选中项重新审核" if selected_mode else ("全部重审" if mode == "all" else "仅风险项")
    emit(1, f"已筛选 {total:,} 条待审校文本（{scope_label}）")
    _atomic_json(checkpoint_path, checkpoint)
    _write_full_xlsx(reviewed_xlsx, xlsx_order, xlsx_texts)
    try:
        for start in range(0, total, effective_batch):
            batch = pending[start:start + effective_batch]
            batch_ids = []
            for group in batch:
                batch_ids.extend(str(records[index].get("id", "")) for index in group["indexes"][:3])
            batch_samples = [
                f"原：{group['source']}  |  现：{group['before']}"
                for group in batch[:3]
            ]
            first_text = re.sub(r"\s+", " ", batch[0]["before"]).strip()[:48] if batch else ""
            emit(
                2 + start * 90 / max(1, total),
                f"正在审校 {start + 1:,}-{min(total, start + len(batch)):,} / {total:,}：{first_text}",
                samples=batch_samples,
                upcoming_ids=batch_ids[:60],
            )
            api_batch = []
            api_errors = {}
            results = {}
            for group in batch:
                cache_key = _hash(group["source"], group["before"])
                row = db.execute("SELECT target_text FROM api_review_cache WHERE cache_key=?", (cache_key,)).fetchone() if mode != "all" and not selected_mode else None
                if row:
                    results[group["id"]] = row[0]
                    cached += 1
                else:
                    api_batch.append({"id": group["id"], "text": f"越南语原文：{group['source']}\n当前译文：{group['before']}"})
            if api_batch:
                translated, call_count, api_errors = _call_review_resilient(
                    base_url,
                    api_key,
                    model,
                    REVIEW_PROMPT + "\n\n补充偏好：\n" + str(prompt or ""),
                    api_batch,
                    notify=lambda message: emit(
                        2 + start * 90 / max(1, total),
                        message,
                        samples=batch_samples,
                        upcoming_ids=batch_ids[:60],
                    ),
                )
                api_calls += call_count
                api_error_count += len(api_errors)
                results.update({str(row.get("id", "")): str(row.get("text", "")) for row in translated})

            live_updates = []
            for group in batch:
                target = results.get(group["id"], "").strip()
                ok, reason = _validate_translation(group["source"], target)
                if (not ok and reason == "same-as-source" and CJK_RE.search(target)
                        and normalize_key(group["source"]) == normalize_key(group["before"])):
                    ok, reason = True, "ok"
                token_ok, token_reason = validate_translation(group["source"], target)
                if not ok or not token_ok or review_risks(group["source"], target):
                    failed += 1
                    items[group["id"]]["status"] = "失败"
                    items[group["id"]]["error"] = api_errors.get(group["id"]) or (reason if not ok else (token_reason if not token_ok else "审校后仍有风险"))
                    for record_index in group["indexes"]:
                        record = records[record_index]
                        record["review_status"] = "有风险"
                        live_updates.append({"id": record.get("id"), "text": record.get("original", ""), "review_status": "有风险"})
                    continue
                for record_index in group["indexes"]:
                    record = records[record_index]
                    if record.get("source_original") is None:
                        record["source_original"] = record.get("original", "")
                    record["original"] = target
                    record["language"] = score_text(target)[1]
                    record["status"] = "已翻译" if target != record.get("source_original", "") else "未翻译"
                    record["review_signature"] = signature
                    record["review_status"] = "通过"
                    record["reviewed_at"] = _now()
                    live_updates.append({"id": record.get("id"), "text": target, "language": record["language"], "status": record["status"], "review_status": "通过"})
                    mapping_id = record_to_mapping.get(record.get("id"))
                    if mapping_id:
                        xlsx_texts[mapping_id] = target
                items[group["id"]].update({"text": target, "status": "通过", "error": ""})
                reviewed += 1
                cache_key = _hash(group["source"], group["before"])
                now = _now()
                db.execute("""INSERT INTO api_review_cache(cache_key,source_text,input_text,target_text,created_at,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET target_text=excluded.target_text,updated_at=excluded.updated_at""",
                    (cache_key, group["source"], group["before"], target, now, now))

            checkpoint.update({"updated_at": _now(), "model": model, "reviewed": reviewed, "cached": cached, "failed": failed})
            _atomic_json(records_path, records)
            _atomic_json(checkpoint_path, checkpoint)
            _write_full_xlsx(reviewed_xlsx, xlsx_order, xlsx_texts)
            db.commit()
            percent = min(94, 2 + min(total, start + len(batch)) * 92 / max(1, total))
            for offset in range(0, len(live_updates), 300):
                emit(percent, f"已审校 {min(total, start + len(batch)):,} / {total:,} 条，结果已保存", live_updates[offset:offset + 300], batch_samples, batch_ids[:60])
    finally:
        db.commit()
        db.close()

    remaining = sum(1 for key in groups if items.get(key, {}).get("status") != "通过")
    _write_review_log(review_log, items)
    report = {
        "pak": pak_name,
        "mode": "selected" if selected_mode else mode,
        "selected_ids": len(force_ids),
        "model": model,
        "candidates": total,
        "reviewed": reviewed,
        "cached": cached,
        "failed": failed,
        "remaining": remaining,
        "api_calls": api_calls,
        "api_errors": api_error_count,
        "batch_size": effective_batch,
        "checkpoint": str(checkpoint_path),
        "reviewed_xlsx": str(reviewed_xlsx),
        "review_log": str(review_log),
        "records_path": str(records_path),
    }
    report_path = output_dir / ("review_selection_report.json" if selected_mode else "review_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(95, f"API 审校完成：通过 {reviewed:,}，失败 {failed:,}，正在生成构建资源", upcoming_ids=[])
    return report
