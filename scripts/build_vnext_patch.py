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

PATCH_VERSION = "7.0.1-vnext"
BASELINE_COMMIT = "cd196402f0681b4f233395c5db32a1385510"
PATCH_NAME = f"Cocos-PAK-Studio-vNext-{PATCH_VERSION}-Incremental-Patch"

# The first 7.0.0 overlay only copied files changed by vNext itself. That was too
# narrow for users whose local Studio predates intermediate legacy bootstrap
# modules such as electron/main_with_glossary_resume.js. v7.0.1 is still an
# incremental *runtime* overlay (not a full repository), but it is cumulative:
# all runtime Python/JS/HTML/CSS entrypoints required by the current branch are
# included, while tests, docs, CI metadata, workspaces and node_modules remain
# excluded.
ROOT_RUNTIME_FILES = [
    "package.json",
    "START_V3A.bat",
    "INSTALL_DEPS.bat",
]


def collect_runtime_files(repo: Path) -> list[str]:
    files: set[str] = set()
    for rel in ROOT_RUNTIME_FILES:
        if (repo / rel).is_file():
            files.add(rel)

    # Electron bootstrap/preload and renderer assets are all small and tightly
    # coupled, so ship the complete runtime directories.
    for folder, suffixes in (
        ("electron", {".js", ".json"}),
        ("renderer", {".js", ".css", ".html", ".json"}),
    ):
        root = repo / folder
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes:
                files.add(path.relative_to(repo).as_posix())

    # Backend runtime is dependency-rich. Include Python sources and small JSON
    # configuration files, but never tests, caches or generated outputs.
    backend = repo / "backend"
    if backend.is_dir():
        for path in backend.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo).as_posix()
            parts = path.relative_to(backend).parts
            if "__pycache__" in parts:
                continue
            name = path.name.lower()
            if name.startswith("test_") or name.endswith("_test.py"):
                continue
            if path.suffix.lower() not in {".py", ".json"}:
                continue
            files.add(rel)

    return sorted(files)


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
    runtime_files = collect_runtime_files(repo)

    required = {
        "package.json",
        "electron/main.js",
        "electron/main_vnext.js",
        "electron/main_with_glossary.js",
        "electron/main_with_glossary_resume.js",
        "electron/preload.js",
        "electron/vnext_ipc.js",
        "electron/vnext_compat_ipc.js",
        "backend/studio_cli.py",
        "backend/glossary_xlsx_translate.py",
        "backend/glossary_xlsx_translate_resumable.py",
        "backend/xlsx_localization.py",
        "backend/pak_builder.py",
        "backend/pak_core.py",
        "backend/vnext/pipeline.py",
        "renderer/index.html",
        "renderer/vnext_ui.js",
    }
    missing_required = sorted(required - set(runtime_files))
    if missing_required:
        raise SystemExit("Patch source missing required runtime files: " + ", ".join(missing_required))

    stage = out_root / PATCH_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    files = []
    for rel in runtime_files:
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
        "install_mode": "cumulative-runtime-overlay-replace",
        "database_schema": 2,
        "core_version": "0.7.0",
        "files": files,
        "excluded": [
            "node_modules/",
            ".git/",
            ".github/",
            "docs/",
            "tests and test_*.py",
            "__pycache__/",
            "workspace/ and user project data",
            "PAK files",
        ],
        "compatibility": {
            "older_local_baselines": "supported by cumulative runtime overlay",
            "startup_fallback": "main_with_glossary_resume -> main_with_glossary -> main",
        },
        "validation": {
            "phase_1_to_7_ci": "required before artifact publication",
            "windows_electron_smoke": "required before artifact publication",
            "real_game_three_pak_runtime": "not completed without user original PAK/game client",
        },
    }
    (stage / "PATCH_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    instructions = f"""Cocos PAK Localization Studio vNext 累计增量补丁\n\n版本：{PATCH_VERSION}\n目标参考基线：{BASELINE_COMMIT}\n\n这是 7.0.0 的兼容修正版。7.0.0 只打包了 vNext 自身变更文件，在较老的本地 Studio（例如早期 v3a-hotfix）上可能缺少 main_with_glossary_resume.js 等中间运行模块，导致 Electron 启动时报 Cannot find module。7.0.1 改为累计运行时覆盖包，仍不包含完整仓库、测试、文档或 node_modules。\n\n安装：\n1. 完全退出 Studio。\n2. 备份当前 Studio 代码目录。\n3. 将本压缩包内 {PATCH_NAME} 文件夹中的内容复制到 Studio 项目根目录。\n4. Windows 提示时选择“替换目标中的文件”。\n5. 不要删除 node_modules，也不要覆盖/删除你的 workspace、原始 PAK、localization 数据。\n6. 启动原来的根目录启动脚本，或执行 npm start。\n\n首次进入 vNext：\n- 先打开原有项目。\n- 在“旧版工具”区域检查兼容状态。\n- 如果工作区显示未同步，先执行“仅同步源索引”；如果要保留旧版已翻译中文，则执行“吸收旧版当前译文”。\n- 新翻译优先使用“智能翻译”，新 PAK 使用“构建”页的 vNext Build Gate。\n\n重要：\n- 这是累计增量运行时补丁，不包含 node_modules、Git、测试、文档、用户 workspace 或 PAK。\n- 自动 CI 必须通过 Phase 1–7、Windows 三 PACK 构建链路和 Electron 主入口启动验证后才会发布。\n- 尚未取得你的真实原版 settings.pak/updatefs.pak/ui.pak 与游戏客户端，因此真实游戏运行不闪退仍需首次实机验证；请保留原始 PAK 备份。\n"""
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
