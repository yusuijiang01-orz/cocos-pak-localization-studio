from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_SOURCE = ROOT / "pak/settings/modified_utf8/0696_93F6768D.ini"
SETTINGS_TARGET = ROOT / "pak/settings/modified/settings/0696_93F6768D.ini"
UPDATEFS_SOURCE = (
    ROOT / "pak/updatefs/build/updatefs/extracted/updatefs/1472_93F6768D.ini"
)
UPDATEFS_TARGET = ROOT / "pak/updatefs/modified/updatefs/1472_93F6768D.ini"

VIETNAMESE_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ]"
    r"|[àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợ"
    r"ùúủũụừứửữựỳýỷỹỵ]",
    re.IGNORECASE,
)


SETTINGS_FIXES = {
    "durability_v": "耐久度：#d1-",
    "magic_modify_shedblood": (
        "攻击时有 #d1-% 概率触发流血（每半秒造成一次伤害，额外造成 #d2-% 伤害）"
    ),
    "physic_deadly_strike_damage_r": "受到的物理暴击伤害降低：#d1-%",
    "magic_deadly_strike_damage_r": "受到的法术暴击伤害降低：#d1-%",
}

UPDATEFS_FIXES = {
    "durability_v": "耐久度：#d1- 点",
    "lucky_v_partner": "队伍幸运：#d1- 点",
    "lifepotion_p": "生命：#d1+%",
    "magic_summon_beast_inherit_player_v": "召唤兽继承玩家属性：#d1+%",
    "magic_life_kmfc_dot_by_self_p": "按已损失生命比例恢复：#d1-%",
}

# These five values remain ASCII on disk but represent Chinese in different forms.
# If one form renders as Chinese in game, it identifies a decoder supported by the
# legacy equipment INI path. Keep placeholders unchanged so gameplay data is safe.
UNICODE_MATRIX = {
    "durability_v": "耐久度：#d1-",
    "exdefense_v": r"\u9632\u5FA1：#d1-",
    "requirelevel": "&#x7B49;&#x7EA7;&#x8981;&#x6C42;：#d1-",
    "attackrating_v": "&#21629;&#20013;：#d1-",
    "armordefense_v": "%E9%97%AA%E9%81%BF：#d1-",
}


def parse_lines(path: Path) -> list[str]:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        # Legacy TCVN3 text is byte-oriented; Latin-1 preserves every byte while
        # allowing us to retain the ASCII INI keys and structural markers.
        return data.decode("latin-1").splitlines()


def keyed_values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def needs_chinese(value: str) -> bool:
    if VIETNAMESE_RE.search(value):
        return True
    if re.search(r"[\u4e00-\u9fff]", value):
        return False
    visible = re.sub(r"<[^>]*>|#[A-Za-z]\d+[-+]?|[$%+\-:/().,0-9\s]", "", value)
    return bool(re.search(r"[A-Za-z]", visible))


def apply_values(
    lines: list[str], replacements: dict[str, str], preserve_dollar: bool
) -> list[str]:
    result: list[str] = []
    for line in lines:
        if "=" not in line:
            result.append(line)
            continue
        key, old_value = line.split("=", 1)
        if key not in replacements:
            result.append(line)
            continue
        value = replacements[key]
        if preserve_dollar and old_value.startswith("$"):
            value = "$" + value.lstrip("$")
        elif not preserve_dollar:
            value = value.lstrip("$")
        result.append(f"{key}={value}")
    return result


def keys(lines: list[str]) -> list[str]:
    return [line.split("=", 1)[0] for line in lines if "=" in line]


def write_checked(path: Path, original_lines: list[str], new_lines: list[str]) -> None:
    if keys(original_lines) != keys(new_lines):
        raise RuntimeError(f"INI key order changed: {path}")
    text = "\r\n".join(new_lines) + "\r\n"
    if VIETNAMESE_RE.search(text):
        raise RuntimeError(f"Vietnamese text remains: {path}")
    path.write_bytes(text.encode("utf-8"))


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "pak/_unicode_matrix_backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SETTINGS_TARGET, backup_dir / SETTINGS_TARGET.name)
    shutil.copy2(UPDATEFS_TARGET, backup_dir / UPDATEFS_TARGET.name)

    settings_original = parse_lines(SETTINGS_TARGET)
    settings_lines = parse_lines(SETTINGS_SOURCE)
    settings_lines = apply_values(settings_lines, SETTINGS_FIXES, preserve_dollar=True)
    settings_lines = apply_values(settings_lines, UNICODE_MATRIX, preserve_dollar=True)
    write_checked(SETTINGS_TARGET, settings_original, settings_lines)

    updatefs_original = parse_lines(UPDATEFS_SOURCE)
    settings_zh = keyed_values(settings_lines)
    updatefs_values = keyed_values(updatefs_original)
    reusable = {
        key: value
        for key, value in settings_zh.items()
        if key in updatefs_values
        and needs_chinese(updatefs_values[key])
        and re.search(r"[\u4e00-\u9fff]", value)
        and "\t" not in value
    }
    updatefs_lines = apply_values(updatefs_original, reusable, preserve_dollar=False)
    updatefs_lines = apply_values(
        updatefs_lines, UPDATEFS_FIXES, preserve_dollar=False
    )
    updatefs_lines = apply_values(
        updatefs_lines, UNICODE_MATRIX, preserve_dollar=False
    )
    write_checked(UPDATEFS_TARGET, updatefs_original, updatefs_lines)

    print(f"backup={backup_dir}")
    print(f"settings={SETTINGS_TARGET}")
    print(f"updatefs={UPDATEFS_TARGET}")
    for key, value in UNICODE_MATRIX.items():
        print(f"matrix {key}={value}")


if __name__ == "__main__":
    main()
