"""Studio vNext core primitives.

vNext is developed side-by-side with the legacy runtime. Nothing in this
package replaces the legacy data/build contracts until an explicit vNext path
opts into them.
"""
SCHEMA_VERSION = 2
CORE_VERSION = "0.5.0"

from .models import SourceLanguage, UnitKind, TargetStatus, QASeverity
from .normalize import normalize_source, source_key, stable_id

__all__ = [
    "SCHEMA_VERSION", "CORE_VERSION",
    "SourceLanguage", "UnitKind", "TargetStatus", "QASeverity",
    "normalize_source", "source_key", "stable_id",
]
