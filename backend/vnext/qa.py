from __future__ import annotations

import re

from localization_tm import validate_tokens
from .models import QAResult, QASeverity
from .normalize import normalize_source


HAN_RE = re.compile(r"[\u3400-\u9fff]")
VI_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"àảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíị"
    r"òỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
    r"ÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊ"
    r"ÒỎÕÓỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]"
)
LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{2,}\b")
SPACED_HAN_RE = re.compile(r"(?:[\u3400-\u9fff]\s+){3,}[\u3400-\u9fff]")
REPLACEMENT_RE = re.compile(r"\ufffd|ï¿½")
ALLOWED_LATIN = {"NPC", "PK", "PVP", "PVE", "VIP", "HP", "MP", "EXP", "ID", "UI", "URL", "GM", "FPS"}


def _residual_latin_words(text: str) -> list[str]:
    return [word for word in LATIN_WORD_RE.findall(text) if word.upper() not in ALLOWED_LATIN]


def evaluate_translation(source: str, target: str) -> list[QAResult]:
    findings: list[QAResult] = []
    src = str(source or "")
    tgt = str(target or "")
    if not normalize_source(tgt):
        findings.append(QAResult("EMPTY_TARGET", QASeverity.ERROR, "译文为空"))
        return findings

    ok, source_tokens, target_tokens = validate_tokens(src, tgt)
    if not ok:
        findings.append(QAResult(
            "PROTECTED_TOKEN_MISMATCH", QASeverity.FATAL,
            "占位符、标签或控制标记与原文不一致",
            {"source_tokens": source_tokens, "target_tokens": target_tokens},
        ))
    if REPLACEMENT_RE.search(tgt):
        findings.append(QAResult("ENCODING_REPLACEMENT_CHAR", QASeverity.FATAL, "译文包含乱码替换字符"))

    has_han = bool(HAN_RE.search(tgt))
    residual_vi = bool(VI_RE.search(tgt))
    residual_latin = _residual_latin_words(tgt)
    if has_han and residual_vi:
        findings.append(QAResult("ZH_VI_MIXED", QASeverity.ERROR, "译文仍包含越南语重音字符"))
    elif has_han and residual_latin:
        findings.append(QAResult(
            "ZH_LATIN_MIXED", QASeverity.WARNING,
            "中文译文仍包含未允许的拉丁词", {"words": residual_latin[:20]},
        ))
    if SPACED_HAN_RE.search(tgt):
        findings.append(QAResult("WORD_BY_WORD_SPACED_HAN", QASeverity.ERROR, "疑似逐词替换产生的单字空格中文"))
    if normalize_source(src) == normalize_source(tgt):
        findings.append(QAResult("UNCHANGED_TARGET", QASeverity.WARNING, "译文与原文相同"))
    return findings


def is_build_safe(findings: list[QAResult]) -> bool:
    return not any(item.severity in (QASeverity.ERROR, QASeverity.FATAL) for item in findings)
