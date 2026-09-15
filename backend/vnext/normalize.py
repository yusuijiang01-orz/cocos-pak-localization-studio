from __future__ import annotations

import hashlib
import re
import unicodedata


_HSPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_source(value: str) -> str:
    """Normalize only equivalences safe for translation-memory lookup."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HSPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return _BLANK_LINES_RE.sub("\n\n", text)


def normalized_lookup_key(value: str) -> str:
    """Case-folded lookup key used only for exact TM/glossary matching."""
    return normalize_source(value).casefold()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_key(value: str, source_lang: str = "vi", target_lang: str = "zh-CN") -> str:
    canonical = f"{source_lang}\0{target_lang}\0{normalized_lookup_key(value)}"
    return sha256_text(canonical)


def stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    payload = "\0".join(str(part or "") for part in parts)
    return f"{prefix}{sha256_text(payload)[:length]}"


def file_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def occurrence_fingerprint(pak: str, source_file: str, record_id: str, source_text: str) -> str:
    return sha256_text("\0".join((str(pak or ""), str(source_file or ""), str(record_id or ""), normalize_source(source_text))))
