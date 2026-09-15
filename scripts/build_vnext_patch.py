#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PATCH_VERSION = "7.0.0-vnext"
BASELINE_COMMIT = "cd196402f0681b4b8c105a1cef47bed42542a686"
PATCH_NAME = f"Cocos-PAK-Studio-vNext-{PATCH_VERSION}-Incremental-Patch"

RUNTIME_FILES = [
    "package.json",
    "backend/vnext/__init__.py",
    "backend/vnext/build.py",
    "backend/vnext/classify.py",
    "backend/vnext/compatibility.py",
    "backend/vnext/database.py",
    "backend/vnext/ingest.py",
    "backend/vnext/jobs.py",
    "backend/vnext/knowledge.py",
    "backend/vnext/models.py",
    "backend/vnext/normalize.py",
    "backend/vnext/ollama_engine.py",
    "backend/vnext/phase7.py",
    "backend/vnext/pipeline.py",
    "backend/vnext/protection.py",
    "backend/vnext/qa.py",
    "backend/vnext/review.py",
    "backend/vnext/ui_api.py",
    "backend/vnext/workspace_ingest.py",
    "backend/vnext_cli.py",
    "backend/vnext_compat_cli.py",
    "backend/vnext_phase7_cli.py",
    "backend/vnext_ui_cli.py",
    "electron/main_vnext.js",
    "electron/preload.js",
    "electron/vnext_compat_ipc.js",
    "electron/vnext_ipc.js",
    "renderer/glossary_translate_ui.js",
    "renderer/index.html",
    "renderer/vnext.css",
    "renderer/vnext_compat.css",
    "renderer/vnext_compat_ui.js",
    "renderer/vnext_ui.js",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    repo = Path(argv[1] if len(argv) > 1 else ".").resolve()
    out_root = Path(argv[2] if len(argv) > 2 else repo / "dist-patch").resolve()
    source_commit = os.environ.get("PATCH_SOURCE_COMMIT") or os.environ.get("GITHUB_SHA") or "unknown"

    missing = [rel for rel in RUNTIME_FILES if not (repo / rel).is_file()]
    if missing:
        raise SystemExit("Patch source missing files: " + ", ".join(missing))

    stage = out_root / PATCH_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    files = []
    for rel in RUNTIME_FILES:
        src = repo / rel
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files.append({
            "path": rel.replace("\\", "/"),
            "size": dst.stat().st_size,
            "sha256": sha256(dst),
        })

    manifest = {
        "patch": PATCH_NAME,
        "patch_version": PATCH_VERSION,
        "baseline_commit": BASELINE_COMMIT,
        "source_commit": source_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "install_mode": "overlay-replace",
        "database_schema": 2,
        "core_version": "0.7.0",
        "files": files,
        "excluded": [
            "node_modules/",
            ".git/",
            "workspace/ and user project data",
            "PAK files",
            "tests and CI-only files",
        ],
        "validation": {
            "phase_1_to_7_ci": "required before artifact publication",
            "windows_electron_smoke": "required before artifact publication",
            "real_game_three_pak_runtime": "not completed without user original PAK/game client",
        },
    }
    (stage / "PATCH_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    instructions = f"""Cocos PAK Localization Studio vNext 增量补丁\n\n版本：{PATCH_VERSION}\n目标基线：{BASELINE_COMMIT}\n\n安装：\n1. 完全退出 Studio。\n2. 先备份你当前的项目代码目录。\n3. 将本压缩包内 {PATCH_NAME} 文件夹中的内容复制到 Studio 项目根目录。\n4. Windows 提示时选择“替换目标中的文件”。\n5. 不要删除 node_modules，也不要覆盖/删除你的 workspace、原始 PAK、localization 数据。\n6. 启动原来的根目录启动脚本，或执行 npm start。\n\n首次进入 vNext：\n- 先打开原有项目。\n- 在“旧版工具”区域检查兼容状态。\n- 如果工作区显示未同步，先执行“仅同步源索引”；如果你要保留旧版已翻译中文，则执行“吸收旧版当前译文”。\n- 新翻译优先使用“智能翻译”，新 PAK 使用“构建”页的 vNext Build Gate。\n\n重要：\n- 这是增量覆盖补丁，不包含完整项目和 node_modules。\n- 自动 CI 已通过 Phase 1–7、Windows 三 PACK 构建链路和 Electron 主入口启动验证。\n- 因尚未取得你的真实原版 settings.pak/updatefs.pak/ui.pak 与游戏客户端，本补丁尚未完成真实游戏运行不闪退的最终签字验证。首次用于正式游戏前请保留原始 PAK 备份。\n"""
    (stage / "补丁说明.txt").write_text(instructions, encoding="utf-8-sig")

    zip_path = out_root / f"{PATCH_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_root).as_posix())

    zip_sha = sha256(zip_path)
    (out_root / f"{PATCH_NAME}.zip.sha256").write_text(
        f"{zip_sha}  {zip_path.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "ok": True,
        "zip": str(zip_path),
        "sha256": zip_sha,
        "file_count": len(files),
        "stage": str(stage),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
