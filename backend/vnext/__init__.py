"""Studio vNext core primitives.

Phase 1 is intentionally side-by-side with the legacy runtime.  Nothing in this
package is imported by the current Electron workflow until a later migration
phase explicitly opts into it.
"""
SCHEMA_VERSION = 1
CORE_VERSION = "0.1.0"

from .models import SourceLanguage, UnitKind, TargetStatus, QASeverity
from .normalize import normalize_source, source_key, stable_id

__all__ = [
    "SCHEMA_VERSION", "CORE_VERSION",
    "SourceLanguage", "UnitKind", "TargetStatus", "QASeverity",
    "normalize_source", "source_key", "stable_id",
]
