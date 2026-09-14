#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from localization_tm import validate_tokens


CJK_RE = re.compile(r"[\u3400-\u9fff]")
VI_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạắằẳẵặấầẩẫậèéẻẽẹếềểễệìíỉĩịòóỏõọốồổỗộớờởỡợùúủũụứừửữựỳỷỹýỵ]")

VISIBLE_HEADERS = {
    "名称", "名字", "Name", "ItemName", "WeaponName", "EquipName",
    "说明文字", "说明", "描述", "简介", "Intro", "Desc", "Description",
    "Text", "文本", "Title", "标题", "内容", "任务", "对白",
}

REPLACEMENTS = [
    ("Tinh Quân", "星君"),
    ("Huyền Thiết", "玄铁"),
    ("Hỗn Nguyên", "混元"),
    ("Chấn Lôi", "震雷"),
    ("Thần Binh", "神兵"),
    ("Thần binh", "神兵"),
    ("Thương Khung", "苍穹"),
    ("Đoạn Nhạc", "断岳"),
    ("Chiến Ngoa", "战靴"),
    ("Đầu Khôi", "头盔"),
    ("Đạo Bào", "道袍"),
    ("Khải", "铠"),
    ("Hộ Giáp", "护甲"),
    ("Huyết Giáp", "血甲"),
    ("Yêu Đái", "腰带"),
    ("Chí Tôn Thương Chu Cung", "至尊商周弓"),
    ("Cung Kinh Trập", "惊蛰弓"),
    ("Cung Tồi Sơn", "摧山弓"),
    ("Cung Càn Khôn", "乾坤弓"),
    ("Cung Liệt Địa", "裂地弓"),
    ("Cung Nhiếp Hồn", "摄魂弓"),
    ("Cung Săn", "猎弓"),
    ("Cung săn", "猎弓"),
    ("Đan Đỉnh Hạc", "丹顶鹤"),
    ("Thừa Phong Hạc", "乘风鹤"),
    ("Phiêu Tuyết Hạc", "飘雪鹤"),
    ("Độc Giác Ma Tranh", "独角魔狰"),
    ("Liệt Diễm Ma Tranh", "烈焰魔狰"),
    ("Ưng Chuẩn", "鹰隼"),
    ("Thiên Tai", "天灾"),
    ("Tiên Ma", "仙魔"),
    ("Thất Trần Trai", "七尘斋"),
    ("Thất Bảo Kim Liên", "七宝金莲"),
    ("Tam Sinh Thạch", "三生石"),
    ("Kim Sơn", "金山"),
    ("Pháp bảo", "法宝"),
    ("pháp bảo", "法宝"),
    ("Pháp Bảo", "法宝"),
    ("Phòng ngự", "防御"),
    ("phòng ngự", "防御"),
    ("phòng thủ", "防御"),
    ("tấn công", "攻击"),
    ("sát thương", "伤害"),
    ("Lôi sát", "雷杀"),
    ("Thổ sát", "土杀"),
    ("Băng sát", "冰杀"),
    ("Hỏa sát", "火杀"),
    ("STCB", "基础伤害"),
    ("tinh lực", "精力"),
    ("lực", ""),
    ("Thăng cấp", "升级"),
    ("Nâng cấp", "升级"),
    ("Tiên Cấp", "仙级"),
    ("Ma Cấp", "魔级"),
    ("Cấp", "级"),
    ("Thiên", "天"),
    ("Thủy", "水"),
    ("Địa", "地"),
    ("Thổ", "土"),
    ("Phong", "风"),
    ("Khôi", "盔"),
    ("Quán", "冠"),
    ("Giáp", "甲"),
    ("Cân", "巾"),
    ("Liên", "链"),
    ("Trụ", "胄"),
    ("Ngoa", "靴"),
    ("Hài", "鞋"),
    ("Lý", "履"),
    ("Giáp Sĩ", "甲士"),
    ("Đạo Sĩ", "道士"),
    ("Dị Nhân", "异人"),
    ("Thần tiên", "神仙"),
    ("Thần Thú", "神兽"),
    ("Thần thú", "神兽"),
    ("Thú cưỡi", "坐骑"),
    ("Thú Cưỡi", "坐骑"),
    ("Cánh", "翅膀"),
    ("chim", "鸟"),
    ("Chim", "神鸟"),
    ("Thiên Giới", "天界"),
    ("Thiên cung", "天宫"),
    ("Hoàng Phi Hổ", "黄飞虎"),
    ("Thái Thượng Lão Quân", "太上老君"),
    ("Quang Minh", "光明"),
    ("bát quái", "八卦"),
    ("dược lực", "药力"),
    ("linh khí", "灵气"),
    ("thiên địa", "天地"),
    ("ngoài biển", "海外"),
    ("tiên đảo", "仙岛"),
    ("anh hùng hào kiệt", "英雄豪杰"),
    ("Anh hùng hào kiệt", "英雄豪杰"),
    ("Trùng Sinh", "重生"),
    ("Sinh Nhật", "生日"),
    ("Phong Thần", "封神"),
    ("ĐS", "重生"),
    ("GS", "甲士"),
    ("DN", "异人"),
    ("Ma Lễ Thanh", "魔礼青"),
    ("Thiên Tuyệt", "天绝"),
    ("Tia Sáng Mặt Trời", "太阳光"),
    ("Nguyên Thủy Thiên Tôn", "元始天尊"),
    ("Nam Cực Tiên Ông", "南极仙翁"),
    ("Địa Thủy Hỏa Phong", "地水火风"),
    ("Bích Du", "碧游"),
    ("Thiên Giới", "天界"),
    ("Vân Tiêu", "云霄"),
    ("Thần Tôn", "神尊"),
    ("Thông Thiên Giáo Chủ", "通天教主"),
    ("Nữ Oa Nương Nương", "女娲娘娘"),
    ("Ngọc Hư", "玉虚"),
    ("Hồng Quân", "鸿钧"),
    ("thuộc tính", "属性"),
    ("đạo cụ", "道具"),
    ("vũ khí", "武器"),
    ("Vũ khí", "武器"),
    ("Vũ Sĩ", "武士"),
    ("Sùng Thành", "崇城"),
    ("hộ thân", "护身"),
    ("được chế tạo thông qua", "通过"),
    ("được chế tạo bởi", "由"),
    ("chế tạo từ", "由"),
    ("dành tặng cho", "赠予"),
    ("Tặng Cho", "赠予"),
    ("tặng cho", "赠予"),
    ("khoá", "绑定"),
    ("khóa", "绑定"),
    ("Nhiệm vụ", "任务"),
    ("N.v", "任务"),
    ("N.Vụ", "任务"),
]


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(str(text or "")))


def has_vi(text: str) -> bool:
    return bool(VI_RE.search(str(text or "")))


def normalize_spaces(text: str) -> str:
    text = re.sub(r"\s+([，。）、])", r"\1", text)
    text = re.sub(r"（\s+", "（", text)
    text = re.sub(r"\s+）", "）", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def generic_replace(text: str) -> str:
    out = text
    for src, dst in REPLACEMENTS:
        out = out.replace(src, dst)
    out = out.replace(". ", "。").replace(".", "。")
    out = out.replace(" - ", " - ")
    out = re.sub(r"仙级\s*(\d+)", r"仙级\1", out)
    out = re.sub(r"魔级\s*(\d+)", r"魔级\1", out)
    return normalize_spaces(out)


def translate_rule(text: str) -> str:
    value = str(text or "").strip()
    if not value or has_cjk(value):
        return ""
    rebirth = re.search(r"\(\s*Trùng Sinh\s*(\d+)\s*\)$", value)
    rebirth_suffix = f" (重生{rebirth.group(1)})" if rebirth else ""
    value_without_rebirth = re.sub(r"\s*\(\s*Trùng Sinh\s*\d+\s*\)$", "", value)
    match = re.fullmatch(r"(.+?) trong bộ (.+?)\. Hỗ trợ (tấn công|phòng thủ)", value)
    if match:
        part, suit, role = match.groups()
        part_cn = generic_replace(part)
        suit_cn = generic_replace(suit)
        role_cn = "攻击" if role == "tấn công" else "防御"
        if part_cn and suit_cn and not has_vi(part_cn + suit_cn):
            return f"{suit_cn}套装{part_cn}。辅助{role_cn}"
    if value_without_rebirth == "Thần Thú được hấp thụ dược lực từ lò bát quái <c=orange>Thái Thượng Lão Quân<c>":
        return f"吸收八卦炉<c=orange>太上老君<c>药力的神兽{rebirth_suffix}"
    if value_without_rebirth == "Hấp thụ dược lực từ lò bát quái <c=green>Thái Thượng Lão Quân<c>":
        return f"吸收八卦炉<c=green>太上老君<c>药力{rebirth_suffix}"
    mount_phrases = {
        "Thú cưỡi của Thần tiên": "神仙坐骑",
        "Thần Thú Thiên Giới": "天界神兽",
        "Cánh có thể bay tận trời xanh": "可翱翔青天的翅膀",
        "Loại chim có thể chở người trên lưng": "可载人飞行的神鸟",
        "Thần hạc của đỉnh Quang Minh": "光明顶神鹤",
        "Anh hùng hào kiệt mới cưỡi được": "英雄豪杰方可骑乘",
        "<c=yel>*Thú Sinh Nhật Phong Thần (ĐS)*<c>": "<c=yel>*封神生日坐骑(重生)*<c>",
        "Chim thần đến từ tiên đảo ngoài biển": "来自海外仙岛的神鸟",
        "Thú thần của Thiên cung, tượng trưng cho sự may mắn": "天宫神兽，象征吉祥",
        "Mang linh khí của thiên địa tối cao": "蕴含至高天地灵气",
        "Cánh thần kỳ, bay nhanh như gió": "神奇羽翼，疾飞如风",
        "Thú Cưỡi của Hoàng Phi Hổ, ngày đi vạn dặm": "黄飞虎的坐骑，日行万里",
        "Khi tên rời cung, sấm chớp quấn quanh, mang thiên uy giáng hạ - thú rừng gào khóc mà bỏ chạy tán loạn": "箭离弦时雷电缠绕，天威降临，野兽哀号四散",
        "Ngọn núi nơi mũi tên lướt qua, trong khoảnh khắc hóa thành tro bụi": "箭锋掠过之山，顷刻化为灰烬",
        "Loài Phụng Hoàng sống ở cung Diêu Trì": "栖于瑶池宫的凤凰",
        "Có thể bay xuyên tam giới": "可飞越三界",
        "Nguyên Thủy Thiên Tôn ban cho Khương Tử Nha": "元始天尊赐予姜子牙之物",
        "Một trong tứ đại thần thú, tượng trưng cho liệt hỏa hủy diệt trời đất": "四大神兽之一，象征焚天灭地的烈火",
        "Đôi cánh mà bao nhiêu dũng tướng đều mơ ước được sở hữu": "无数勇将梦寐以求的羽翼",
        "<c=green>*Thú Sinh Nhật Phong Thần (GS)*<c>": "<c=green>*封神生日坐骑(甲士)*<c>",
        "<c=pink>*Thú Sinh Nhật Phong Thần (DN)*<c>": "<c=pink>*封神生日坐骑(异人)*<c>",
        "Giáp Sĩ tác chiến thường cưỡi": "甲士作战常骑之物",
        "Tướng quân tác chiến thường cưỡi": "将军作战常骑之物",
        "Đại tướng tác chiến thường cưỡi": "大将作战常骑之物",
        "Muôn thú chi vương hung mãnh vô song, người phàm khó cưỡi được": "万兽之王，凶猛无双，凡人难以驾驭",
        "Xuất xứ từ đại hoang mạc phía Bắc, hung dữ dị thường": "出自北方大荒漠，异常凶猛",
        "<c=cyan>*Đan Đỉnh Hạc*<c>": "<c=cyan>*丹顶鹤*<c>",
        "Thần hạc trên đỉnh Quang Minh": "光明顶神鹤",
        "<c=cyan>*Thừa Phong Hạc*<c>": "<c=cyan>*乘风鹤*<c>",
        "<c=cyan>*Phiêu Tuyết Hạc*<c>": "<c=cyan>*飘雪鹤*<c>",
        "Bắc Minh Đại Ngư hóa thành": "北冥大鱼所化",
        "Quái thú sống nhờ hỏa diệm": "依火焰而生的异兽",
        "Cánh đẹp ngọc bích": "碧玉般美丽的羽翼",
        "Cánh đẹp mây hồng": "红霞般美丽的羽翼",
        "Cánh có sức mạnh của phong lôi": "蕴含风雷之力的羽翼",
        "Bay qua để lại mùi Trầm hương thoang thoảng": "飞过时留下淡淡沉香",
        "Có thể bay tận mây xanh": "可飞上青云",
        "Thần binh hiếm có - khi kéo cung, không gian vặn xoắn, thời gian rối loạn; Một mũi tên bắn ra, cắt đôi cả chiến trường": "稀世神兵，拉弓时空间扭曲、时间紊乱；一箭射出，可斩断整个战场",
        "Tạng Dương": "藏羊",
        "Dê Tạng đến từ sa mạc, có sức mạnh và tốc độ vô thượng.": "来自沙漠的藏羊，拥有无上的力量与速度。",
        "Tiếng nó vang như đá nổ, rền vang như sấm sét, nơi nó xuất hiện, khói lửa dậy khắp sơn lâm.": "其声如裂石，轰鸣若雷霆；所至之处，山林烟火四起。",
        "Hình dáng như báo đỏ, toàn thân rực sáng, năm đuôi kéo dài, một sừng nhô cao - quanh mình luôn bao phủ khói đen.": "形似赤豹，通体赤亮，五尾修长，独角高耸，周身常绕黑烟。",
        "Dị thú quý hiếm đến từ phương Tây, có thể tung hoành giữa trời đất.": "来自西方的稀有异兽，可纵横天地之间。",
        "Chỉ anh hùng kiệt xuất mới đủ sức điều khiển nó.": "唯有卓绝英雄方能驾驭。",
    }
    qah_match = re.fullmatch(r"Anh hùng hào kiệt Quần Anh Hội mới cưỡi được Trùng Sinh (\d+)", value)
    if qah_match:
        return f"群英会英雄豪杰方可骑乘 重生{qah_match.group(1)}"
    if value_without_rebirth in mount_phrases:
        return mount_phrases[value_without_rebirth] + rebirth_suffix
    if re.search(r"(Tinh Quân|Thần Binh|Tiên Cấp|Ma Cấp|Chiến Ngoa|Đầu Khôi|Hỗn Nguyên|Huyền Thiết|Chấn Lôi)", value):
        out = generic_replace(value)
        return out if has_cjk(out) and not has_vi(out) else ""
    if re.search(r"^(Thăng cấp|Nâng cấp) ", value):
        out = generic_replace(value)
        out = re.sub(r"升级\s+", "升级", out)
        return out if has_cjk(out) and not has_vi(out) else ""
    if re.search(r"(Thú cưỡi|Thần Thú|Thiên Giới|Thái Thượng Lão Quân|Trùng Sinh|Phong Thần|Hoàng Phi Hổ)", value):
        out = generic_replace(value)
        if "được" in out:
            out = out.replace("được", "可")
        out = out.replace("của", "的").replace("từ", "来自").replace("tượng trưng cho sự may mắn", "象征吉祥")
        out = out.replace("ngày đi vạn dặm", "日行万里")
        out = out.replace("mới cưỡi 可", "方可骑乘")
        out = out.replace("英雄豪杰 mới cưỡi 可", "英雄豪杰方可骑乘")
        return out if has_cjk(out) and not has_vi(out) else ""
    if re.search(r"(Pháp bảo|Tiên Ma|Băng|Lôi|Thổ|Hỏa|thuộc tính|đạo cụ)", value):
        out = generic_replace(value)
        return out if has_cjk(out) and not has_vi(out) else ""
    return ""


def run(workspace: Path, pak: str, dry_run: bool) -> dict:
    records_path = workspace / "localization" / "text_records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    headers = {
        (str(r.get("source_file") or ""), int(r.get("column") or 1)): str(r.get("original") or "")
        for r in records
        if r.get("pak") == pak and int(r.get("line") or 0) == 1
    }
    updated = 0
    rejected = 0
    by_file: dict[str, int] = {}
    samples = []
    for r in records:
        if r.get("pak") != pak or int(r.get("line") or 0) <= 1:
            continue
        header = headers.get((str(r.get("source_file") or ""), int(r.get("column") or 1)), "")
        if header not in VISIBLE_HEADERS:
            continue
        current = str(r.get("original") or "")
        source = str(r.get("source_original") or current)
        token_ok, _current_source_tokens, _current_target_tokens = validate_tokens(source, current)
        if r.get("language") == "zh" and has_cjk(current) and token_ok:
            continue
        target = translate_rule(source)
        if not target:
            continue
        ok, _source_tokens, _target_tokens = validate_tokens(source, target)
        if not ok:
            rejected += 1
            continue
        r["source_original"] = source
        r["original"] = target
        r["translation"] = target
        r["language"] = "zh"
        r["status"] = "已规则翻译"
        r["note"] = (str(r.get("note") or "").strip() + " [玩家可见模板规则]").strip()
        updated += 1
        file = str(r.get("source_file") or "")
        by_file[file] = by_file.get(file, 0) + 1
        if len(samples) < 50:
            samples.append({"file": file, "line": r.get("line"), "column": r.get("column"), "source": source, "target": target})
    report = {
        "dry_run": dry_run,
        "pak": pak,
        "updated": updated,
        "rejected": rejected,
        "top_files": dict(sorted(by_file.items(), key=lambda kv: -kv[1])[:30]),
        "samples": samples,
    }
    out_dir = workspace / "build" / f"rule_translate_visible_remaining_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if updated and not dry_run:
        backup = records_path.with_name(f"text_records.before_rule_translate_visible_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        shutil.copy2(records_path, backup)
        tmp = records_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, records_path)
        report["backup"] = str(backup.resolve())
    report_path = out_dir / "rule_translate_visible_remaining_report.json"
    report["report_path"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Rule-translate highly repeated player-visible Vietnamese templates.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--pak", default="updatefs.pak")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.pak, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
