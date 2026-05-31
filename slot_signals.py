"""Derive session-fit signals from a game's genre tags + hours.

Lookup tables are module-level and extensible (frozensets), per the project's
"fixes must be general" rule — retuning a table re-scores every game with no
migration. All functions are pure: they take already-fetched tag names / row data,
never a DB connection.
"""
from __future__ import annotations

import sqlite3

# Genres whose games are typically grindy/long-form and do NOT suit a short sitting.
# Anything not listed here is treated as short-session tolerant (innocent until
# proven long), so an unknown/untagged game is never silently excluded.
LONG_FORM_GENRES: frozenset[str] = frozenset({
    "Open World", "JRPG", "RPG", "Strategy", "Simulation", "Story Rich",
})

# Genres that suffer from input lag -> excluded from stream-only contexts and
# required for the low-latency (garage) slot.
LATENCY_SENSITIVE_GENRES: frozenset[str] = frozenset({
    "Fighting", "Shooter", "Action", "Platformer", "Racing", "Rhythm", "Metroidvania",
})


def session_tolerant(tag_names: set[str]) -> bool:
    """True if the game is enjoyable in a short (<~1hr) sitting / has clean stops."""
    return not (set(tag_names) & LONG_FORM_GENRES)


def latency_tolerant(tag_names: set[str], override: int | None) -> bool:
    """True if the game plays fine over a streamed (laggy) connection.

    override: 1 -> force tolerant, 0 -> force not, None -> derive from tags.
    """
    if override is not None:
        return bool(override)
    return not (set(tag_names) & LATENCY_SENSITIVE_GENRES)


def _get(row: dict | sqlite3.Row, key: str) -> object:
    """Read a column from a dict or sqlite3.Row, returning None if absent."""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def effective_time_to_beat_minutes(row: dict | sqlite3.Row) -> int | None:
    """Manual override wins; else HLTB 'main story' minutes; else None (unknown)."""
    override = _get(row, "time_to_beat_override_minutes")
    if override is not None:
        return override  # type: ignore[return-value]
    return _get(row, "hltb_main_minutes")  # type: ignore[return-value]
