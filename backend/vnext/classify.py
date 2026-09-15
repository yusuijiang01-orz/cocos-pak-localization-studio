from __future__ import annotations

import re

from .models import SourceLanguage, TranslationUnitCandidate, UnitKind
from .normalize import normalize_source, source_key, stable_id


HAN_RE = re.compile(r"[\u3400-\u9fff]")
VI_DIACRITIC_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"àảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíị"
    r"òỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
    r"ÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊ"
    r"ÒỎÕÓỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]"
)
LATIN_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]")
WORD_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]+|[\u3400-\u9fff]+")
SENTENCE_MARK_RE = re.compile(r"[.!?。！？:：;,，；]|[\r\n]")
# Do not flag standalone Â/Ã: both are valid Vietnamese letters and occur in real
# proper names such as "Ân Hồng".  Mojibake from UTF-8 decoded as a legacy
# single-byte codec instead contains the replacement marker, the characteristic
# á»/áº sequences, or Ã/Â/Æ followed by a C1/Latin-1 continuation-like byte.
MOJIBAKE_RE = re.compile(r"\ufffd|ï¿½|á»|áº|(?:Ã|Â|Æ)[\u0080-\u00bf]")
TECH_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"[A-Za-z0-9_.:/\\@#$%+\-=()\[\]{}<>|,&*]+"
    r"|%[-+#0-9.*hlLzjt]*[diuoxXfFeEgGaAcspn]"
    r"|\\[nrt0\\\"']"
    r")\s*$"
)
TITLE_CASE_TOKEN_RE = re.compile(r"^[A-ZĂÂĐÊÔƠƯ][A-Za-z\u00c0-\u024f\u1e00-\u1eff'-]*$")


def detect_language(text: str) -> SourceLanguage:
    value = normalize_source(text)
    if not value:
        return SourceLanguage.EMPTY
    if TECH_ONLY_RE.match(value):
        return SourceLanguage.TECHNICAL
    has_han = bool(HAN_RE.search(value))
    has_vi = bool(VI_DIACRITIC_RE.search(value))
    has_latin = bool(LATIN_RE.search(value))
    if has_han and (has_vi or has_latin):
        return SourceLanguage.MIXED
    if has_han:
        return SourceLanguage.ZH
    if has_vi:
        return SourceLanguage.VI
    if has_latin:
        return SourceLanguage.LATIN_OTHER
    return SourceLanguage.UNKNOWN


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(normalize_source(text)))


def _looks_proper_noun(text: str) -> bool:
    words = [w for w in normalize_source(text).split() if w]
    if not 1 <= len(words) <= 8:
        return False
    alpha = [w.strip(".,:;!?()[]{}") for w in words]
    alpha = [w for w in alpha if LATIN_RE.search(w)]
    if not alpha:
        return False
    title_like = sum(bool(TITLE_CASE_TOKEN_RE.match(w)) for w in alpha)
    return title_like / max(1, len(alpha)) >= 0.75


def classify_source(text: str) -> TranslationUnitCandidate:
    value = normalize_source(text)
    language = detect_language(value)
    flags: list[str] = []
    words = _word_count(value)

    if MOJIBAKE_RE.search(value):
        flags.append("possible_mojibake")
    if language == SourceLanguage.MIXED:
        flags.append("mixed_source")

    if language == SourceLanguage.EMPTY:
        kind = UnitKind.UNKNOWN
    elif language == SourceLanguage.TECHNICAL:
        kind = UnitKind.TECHNICAL
    elif "possible_mojibake" in flags:
        kind = UnitKind.CORRUPT_SOURCE
    elif language == SourceLanguage.ZH:
        kind = UnitKind.ALREADY_CHINESE
    elif language == SourceLanguage.MIXED:
        kind = UnitKind.MIXED_SOURCE
    elif _looks_proper_noun(value):
        kind = UnitKind.PROPER_NOUN
    elif words <= 4 and not SENTENCE_MARK_RE.search(value):
        kind = UnitKind.UI_SHORT
    elif words >= 5 or SENTENCE_MARK_RE.search(value):
        kind = UnitKind.SENTENCE
    else:
        kind = UnitKind.UNKNOWN

    skey = source_key(value)
    return TranslationUnitCandidate(
        source_text=value,
        source_key=skey,
        unit_id=stable_id("u_", skey),
        language=language,
        kind=kind,
        word_count=words,
        risk_flags=tuple(flags),
    )
