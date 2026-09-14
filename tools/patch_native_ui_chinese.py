import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


TRANSLATIONS = {
    "Nhân vật": "角色",
    "Không hiển thị": "不显示",
    "Tên:": "名:",
    "Lãnh địa:": "领地：",
    "Hệ:": "职:",
    "PK:": "PK:",
    "Công lực:": "战力：",
    "Xem chi tiết thuộc tính": "查看详细属性",
    "Cấp:": "级:",
    "Hạng:": "排:",
    "Danh vọng:": "声望：",
    "Kinh nghiệm:": "经验：",
    "Hữu công:": "物攻：",
    "Vũ khí công:": "武器攻击：",
    "Ma pháp công:": "法术攻击：",
    "Tập trung:": "命中：",
    "Chính xác:": "精准：",
    "Né tránh:": "闪避：",
    "Phòng thủ:": "防御：",
    "Kháng lôi:": "雷抗：",
    "Kháng Hỏa:": "火抗：",
    "Kháng băng:": "冰抗：",
    "Kháng thổ:": "土抗：",
    "Lôi công:": "雷攻：",
    "Thổ công:": "土攻：",
    "Băng công:": "冰攻：",
    "Hỏa công:": "火攻：",
    "Sức mạnh:": "力量：",
    "Thể chất:": "体质：",
    "Thân pháp:": "身法：",
    "Ngộ Tính:": "悟性：",
}


def patch_library(path: Path, backup_root: Path) -> dict:
    original = path.read_bytes()
    patched = original
    replacements = {}

    for source, target in TRANSLATIONS.items():
        if source == target:
            continue
        source_bytes = source.encode("utf-8")
        target_bytes = target.encode("utf-8")
        if len(target_bytes) > len(source_bytes):
            raise ValueError(f"translation is too long: {source!r} -> {target!r}")
        count = patched.count(source_bytes)
        if count:
            replacement = target_bytes + (b"\0" * (len(source_bytes) - len(target_bytes)))
            patched = patched.replace(source_bytes, replacement)
        replacements[source] = {"target": target, "count": count}

    relative = Path(path.drive.replace(":", "")) / path.relative_to(path.anchor)
    backup = backup_root / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    path.write_bytes(patched)

    remaining = [source for source in TRANSLATIONS if source.encode("utf-8") in patched]
    return {
        "path": str(path),
        "backup": str(backup),
        "changed_bytes": sum(a != b for a, b in zip(original, patched)),
        "replacements": replacements,
        "remaining_vietnamese": remaining,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch embedded Vietnamese UI labels in libcocos2dcpp.so.")
    parser.add_argument("libraries", nargs="+", type=Path)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = args.backup_root or Path("ipa") / "_native_ui_backups" / stamp
    results = [patch_library(path.resolve(), backup_root.resolve()) for path in args.libraries]
    report = {"timestamp": stamp, "libraries": results}
    report_path = args.report or Path("ipa") / f"native-ui-chinese-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "libraries": results}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
