"""Library deduplication: one row per playable game.

This tracker tracks games and progress to decide what to play or buy next — not
a wishlist or a catalog of every edition/version owned. The data model is one
row per playable game; this module collapses duplicate rows (editions, regional
variants, cross-platform copies, cross-source imports) into one. Bundle
expansion (a single store product that grants several separate games) is a
separate concern (see import_scraped/bundles); single-product launchers played
as one thing (Ezio Collection, GTA Trilogy) deliberately stay one game.

Pure detection + merge engine over a sqlite3 connection; the Flask layer in
app.py is a thin wrapper.
"""
from __future__ import annotations

from models import clean_title, normalize_title

FUZZY_THRESHOLD = 0.85

# Edition / qualifier phrases that mark the SAME game in a different edition.
# Used for matching only (display titles are chosen in the merge modal).
# Extensible curated table.
EDITION_QUALIFIERS = (
    "Digital Deluxe Edition", "Spacer's Choice Edition", "Game of the Year Edition",
    "Console Edition", "Definitive Edition", "Complete Edition", "Deluxe Edition",
    "Ultimate Edition", "Special Edition", "Legendary Edition", "Anniversary Edition",
    "Enhanced Edition", "Gold Edition", "Royal Edition", "Collection Edition",
    "GOTY Edition", "The Final Cut", "Remastered", "Remaster", "Redux",
)


def base_key(title: str) -> str:
    """Fresh match key for a title: normalize_title(clean_title(title))."""
    return normalize_title(clean_title(title))


# Normalized qualifier keys, longest first so the most specific strips first.
_NORM_QUALIFIERS = tuple(
    sorted((normalize_title(q) for q in EDITION_QUALIFIERS), key=len, reverse=True)
)


def strip_edition_key(key: str) -> str:
    """Remove a single trailing known edition qualifier from a normalized key."""
    for qualifier in _NORM_QUALIFIERS:
        suffix = " " + qualifier
        if key.endswith(suffix):
            return key[: -len(suffix)].strip()
    return key
