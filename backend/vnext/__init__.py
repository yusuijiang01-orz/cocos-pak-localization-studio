"""Studio vNext core primitives.

vNext is developed side-by-side with the legacy runtime. Nothing in this
package replaces the current Electron translation/build path until a later
migration phase explicitly opts into it.
"""
SCHEMA_VERSION = 1
CORE_VERSION = "0.2.0"

from .models import SourceLanguage, UnitKind, TargetStatus, QASeverity
from .normalize import normalize_source, source_key, stable_id

__all__ = [
    "SCHEMA_VERSION", "CORE_VERSION",
    "SourceLanguage", "UnitKind", "TargetStatus", "QASeverity",
    "normalize_source", "source_key", "stable_id",
]
