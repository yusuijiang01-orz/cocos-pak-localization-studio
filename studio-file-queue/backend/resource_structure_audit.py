from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from localization_tm import RESOURCE_PATH_PATTERN


TEXT_SUFFIXES = {".tsv", ".ini", ".txt", ".lua"}
ASSET_SUFFIXES = {".spr", ".bmp", ".jpg", ".jpeg", ".png"}
TAG_RE = re.compile(rb"<[^<>\r\n]{1,512}>")
PATH_RE = re.compile(RESOURCE_PATH_PATTERN.encode("ascii"), re.I)
KV_RE = re.compile(rb"^(?!\s*<)\s*[^=\r\n]{1,160}\s*=")
SECTION_RE = re.compile(rb"^\s*\[[^\]\r\n]{1,160}\]\s*$")
LUA_CODE_RE = re.compile(rb"\b(?:function|local|return|if|then|else|end|for|while|require)\b|--|[A-Za-z_]\w*\s*\(")
HTML_RE = re.compile(rb"</?(?:html|style|script|body|div|span|table|tr|td)\b", re.I)
IDENTIFIER_RE = re.compile(rb"^\s*[A-Za-z_][A-Za-z0-9_.:-]*\s*$")


SUFFIX_POLICY = {
    ".tsv": ("mixed", "Only allowlisted player-visible columns; preserve headers, tabs, IDs, paths and numeric fields."),
    ".ini": ("mixed", "Translate values of visible text keys only; preserve sections, keys, equals signs and resource references."),
    ".lua": ("mixed", "Translate visible spans in quoted strings only; preserve code, comments, identifiers, paths and every <...> tag."),
    ".txt": ("inspect", "Plain prose may be translated line-by-line; key/value, tabular, HTML/script and path-bearing files remain mixed."),
    ".bin": ("blocked", "Binary layout is unknown. Never infer translatable text from decoded byte runs."),
    ".spr": ("blocked", "Sprite asset. Text baked into pixels requires an image workflow, never byte replacement."),
    ".bmp": ("blocked", "Bitmap asset. Text baked into pixels requires an image workflow, never byte replacement."),
    ".jpg": ("blocked", "Image asset. Text baked into pixels requires an image workflow, never byte replacement."),
    ".jpeg": ("blocked", "Image asset. Text baked into pixels requires an image workflow, never byte replacement."),
    ".png": ("blocked", "Image asset. Text baked into pixels requires an image workflow, never byte replacement."),
    ".json": ("blocked", "Studio/extraction metadata by default; require an explicit schema before localization."),
}


def inspect_file(path: Path) -> dict:
    suffix = path.suffix.lower() or "<none>"
    policy, rule = SUFFIX_POLICY.get(suffix, ("blocked", "Unknown format; quarantine until a parser and structural validator exist."))
    result = {"file": path.name, "suffix": suffix, "bytes": path.stat().st_size, "policy": policy, "rule": rule}
    if suffix not in TEXT_SUFFIXES:
        return result
    data = path.read_bytes()
    lines = data.splitlines()
    flags = Counter()
    for line in lines:
        if b"\t" in line:
            flags["tabular_lines"] += 1
        if KV_RE.match(line):
            flags["key_value_lines"] += 1
        if SECTION_RE.match(line):
            flags["section_lines"] += 1
        if TAG_RE.search(line):
            flags["tag_lines"] += 1
        if PATH_RE.search(line):
            flags["path_lines"] += 1
        if HTML_RE.search(line):
            flags["html_or_script_lines"] += 1
        if IDENTIFIER_RE.match(line):
            flags["identifier_only_lines"] += 1
    if suffix == ".lua":
        flags["lua_code_lines"] = sum(1 for line in lines if LUA_CODE_RE.search(line))
    result["lines"] = len(lines)
    result["structures"] = dict(flags)
    if suffix == ".txt":
        structured = any(flags[name] for name in (
            "tabular_lines", "key_value_lines", "tag_lines", "path_lines", "html_or_script_lines"
        ))
        result["policy"] = "mixed" if structured else "plain_text"
        result["rule"] = (
            "Translate natural-language lines, but still preserve placeholders and identifiers."
            if not structured else
            "Translate parsed text fields/spans only; whole-file translation is unsafe."
        )
    return result


def audit_tree(root: Path) -> dict:
    files = [path for path in sorted(root.rglob("*")) if path.is_file()]
    details = []
    for path in files:
        item = inspect_file(path)
        item["file"] = path.relative_to(root).as_posix()
        details.append(item)
    suffixes = defaultdict(Counter)
    for item in details:
        suffixes[item["suffix"]][item["policy"]] += 1
    return {
        "root": str(root.resolve()),
        "default": "deny",
        "files": len(details),
        "suffix_summary": {
            suffix: {"files": sum(counts.values()), "policies": dict(counts), "rule": SUFFIX_POLICY.get(suffix, ("blocked", "Unknown format"))[1]}
            for suffix, counts in sorted(suffixes.items())
        },
        "plain_text_files": [item["file"] for item in details if item["policy"] == "plain_text"],
        "mixed_files": [item for item in details if item["policy"] == "mixed"],
        "blocked_files": [item["file"] for item in details if item["policy"] == "blocked"],
    }


def write_audit(root: Path, output: Path) -> dict:
    report = audit_tree(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audit localization-safe resource structures")
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(write_audit(args.root, args.output), ensure_ascii=False))
