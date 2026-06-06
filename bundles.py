"""Curated bundle expansion: one store product that grants several games.

A purchased *bundle* (a single store product granting several separately released
games) should not live in the library as its own phantom row — owning the bundle
means owning its constituents. This module holds the curated
`(source, external_id) -> constituent titles` map and the pure helpers; the
importer (import_scraped) expands on import and offers a one-time cleanup.

Single-product compilations played as one thing (Halo MCC, GTA Trilogy, the Ezio
/ Mega Man Legacy collections) are deliberately NOT here — they stay one game.
Constituents are matched through the importer's normalized-title cascade, so their
casing/punctuation need not be exact; a missing constituent is created from the
title given here (clean_title applies).
"""
from __future__ import annotations

# (source, external_id) -> constituent canonical titles. Extensible curated table.
BUNDLE_CONTENTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("nintendo", "70070000014767"): (
        "Edna & Harvey: Harvey's New Eyes",
        "Edna & Harvey: the Breakout - Anniversary Edition",
    ),
    ("nintendo", "70070000018036"): ("Pikmin 1", "Pikmin 2"),
    ("nintendo", "70070000025556"): ("SteamWorld Heist II", "SteamWorld Build"),
    ("nintendo", "70070000013722"): ("Portal", "Portal 2"),
    ("xbox", "BTNQR63WQV3G"): ("Watch Dogs", "Watch Dogs 2"),
    ("xbox", "BR64DHW9XK6B"): ("Prototype", "Prototype 2"),
    # "Frozen Hearth" is DLC and is intentionally dropped.
    ("xbox", "9P6KBLVP8V3G"): ("Nobody Saves the World",),
    ("xbox", "C4DQHRNN1ZN5"): ("Borderlands 2", "Borderlands: The Pre-Sequel"),
    # --- user-curated additions ---
    ("xbox", "BRW49CBS558D"): ("Batman: Arkham Asylum", "Batman: Arkham City"),
    ("xbox", "C4HB1XWT02DK"): (
        "Assassin's Creed Chronicles: China",
        "Assassin's Creed Chronicles: India",
        "Assassin's Creed Chronicles: Russia",
    ),
    ("xbox", "9NZJGLTJX1J1"): (
        "Borderlands", "Borderlands 2", "Borderlands: The Pre-Sequel", "Borderlands 3",
    ),
    ("nintendo", "70070000014049"): ("Deponia", "Chaos on Deponia", "Goodbye Deponia"),
    ("nintendo", "70010000078053"): ("Doom", "Doom II"),
    # FF I-VI Pixel Remaster: the plain titles match the existing FF I-VI rows
    # (one row per playable game), so this links them rather than creating dupes.
    ("nintendo", "70070000017105"): (
        "Final Fantasy", "Final Fantasy II", "Final Fantasy III",
        "Final Fantasy IV", "Final Fantasy V", "Final Fantasy VI",
    ),
    # Game + (Expansion/Season) Pass bundles: keep only the base game; the pass is
    # DLC, resolved separately by the DLC ownership pipeline (cf. "Frozen Hearth").
    ("nintendo", "70070000000661"): ("Xenoblade Chronicles 2",),
    ("nintendo", "70070000014933"): ("Xenoblade Chronicles 3",),
    ("nintendo", "70070000025331"): ("Brotato",),
    ("nintendo", "70070000013956"): ("Gotta Protectors: Cart of Darkness",),
}


def expand_bundle(source: str | None, external_id: str | None) -> tuple[str, ...] | None:
    """Constituent titles for a known bundle, else None.

    Keyed on the exact (source, external_id); a falsy external_id never matches.
    """
    if not external_id:
        return None
    return BUNDLE_CONTENTS.get((source, external_id))
