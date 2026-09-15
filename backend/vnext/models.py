from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceLanguage(str, Enum):
    VI = "vi"
    ZH = "zh"
    MIXED = "mixed"
    LATIN_OTHER = "latin_other"
    TECHNICAL = "technical"
    UNKNOWN = "unknown"
    EMPTY = "empty"


class UnitKind(str, Enum):
    ALREADY_CHINESE = "already_chinese"
    UI_SHORT = "ui_short"
    SENTENCE = "sentence"
    PROPER_NOUN = "proper_noun"
    MIXED_SOURCE = "mixed_source"
    TECHNICAL = "technical"
    CORRUPT_SOURCE = "corrupt_source"
    UNKNOWN = "unknown"


class TargetStatus(str, Enum):
    UNTRANSLATED = "untranslated"
    TM = "tm"
    REFERENCE = "reference"
    MODEL = "model"
    MANUAL = "manual"
    APPROVED = "approved"
    REJECTED = "rejected"


class QASeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


@dataclass(frozen=True)
class TranslationUnitCandidate:
    source_text: str
    source_key: str
    unit_id: str
    language: SourceLanguage
    kind: UnitKind
    word_count: int
    risk_flags: tuple[str, ...] = ()
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QAResult:
    code: str
    severity: QASeverity
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
