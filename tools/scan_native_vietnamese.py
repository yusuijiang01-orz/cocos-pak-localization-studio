import argparse
import json
import re
from pathlib import Path

import lief


VIETNAMESE_MARKS = set(
    "ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩị"
    "óòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵÁÀẢÃẠẤẦẨẪẬẮẰẲẴẶ"
    "ÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ"
)
COMMON_WORDS = re.compile(
    r"(?i)\b(không|thuộc|tính|sát|thương|tăng|giảm|phòng|kháng|chính|xác|"
    r"cấp|nhân|vật|nhiệm|vụ|trang|bị|bỏ|qua|tỷ|lệ|công|lực|thời|gian|"
    r"hiển|thị|điểm|người|chơi|mua|bán|đồng|ý|hủy|thoát|máy|chủ)\b"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory likely Vietnamese UTF-8 strings embedded in an ELF library.")
    parser.add_argument("library", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    binary = lief.parse(str(args.library))
    rodata = binary.get_section(".rodata")
    rows = []
    cursor = rodata.offset
    for chunk in bytes(rodata.content).split(b"\0"):
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            cursor += len(chunk) + 1
            continue
        candidate = len(text) >= 2 and (any(ch in VIETNAMESE_MARKS for ch in text) or COMMON_WORDS.search(text))
        if candidate and all(ch.isprintable() or ch in "\r\n\t" for ch in text):
            rows.append({"offset": cursor, "virtual_address": cursor, "text": text})
        cursor += len(chunk) + 1

    unique = []
    seen = set()
    for row in rows:
        if row["text"] in seen:
            continue
        seen.add(row["text"])
        unique.append(row)
    report = {"library": str(args.library), "occurrences": len(rows), "unique": len(unique), "strings": unique}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "occurrences": len(rows), "unique": len(unique)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
