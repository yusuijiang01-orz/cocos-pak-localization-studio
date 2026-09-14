#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inventory(path: Path) -> dict[str, dict]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: {"size": i.file_size, "crc": f"{i.CRC:08x}"} for i in zf.infolist() if not i.is_dir()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("original", type=Path)
    ap.add_argument("patched", type=Path)
    ap.add_argument("ipa", type=Path)
    ap.add_argument("--extract", type=Path)
    args = ap.parse_args()

    a, b, ios = inventory(args.original), inventory(args.patched), inventory(args.ipa)
    changed = []
    with zipfile.ZipFile(args.original) as za, zipfile.ZipFile(args.patched) as zb:
        for name in sorted(set(a) | set(b)):
            if name not in a or name not in b:
                changed.append({"name": name, "state": "added" if name in b else "removed", "before": a.get(name), "after": b.get(name)})
            elif a[name] != b[name]:
                da, db = za.read(name), zb.read(name)
                changed.append({"name": name, "state": "changed", "before": a[name], "after": b[name],
                                "sha_before": digest(da), "sha_after": digest(db)})
                if args.extract and name.startswith("lib/") and name.endswith(".so"):
                    for label, payload in (("apk_original", da), ("apk_patched", db)):
                        out = args.extract / label / name
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(payload)

    executables = []
    with zipfile.ZipFile(args.ipa) as zi:
        for name, meta in ios.items():
            leaf = Path(name).name
            if name.startswith("Payload/") and (".framework/" in name or name.count("/") == 2):
                data = zi.read(name)
                magic = data[:4].hex()
                if magic in {"cffaedfe", "feedfacf", "cafebabe", "bebafeca", "cefaedfe", "feedface"}:
                    executables.append({"name": name, "size": len(data), "magic": magic, "sha256": digest(data)})
                    if args.extract:
                        out = args.extract / name
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(data)
        if args.extract:
            for name in ("Payload/Tam Gioi Phan Tranh Mobile.app/Info.plist",):
                if name in ios:
                    out = args.extract / name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zi.read(name))

    print(json.dumps({"apk_changed": changed, "ipa_files": len(ios), "ipa_macho": executables}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
