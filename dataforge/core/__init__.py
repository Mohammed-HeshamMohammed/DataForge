"""Core data layer: record types, file I/O, normalisation and deduplication."""

from dataforge.core.dedupe import DedupeConfig, DedupeResult, Deduplicator
from dataforge.core.io import load_table, save_table
from dataforge.core.models import ColumnRoles, MatchCandidate

__all__ = [
    "ColumnRoles",
    "DedupeConfig",
    "DedupeResult",
    "Deduplicator",
    "MatchCandidate",
    "load_table",
    "save_table",
]
