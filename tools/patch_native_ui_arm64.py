import argparse
import json
import shutil
import struct
from datetime import datetime
from pathlib import Path

import capstone
import lief


TRANSLATIONS = {
    "Nhân vật": "角色",
    "Không hiển thị": "不显示",
    "Tên:": "名称：",
    "Lãnh địa:": "领地：",
    "Hệ:": "职业：",
    "Công lực:": "战力：",
    "Xem chi tiết thuộc tính": "查看详细属性",
    "Cấp:": "等级：",
    "Hạng:": "排名：",
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
    "Thuộc tính": "属性",
    "Sát thương cơ bản": "基础伤害",
    "Tăng thời gian trúng thương": "受创时间增加",
    "Tăng thời gian đóng băng": "冰冻时间增加",
    "Giảm thời gian trúng thương": "受创时间减少",
    "Giảm thời gian đóng băng": "冰冻时间减少",
    "Bỏ qua phòng ngự": "无视防御",
    "Tỷ lệ b. kích cơ bản": "基础暴击率",
    "Sát thương b. kích cơ bản": "基础暴击伤害",
    "Tỷ lệ b. kích Lôi": "雷系暴击率",
    "Tỷ lệ b. kích Thổ": "土系暴击率",
    "Tỷ lệ b. kích Hỏa": "火系暴击率",
    "Tỷ lệ b. kích Băng": "冰系暴击率",
    "Sát thương b. kích Pháp thuật": "法术暴击伤害",
    "Bỏ qua Kháng Lôi": "忽略雷抗",
    "Bỏ qua Kháng Thổ": "忽略土抗",
    "Bỏ qua Kháng Hỏa": "忽略火抗",
    "Bỏ qua Kháng Băng": "忽略冰抗",
}

QUALITY_OVERRIDES = {
    "Chính xác": "精准",
    "Trác Việt": "卓越",
    "Yêu đái": "腰带",
    "Ngọc\nbội": "玉\n佩",
    "Thần\nấn": "神\n印",
    "Pháp\nbảo": "法\n宝",
    "Bốc hỏa": "冒火",
    "Phụ tuyến": "支线",
    "Đầu khôi": "头盔",
    "Khải giáp": "铠甲",
    "Phù Thạch": "符石",
    "Tư chất:": "资质：",
    "Hơi chuếnh": "微醺",
    " Chưa có ": " 暂无 ",
    "Đẳng cấp Kim Phong Doanh": "金锋营等级",
    "Đẳng cấp Mộc Hoa Trai": "木华斋等级",
    "Đẳng cấp Thuỷ Đức Sảnh": "水德堂等级",
    "Đẳng cấp Hoả Thành Đường": "火城堂等级",
    "Đẳng cấp Thổ tàng phủ": "土藏府等级",
    "Hôn": "婚",
    "Thăm ngàn": "探访",
    "Giày": "鞋",
    "Cùng hát": "合唱",
    "Thị lang": "侍郎",
}


def encode_adrp(original: int, pc: int, target: int) -> int:
    delta = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
    if not -(1 << 20) <= delta < (1 << 20):
        raise ValueError("ADRP target is out of range")
    immediate = delta & 0x1FFFFF
    value = original & ~((0x3 << 29) | (0x7FFFF << 5))
    return value | ((immediate & 0x3) << 29) | (((immediate >> 2) & 0x7FFFF) << 5)


def encode_add_immediate(original: int, target: int) -> int:
    value = original & ~(0xFFF << 10)
    return value | ((target & 0xFFF) << 10)


def find_code_references(raw: bytes, text_offset: int, text_va: int, text_size: int, targets: set[int]):
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.detail = True
    references = {target: [] for target in targets}
    adrp = {}
    for insn in cs.disasm(raw[text_offset : text_offset + text_size], text_va):
        if insn.mnemonic == "adrp" and len(insn.operands) >= 2:
            adrp[insn.operands[0].reg] = (insn.address, insn.operands[1].imm)
            continue
        if insn.mnemonic != "add" or len(insn.operands) < 3:
            continue
        src = insn.operands[1]
        imm = insn.operands[2]
        if src.type != capstone.arm64.ARM64_OP_REG or imm.type != capstone.arm64.ARM64_OP_IMM:
            continue
        page = adrp.get(src.reg)
        if not page or insn.address - page[0] > 32:
            continue
        target = page[1] + imm.imm
        if target in references:
            references[target].append((page[0], insn.address))
    return references


def patch_library(path: Path, backup_root: Path, translations: dict[str, str]) -> dict:
    binary = lief.parse(str(path))
    if binary.header.machine_type != lief.ELF.ARCH.AARCH64:
        raise ValueError(f"not an ARM64 ELF: {path}")
    rodata = binary.get_section(".rodata")
    text = binary.get_section(".text")
    raw = bytearray(path.read_bytes())

    source_offsets = {}
    for source in translations:
        matches = []
        start = 0
        needle = source.encode("utf-8")
        while True:
            offset = raw.find(needle + b"\0", start)
            if offset < 0:
                break
            matches.append(offset)
            start = offset + 1
        if not matches:
            raise ValueError(f"source text not found: {source!r}")
        source_offsets[source] = matches

    source_vas = {offset for offsets in source_offsets.values() for offset in offsets}
    references = find_code_references(raw, text.offset, text.virtual_address, text.size, source_vas)

    rodata_start = rodata.offset
    rodata_end = rodata.offset + rodata.size

    def allocate(size: int) -> int:
        cave = bytes(raw[rodata_start:rodata_end]).rfind(b"\0" * size)
        if cave < 0:
            raise ValueError(f"no {size}-byte zero cave in .rodata")
        offset = rodata_start + cave
        raw[offset : offset + size] = b"\x7f" * size
        return offset

    changes = []
    skipped = []
    for source, target in translations.items():
        target_bytes = target.encode("utf-8") + b"\0"
        source_bytes = source.encode("utf-8") + b"\0"
        if len(target_bytes) <= len(source_bytes):
            for source_offset in source_offsets[source]:
                replacement = target_bytes + b"\0" * (len(source_bytes) - len(target_bytes))
                raw[source_offset : source_offset + len(source_bytes)] = replacement
            ref_count = sum(
                len(references.get(rodata.virtual_address + (offset - rodata.offset), []))
                for offset in source_offsets[source]
            )
            changes.append({"source": source, "target": target, "mode": "in_place", "references": ref_count})
            continue

        refs = []
        for source_offset in source_offsets[source]:
            source_va = rodata.virtual_address + (source_offset - rodata.offset)
            refs.extend(references.get(source_va, []))
        if not refs:
            skipped.append({"source": source, "target": target, "reason": "longer translation has no direct ARM64 code reference"})
            continue

        target_offset = allocate(len(target_bytes))
        raw[target_offset : target_offset + len(target_bytes)] = target_bytes
        target_va = rodata.virtual_address + (target_offset - rodata.offset)
        for adrp_va, add_va in refs:
            adrp_offset = text.offset + (adrp_va - text.virtual_address)
            add_offset = text.offset + (add_va - text.virtual_address)
            adrp_word = struct.unpack_from("<I", raw, adrp_offset)[0]
            add_word = struct.unpack_from("<I", raw, add_offset)[0]
            struct.pack_into("<I", raw, adrp_offset, encode_adrp(adrp_word, adrp_va, target_va))
            struct.pack_into("<I", raw, add_offset, encode_add_immediate(add_word, target_va))
        changes.append({"source": source, "target": target, "mode": "relocated", "target_va": hex(target_va), "references": len(refs)})

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_root / f"{path.name}.{stamp}.bak"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    path.write_bytes(raw)
    return {"path": str(path), "backup": str(backup), "changes": changes, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Relocate embedded Vietnamese ARM64 UI strings to full Chinese text.")
    parser.add_argument("libraries", nargs="+", type=Path)
    parser.add_argument("--backup-root", type=Path, default=Path("ipa/_native_ui_arm64_backups"))
    parser.add_argument("--report", type=Path, default=Path("ipa/native-ui-arm64-latest.json"))
    parser.add_argument("--translations", type=Path)
    args = parser.parse_args()
    translations = dict(TRANSLATIONS)
    if args.translations:
        translations.update(json.loads(args.translations.read_text(encoding="utf-8")))
    translations.update(QUALITY_OVERRIDES)
    translations["Chờ x.lý 1"] = "等待处理 1"
    results = [patch_library(path.resolve(), args.backup_root.resolve(), translations) for path in args.libraries]
    args.report.write_text(json.dumps({"libraries": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.report), "libraries": len(results)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
