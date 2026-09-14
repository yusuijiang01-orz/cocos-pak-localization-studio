#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import ast
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from localization_analyzer import score_text
from localization_tm import add_tm, init_db, lookup_api_cache, normalize_key

ALIAS_TOKEN_RE = re.compile(r"(?:[♥♣♦♠★☆●■▲◆※◎◇□△▽◁▷]|◈\d+◈)")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
VI_MARK_RE = re.compile(r"[ăâêôơưđĂÂÊÔƠƯĐáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵÁÀẢÃẠẤẦẨẪẬẮẰẲẴẶÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ]")
VI_WORD_RE = re.compile(
    r"\b(?:không|được|người|nhiệm|phòng|ngự|độ|bền|đẳng|cấp|yêu|cầu|"
    r"trang|bị|công|lực|giao|dịch|vứt|bỏ|tiệm|mua|bán|vật|phẩm|kỹ|năng|"
    r"thập|đại|cao|thủ|thiết|mã|băng|qua)\b",
    re.IGNORECASE,
)
MOJIBAKE_RE = re.compile(r"[�]|(?:Ã|Â|Ä|å|Ð|ð|¤|¥|¦|§|©|ª|«|¬|®|¯|µ|¶)")
SUSPICIOUS_TARGET_RE = re.compile(
    r"(?:袋礼能动天|吃醒吃|b极期|们的们|在美国购买任何东西|玩立即|"
    r"美国女性成为人们的焦点|赎回我们的生命)"
)
LATIN_KEEP_RE = re.compile(
    r"\b(?:NPC|PK|Pet|VIP|Boss|GM|OTP|SMS|App|ID|Lv|lv|HP|MP|EXP|"
    r"UI|URL|CDN|PAK|INI|TXT|TSV|CSV|F\d+)\b"
)


# 核心游戏术语表：越文 -> 简体中文（LLM 必须严格遵守，保证全文术语一致，不得另译或生造）
CORE_GLOSSARY = (
    "Máy chủ=服务器; Nhiệm vụ=任务; Đẳng cấp=等级; Cấp=等级; Kinh nghiệm=经验;"
    "Trang bị=装备; Kỹ năng=技能; Môn phái=门派; Vũ khí=武器; Vật phẩm=物品; Đạo cụ=道具;"
    "Thuộc tính=属性; Tấn công=攻击; Phòng thủ=防御; Phòng ngự=防御; Nội lực=内力;"
    "Pháp lực=法力; Sinh lực=生命; Lực chiến=战力; Độ bền=耐久; Trọng lượng=负重;"
    "Bang hội=帮会; Đội ngũ=队伍; Bạn bè=好友; Hệ thống=系统; Thiết lập=设置; Trợ giúp=帮助;"
    "Đăng nhập=登录; Đăng ký=注册; Đăng xuất=退出登录; Thoát=退出; Kết nối=连接;"
    "Tiếp tục=继续; Hủy bỏ=取消; Quay lại=返回; Xác nhận=确认; Đồng ý=同意;"
    "Mua=购买; Bán=出售; Sử dụng=使用; Giao dịch=交易; Nhận thưởng=领取奖励;"
    "Hoàn thành=完成; Chúc mừng=恭喜; Thăng cấp=升级; Chính phái=正派; Tà phái=邪派;"
    "Thiếu Lâm=少林; Thiên Vương=天王; Đường Môn=唐门; Na Tra=哪吒; Khương Tử Nha=姜子牙;"
    "Dương Tiễn=杨戬; Lôi Chấn Tử=雷震子; Nữ Oa=女娲; Trụ Vương=纣王; Đắc Kỷ=妲己; Xi Vưu=蚩尤;"
    "Đát Kỷ=妲己; Từ Hàng Đạo Nhân=慈航道人; Văn Trọng=闻仲; Bá Ấp Khảo=伯邑考; Tỷ Can=比干; Bích Tiêu=碧霄; Ngao Quảng=敖广; Long Cát=龙吉;"
    "Nguyên Thần=元神; Linh Tướng=灵将; Danh Thần=神将; Thần Khí=神器; Pháp Bảo=法宝; Pháp Bảo Tinh Phách=法宝精魄; Kỹ năng Pháp Tượng=法相技能; Ngũ Hành=五行;"
    "Tông Môn=宗门; Hệ phái=流派; Trân Bảo Các=珍宝阁; Đấu giá=拍卖; Thập Tuyệt Trận=十绝阵; Thiên Cực Chiến=天极战; Sách Phong Thần=封神书册; Chú Ấn=符印; Tu Luyện=修炼;"
    "Nguyên Bảo=元宝; Huyền Tinh=玄晶; Linh Thạch=灵石; Nguyên liệu=材料; Phẩm chất=品质; Thuộc tính chính=主属性; Thuộc tính phụ=副属性;"
    "Sát thương=伤害; Pháp công=法攻; Vật công=物攻; Né tránh=闪避; Hồi phục=回复; Trị liệu=治疗; Bạo kích=暴击; Tê liệt=麻痹;"
    "Khống chế=控制; Miễn khống chế=免疫控制; Lá chắn=护盾; Xác suất=概率; Chính xác=精准; Phá phòng=破防; Cấp sao=星级; Cường hóa=强化; Tăng cấp=升级;"
    "Quái=怪物; Quái thường=普通怪; Quái tinh anh=精英怪; Kiếm Xỉ Hổ=剑齿虎; Kiếm Xỉ Hổ Vương=剑齿虎王; Bản Đồ=地图; Bản Đồ Luyện Cấp=练级地图;"
    "Hỏa Tiêm Thương=火尖枪; Hỗn Thiên Lăng=混天绫; Càn Khôn Quyển=乾坤圈; Thanh Tịnh Lưu Ly Bình=清净琉璃瓶; Tam Tiêm Lưỡng Nhận Đao=三尖两刃刀; Kim Giao Tiễn=金蛟剪;"
    "Bình Lưu Ly=琉璃瓶; Càn Khôn khuyên=乾坤圈; Ngũ Quang Thạch=五光石; Hình Thiên ấn=刑天印; Càn Khôn Xích=乾坤尺; Hỏa Long Tiêu=火龙镖; Âm Dương Kính=阴阳镜; Túi Ngô Phong=蜈蜂袋; Bích Tỳ Bà=碧琵琶; Kim Cang Phách=金刚帕; Thái Dương Châm=太阳针; Bàn Cổ phướn=盘古幡; Dây Phược Long=缚龙索; ấm Vạn Nha=万鸦壶; Dây Khổn Tiên=捆仙绳; Chấn Thiên Cung=震天弓; Kim Bát Vu=金钵盂; Linh Lung Tháp=玲珑塔; Toàn Tâm Đinh=攒心钉; Hồng Hồ Lô=红葫芦; Lạc Hồn Chung=落魂钟; Ngọc Hư Phù=玉虚符; Cọc Độn Long=遁龙桩; Kính Chiếu Yêu=照妖镜; Phong Hỏa Luân=风火轮; Kim Quang Tỏa=金光锉; Hạnh Hoàng Kỳ=杏黄旗; Bạch Cốt phướn=白骨幡; Định Phong Châu=定风珠;"
    "Thắp sáng=点亮; Tách=分解; Phân Giải=分解; Trang Trí=装饰; Liên kết=链接"
)


DEFAULT_PROMPT = (
    "你是资深游戏本地化翻译员，负责把越南语武侠/仙侠/封神题材 MMORPG 的 UI 文本与剧情对白，"
    "翻译成符合中国大陆玩家阅读习惯的简体中文。"
    "\n\n核心术语表（全文必须严格遵守，保证术语统一，不得另译或音译替换）：\n"
    + CORE_GLOSSARY
    + "\n\n翻译要求：\n"
    "1. 只输出译文，不解释、不追加任何说明。\n"
    "2. 完整保留原文字符中的所有占位符与特殊符号（如 ◈1◈、♥ ♣ ♦ ♠ ★ ☆ ● ■ ▲ ◆ ※ ◎ ◇ □ △ ▽），"
    "数量与顺序必须与原文一致。◈数字◈ 必须逐字原样输出，绝不能改成大括号、书名号、圆括号或全角符号。\n"
    "3. 完整保留代码、函数名、变量名、URL、文件路径、数字、日期和格式标记，不翻译、不修改字符、顺序或大小写。\n"
    "4. UI 按钮与标签要精简，例如 Chơi ngay 译为 立即游玩，Bạn quên mật khẩu? 译为 忘记密码？\n"
    "5. 剧情对白采用仙侠/武侠国风口吻，自然流畅，避免机翻腔与逐字硬译；人名、地名、门派、装备、技能名优先套用术语表，不要生造。\n"
    "6. 同一术语全文保持一致译法。\n"
    "7. 严格只返回 JSON 数组，格式：[{\"id\":\"1\",\"text\":\"译文\"}]，id 必须与输入逐条对应且不得遗漏。"
)


def _request_json(url: str, api_key: str, payload: dict, timeout: int = 90) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"API HTTP {exc.code}: {detail[:1000]}") from exc
    return json.loads(body)


def _chat_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("BaseURL 不能为空")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def fetch_models(base_url: str, api_key: str) -> list[str]:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if not base.endswith("/v1"):
        base = base + "/v1"
    request = urllib.request.Request(
        base + "/models",
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"获取模型失败 HTTP {exc.code}: {detail[:1000]}") from exc
    models = []
    for item in data.get("data", []):
        if isinstance(item, str):
            model_id = item
        else:
            model_id = item.get("id") or item.get("name") or item.get("model")
        if model_id:
            models.append(str(model_id))
    return sorted(models)


def _extract_json_array(text: str) -> list[dict]:
    value = (text or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    candidates = [value]
    match = re.search(r"\[[\s\S]*\]", value)
    if match and match.group(0) != value:
        candidates.append(match.group(0))
    parsed = None
    last_error = None
    for candidate in candidates:
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            parsed = json.loads(repaired)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
        try:
            literal = ast.literal_eval(repaired)
            if isinstance(literal, list):
                parsed = literal
                break
        except (ValueError, SyntaxError):
            pass
    if parsed is None:
        raise ValueError(f"模型返回的 JSON 无法解析：{last_error}; 返回片段：{value[:300]}")
    if not isinstance(parsed, list):
        raise ValueError("模型返回不是 JSON 数组")
    return parsed


def _call_translate(base_url: str, api_key: str, model: str, prompt: str, rows: list[dict], temperature: float, timeout: int = 90) -> list[dict]:
    content = json.dumps([{"id": row["id"], "text": row["text"]} for row in rows], ensure_ascii=False)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt or DEFAULT_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": temperature,
    }
    data = _request_json(_chat_url(base_url), api_key, payload, timeout=timeout)
    message = data.get("choices", [{}])[0].get("message", {})
    return _extract_json_array(message.get("content", ""))


def _needs_translation(text: str) -> bool:
    return score_text(text or "")[1] in ("vi", "mixed")


def _alias_tokens(text: str) -> list[str]:
    return ALIAS_TOKEN_RE.findall(str(text or ""))


def _cjk_count(text: str) -> int:
    return len(CJK_RE.findall(str(text or "")))


def _latin_noise(text: str) -> str:
    value = str(text or "")
    value = LATIN_KEEP_RE.sub(" ", value)
    value = ALIAS_TOKEN_RE.sub(" ", value)
    return value


def _validate_translation(source: str, target: str) -> tuple[bool, str]:
    source = str(source or "")
    target = str(target or "").strip()
    if not target:
        return False, "empty"
    if normalize_key(source) == normalize_key(target):
        return False, "same-as-source"
    if _alias_tokens(source) != _alias_tokens(target):
        return False, "alias-token-mismatch"
    if MOJIBAKE_RE.search(target):
        return False, "mojibake"
    if SUSPICIOUS_TARGET_RE.search(target):
        return False, "known-bad-output"

    source_lang = score_text(source)[1]
    if source_lang not in ("vi", "mixed"):
        return True, "ok"

    if _cjk_count(target) == 0:
        return False, "no-chinese"
    source_words = re.findall(r"[A-Za-zÀ-ỹĐđ]+", source)
    if len(source_words) >= 2 and len(source) >= 8 and _cjk_count(target) <= 1:
        return False, "too-short"
    noisy = _latin_noise(target)
    if VI_MARK_RE.search(noisy) or VI_WORD_RE.search(noisy):
        return False, "vietnamese-left"
    return True, "ok"


def _split_alias_wrappers(text: str) -> tuple[list[str], str, list[str]]:
    value = str(text or "")
    prefix = []
    suffix = []
    while True:
        match = ALIAS_TOKEN_RE.match(value)
        if not match:
            break
        prefix.append(match.group(0))
        value = value[match.end():]
    while True:
        matches = list(ALIAS_TOKEN_RE.finditer(value))
        if not matches or matches[-1].end() != len(value):
            break
        match = matches[-1]
        suffix.insert(0, match.group(0))
        value = value[:match.start()]
    return prefix, value, suffix


def _lookup_with_core_cache(db, text: str) -> tuple[str | None, str | None]:
    hit, kind = lookup_api_cache(db, text)
    if hit is not None:
        return hit, kind
    prefix, core, suffix = _split_alias_wrappers(text)
    if not core or (not prefix and not suffix):
        return None, None
    hit, kind = lookup_api_cache(db, core)
    if hit is None:
        return None, None
    return "".join(prefix) + hit + "".join(suffix), kind


def _learn_api_cache(db, source_text: str, target_text: str) -> int:
    learned = 0
    pairs = [(source_text, target_text)]
    s_prefix, s_core, s_suffix = _split_alias_wrappers(source_text)
    t_prefix, t_core, t_suffix = _split_alias_wrappers(target_text)
    if s_core and t_core and s_core != source_text and s_prefix == t_prefix and s_suffix == t_suffix:
        pairs.append((s_core, t_core))
    for source, target in pairs:
        if normalize_key(target) == normalize_key(source):
            continue
        try:
            add_tm(db, source, target, "api-auto")
            learned += 1
        except ValueError:
            pass
    return learned


def translate_merged_csv(
    input_csv: Path,
    output_csv: Path,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str = "",
    batch_size: int = 30,
    temperature: float = 0.1,
    db_path: Path | None = None,
    progress=None,
    progress_context: dict | None = None,
) -> dict:
    input_csv = Path(input_csv)
    output_csv = Path(output_csv)
    if not model:
        raise ValueError("模型不能为空")
    with input_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if "id" not in fields or "text" not in fields:
            raise ValueError("合并 CSV 必须包含 id,text 两列")
        rows = list(reader)
    total = len(rows)
    changed = skipped = failed = tm_hits = api_calls = learned_tm = 0
    rejected = 0
    db = init_db(Path(db_path)) if db_path else None

    def emit_progress(payload: dict):
        if progress:
            progress({**payload, **(progress_context or {})})

    def translate_todo_adaptive(todo_rows: list[dict], batch_from: int, batch_to: int, preferred_size: int) -> tuple[list[dict] | None, Exception | None, int]:
        nonlocal api_calls
        if not todo_rows:
            return [], None, 0
        sample = [str(row.get("text", ""))[:80] for row in todo_rows[:5]]
        emit_progress({
            "phase": "api-csv",
            "state": "requesting",
            "percent": round((batch_from - 1) * 100 / max(1, total), 2),
            "current_rows": batch_from - 1,
            "total_rows": total,
            "batch_from": batch_from,
            "batch_to": batch_to,
            "batch_rows": len(todo_rows),
            "input_csv": input_csv.name,
            "samples": sample,
            "message": f"API 正在请求 {input_csv.name}：第 {batch_from:,}-{batch_to:,} 行，本批 {len(todo_rows):,} 条",
        })
        last_error = None
        for attempt in range(2):
            try:
                translated = _call_translate(base_url, api_key, model, prompt, todo_rows, float(temperature))
                api_calls += 1
                return translated, None, len(todo_rows)
            except Exception as exc:
                last_error = exc
                emit_progress({
                    "phase": "api-csv",
                    "state": "retrying",
                    "percent": round((batch_from - 1) * 100 / max(1, total), 2),
                    "current_rows": batch_from - 1,
                    "total_rows": total,
                    "batch_from": batch_from,
                    "batch_to": batch_to,
                    "batch_rows": len(todo_rows),
                    "input_csv": input_csv.name,
                    "samples": sample,
                    "message": f"API 批次失败，正在重试 {attempt + 1}/2：{str(exc)[:160]}",
                })
                time.sleep(1.5 * (attempt + 1))
        if len(todo_rows) <= max(1, preferred_size):
            return None, last_error, len(todo_rows)
        mid = len(todo_rows) // 2
        left_rows = todo_rows[:mid]
        right_rows = todo_rows[mid:]
        emit_progress({
            "phase": "api-csv",
            "state": "split",
            "percent": round((batch_from - 1) * 100 / max(1, total), 2),
            "current_rows": batch_from - 1,
            "total_rows": total,
            "input_csv": input_csv.name,
            "message": f"API 批次过大，自动拆分：{len(todo_rows)} -> {len(left_rows)} + {len(right_rows)}",
        })
        left, left_error, _ = translate_todo_adaptive(left_rows, batch_from, batch_from + len(left_rows) - 1, preferred_size)
        right, right_error, _ = translate_todo_adaptive(right_rows, batch_from + len(left_rows), batch_to, preferred_size)
        merged = []
        if left:
            merged.extend(left)
        if right:
            merged.extend(right)
        if merged:
            return merged, None, len(todo_rows)
        return None, right_error or left_error or last_error, len(todo_rows)

    try:
      requested_batch_size = int(batch_size or 0)
      effective_batch_size = min(total, 200) if requested_batch_size <= 0 else max(1, requested_batch_size)
      fallback_floor = 25
      for start in range(0, total, effective_batch_size):
        chunk = rows[start : start + effective_batch_size]
        todo = []
        for row in chunk:
            source_text = row.get("text", "")
            if not _needs_translation(source_text):
                skipped += 1
                continue
            if db is not None:
                hit, _kind = _lookup_with_core_cache(db, source_text)
                if hit is not None:
                    ok, reason = _validate_translation(source_text, hit)
                    if not ok:
                        row["cache_error"] = reason
                    else:
                        row["text"] = hit
                        tm_hits += 1
                        changed += 1
                        continue
            todo.append(row)
        if not todo:
            pass
        else:
            translated, last_error, _attempted_rows = translate_todo_adaptive(
                todo,
                start + 1,
                start + len(chunk),
                fallback_floor,
            )
            if translated is None:
                failed += len(todo)
                for row in todo:
                    row["error"] = str(last_error)
            else:
                by_id = {str(item.get("id", "")).strip(): str(item.get("text", "")) for item in translated if item.get("id") is not None}
                for row in todo:
                    target = by_id.get(str(row.get("id", "")).strip())
                    if target and target != row.get("text", ""):
                        source_text = row.get("text", "")
                        ok, reason = _validate_translation(source_text, target)
                        if not ok:
                            row["error"] = f"quality rejected: {reason}"
                            failed += 1
                            rejected += 1
                            continue
                        row["text"] = target
                        changed += 1
                        if db is not None and normalize_key(target) != normalize_key(source_text):
                            learned_tm += _learn_api_cache(db, source_text, target)
                    elif not target:
                        failed += 1
                    else:
                        skipped += 1
        if progress:
            done = min(total, start + len(chunk))
            emit_progress({
                "phase": "api-csv",
                "state": "batch-done",
                "percent": round(done * 100 / max(1, total), 2),
                "current_rows": done,
                "total_rows": total,
                "input_csv": input_csv.name,
                "message": f"API 已处理 {input_csv.name}：{done:,} / {total:,} 行，成功 {changed:,}，失败/拒收 {failed:,}",
            })
    finally:
        if db is not None:
            db.commit()
            db.close()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out_fields = ["id", "text"]
    with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=out_fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "input_csv": str(input_csv),
        "output_csv": str(output_csv),
        "rows": total,
        "changed": changed,
        "skipped": skipped,
        "failed": failed,
        "rejected": rejected,
        "tm_hits": tm_hits,
        "api_calls": api_calls,
        "learned_tm": learned_tm,
        "model": model,
        "batch_size": int(batch_size or 0),
        "effective_batch_size": effective_batch_size,
    }
    report_path = output_csv.with_name(output_csv.stem + "_api_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def translate_csv_dir(
    input_dir: Path,
    output_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str = "",
    batch_size: int = 30,
    temperature: float = 0.1,
    db_path: Path | None = None,
    progress=None,
) -> dict:
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    files = sorted(p for p in input_dir.glob("*.csv") if p.is_file() and not p.name.startswith("_"))
    if not files:
        raise ValueError(f"没有找到待翻译分片 CSV：{input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    total_rows = 0
    for p in files:
        with p.open("r", encoding="utf-8-sig", newline="") as stream:
            total_rows += max(0, sum(1 for _ in csv.DictReader(stream)))
    done_rows = 0
    for index, source in enumerate(files, 1):
        target = output_dir / source.name
        if progress:
            progress({
                "phase": "api-csv",
                "percent": round(done_rows * 100 / max(1, total_rows), 2),
                "current_rows": done_rows,
                "total_rows": total_rows,
                "message": f"API 正在翻译分片 {index}/{len(files)}：{source.name}",
            })
        report = translate_merged_csv(
            source,
            target,
            base_url,
            api_key,
            model,
            prompt,
            batch_size,
            temperature,
            db_path,
            progress=lambda p: progress({
                **p,
                "percent": round((done_rows + p.get("current_rows", 0)) * 100 / max(1, total_rows), 2),
                "current_rows": done_rows + p.get("current_rows", 0),
                "total_rows": total_rows,
                "message": f"分片 {index}/{len(files)} {p.get('message', source.name)}",
            }) if progress else None,
            progress_context={"file_index": index, "file_count": len(files)},
        )
        reports.append(report)
        done_rows += report.get("rows", 0)
        if progress:
            progress({
                "phase": "api-csv",
                "percent": round(done_rows * 100 / max(1, total_rows), 2),
                "current_rows": done_rows,
                "total_rows": total_rows,
                "message": f"API 已完成分片 {index}/{len(files)}：{source.name}",
            })
    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "files": len(files),
        "rows": sum(r.get("rows", 0) for r in reports),
        "changed": sum(r.get("changed", 0) for r in reports),
        "skipped": sum(r.get("skipped", 0) for r in reports),
        "failed": sum(r.get("failed", 0) for r in reports),
        "tm_hits": sum(r.get("tm_hits", 0) for r in reports),
        "api_calls": sum(r.get("api_calls", 0) for r in reports),
        "learned_tm": sum(r.get("learned_tm", 0) for r in reports),
        "model": model,
        "reports": reports,
    }
    (output_dir / "_api_translation_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
