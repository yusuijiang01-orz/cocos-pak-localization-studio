import hashlib
import json
from pathlib import Path


WORKSPACE = Path(r"pak/对比/越南手机版")
RECORDS_PATH = WORKSPACE / "localization" / "text_records.json"

TRANSLATIONS = [
    ("0222_1840E406.ini", 71, 1, "Quay 1 lần", "抽奖1次"),
    ("0222_1840E406.ini", 86, 1, "Quay 10 lần", "抽奖10次"),
    (
        "0805_52879FD2.tsv",
        3,
        2,
        "Vòng quay may mắn là cơ hội kỳ diệu để Quý Kỳ Sĩ nhận những vật phẩm quý giá. Từ lần quay 350 trở đi, Quý Kỳ Sĩ sẽ có thêm cơ hội may mắn nhận được 01 trong các phần thưởng đặc biệt gồm: 01 Nội đơn (trung), 01 Phi Thiên Quyển, 01 Thẻ Kim Dật 500, 10 triệu kinh nghiệm, ngẫu nhiên biến thân có thuộc tính, 01 Bàn Cổ Linh Phù, 01 Thông Thiên Linh Phù, 01 Ngọc Bội chất lượng 02, 02 Tướng Quân Lệnh, 100 Mảnh Vũ Khí Truyền Thuyết",
        "幸运转盘可让各位玩家获得珍贵物品。累计抽奖达到350次后，还有机会获得以下特别奖励之一：中级内丹、飞天卷、金逸卡500、1000万经验、随机属性变身、盘古灵符、通天灵符、2级玉佩、2枚将军令或100个传说武器碎片。",
    ),
]


def main() -> None:
    records = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
    by_locator = {
        (r.get("pak"), r.get("source_file"), int(r.get("line", 0)), int(r.get("column", 1))): r
        for r in records
    }
    added = 0
    for source_file, line, column, source, target in TRANSLATIONS:
        locator = ("updatefs.pak", source_file, line, column)
        record = by_locator.get(locator)
        if record is None:
            digest = hashlib.sha256("|".join(map(str, locator)).encode("utf-8")).hexdigest()[:16]
            record = {
                "id": digest, "category": "UI文字", "subcategory": "ui",
                "pak": "updatefs.pak", "hash": source_file.split("_", 1)[1].split(".", 1)[0],
                "source_file": source_file, "line": line, "column": column, "key": "",
                "encoding": "utf-8", "confidence_score": 100.0,
            }
            records.append(record)
            added += 1
        record.update({
            "source_original": source, "original": target, "translation": target,
            "language": "zh", "status": "已翻译", "note": "[人工修复:截图精确定位]",
        })
    RECORDS_PATH.write_text(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"updated={len(TRANSLATIONS)} added={added}")


if __name__ == "__main__":
    main()
