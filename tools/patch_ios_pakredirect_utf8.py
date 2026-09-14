#!/usr/bin/env python3
"""Patch the decrypted TamGioiPhanTranhMobile iOS app in-place inside a new IPA.

The patch mirrors the Android UTF-8 autodetect behaviour: a non-empty valid UTF-8
input bypasses the legacy TCVN3 converter; invalid input continues through the
original converter. The iOS client keeps the official CDN route; Android's local
PakRedirect endpoint must never be copied into an IPA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import tempfile
import unicodedata
import zipfile
from pathlib import Path

from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN


MAIN_REL = "Payload/TamGioiPhanTranhMobile.app/TamGioiPhanTranhMobile"
PATCH_OFF = 0x90FA4
PATCH_VA = 0x100090FA4
PATCH_END_VA = 0x100090FC0
UTF8_LENGTH_VA = 0x100583D18
ORIGINAL = bytes.fromhex(
    "a8c35eb8c800003501000014e00740f9a1035ff8f6cefd974d000014"
)
OLD_URL = b"https://cdn.tamgioipt.vn/linkspak.txt"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def locate_main(names: list[str]) -> str:
    wanted = unicodedata.normalize("NFC", MAIN_REL)
    for name in names:
        if unicodedata.normalize("NFC", name) == wanted:
            return name
    candidates = [n for n in names if n.startswith("Payload/") and ".app/" in n and not n.endswith("/")]
    for name in candidates:
        app = name.split("/", 2)[1]
        if name == f"Payload/{app}/{app[:-4]}":
            return name
    raise RuntimeError("Cannot locate the app's main Mach-O executable")


def make_patch() -> bytes:
    asm = f"""
        ldur x0, [x29, #-0x10]
        bl {UTF8_LENGTH_VA:#x}
        cbz w0, {PATCH_END_VA:#x}
        ldr x0, [sp, #8]
        ldur x1, [x29, #-0x10]
        bl 0x100004b90
        b 0x1000910f0
    """
    encoded, _ = Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN).asm(asm, addr=PATCH_VA)
    result = bytes(encoded)
    if len(result) != len(ORIGINAL):
        raise RuntimeError(f"Unexpected patch length: {len(result)}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Refusing to overwrite the source IPA")

    patch = make_patch()
    with zipfile.ZipFile(args.input, "r") as zin:
        bad = zin.testzip()
        if bad:
            raise RuntimeError(f"Source IPA CRC failure: {bad}")
        names = zin.namelist()
        main_name = locate_main(names)
        original_main = zin.read(main_name)
        if original_main[:4] != bytes.fromhex("cffaedfe"):
            raise RuntimeError("Main executable is not a thin ARM64 Mach-O")
        if original_main[PATCH_OFF:PATCH_OFF + len(ORIGINAL)] != ORIGINAL:
            got = original_main[PATCH_OFF:PATCH_OFF + len(ORIGINAL)].hex()
            raise RuntimeError(f"Unexpected converter bytes at {PATCH_OFF:#x}: {got}")

        modified = bytearray(original_main)
        modified[PATCH_OFF:PATCH_OFF + len(patch)] = patch
        url_count = modified.count(OLD_URL)
        if url_count != 1:
            raise RuntimeError(f"Expected exactly one official linkspak URL, found {url_count}")
        pos = modified.index(OLD_URL)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp_out = args.output.with_suffix(args.output.suffix + ".tmp")
        if temp_out.exists():
            temp_out.unlink()
        with zipfile.ZipFile(temp_out, "w", allowZip64=True) as zout:
            for info in zin.infolist():
                payload = bytes(modified) if info.filename == main_name else zin.read(info.filename)
                zout.writestr(info, payload)
        temp_out.replace(args.output)

    with zipfile.ZipFile(args.output, "r") as check:
        bad = check.testzip()
        if bad:
            raise RuntimeError(f"Output IPA CRC failure: {bad}")
        check_main = check.read(main_name)
    if len(check_main) != len(original_main):
        raise RuntimeError("Mach-O size changed unexpectedly")
    if check_main[PATCH_OFF:PATCH_OFF + len(patch)] != patch:
        raise RuntimeError("UTF-8 patch verification failed")
    if check_main.count(OLD_URL) != 1 or b"127.0.0.1:18480" in check_main:
        raise RuntimeError("Official iOS linkspak URL verification failed")

    report = {
        "source_ipa": str(args.input.resolve()),
        "output_ipa": str(args.output.resolve()),
        "main_executable": main_name,
        "source_main_sha256": sha256(original_main),
        "patched_main_sha256": sha256(check_main),
        "output_ipa_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "utf8_autodetect": {
            "patch_file_offset": hex(PATCH_OFF),
            "patch_va": hex(PATCH_VA),
            "valid_utf8_bypasses_tcvn3": True,
            "invalid_utf8_uses_original_tcvn3": True,
        },
        "pak_route": {
            "file_offset": hex(pos), "mode": "official_cdn", "url": OLD_URL.decode()
        },
        "signature": "invalidated; re-sign the IPA before installation",
    }
    report_path = args.output.with_suffix(".patch-report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows PowerShell may use GBK, while the source IPA name contains
    # Vietnamese combining characters that GBK cannot represent.
    print(json.dumps(report, ensure_ascii=True, indent=2))
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
