"""IGDB platform map covers legacy consoles + reverse short_names_for lookup."""
from __future__ import annotations

from igdb_match import IGDB_PLATFORM_IDS, platform_ids_for, short_names_for
from models import LEGACY_PLATFORM_SEED


def test_every_legacy_platform_is_mapped():
    mapped = set(IGDB_PLATFORM_IDS)
    for _name, short in LEGACY_PLATFORM_SEED:
        assert short in mapped, short


def test_known_legacy_ids():
    # A few spot-checks against IGDB's live platform ids (confirmed 2026-06-15).
    assert IGDB_PLATFORM_IDS["3DS"] == frozenset({37})
    assert IGDB_PLATFORM_IDS["NDS"] == frozenset({20})
    assert IGDB_PLATFORM_IDS["PS2"] == frozenset({8})


def test_short_names_for_single_id():
    assert short_names_for({20}) == ["NDS"]


def test_short_names_for_multiple_ids():
    got = set(short_names_for({20, 18, 38}))  # NDS, NES, PSP
    assert got == {"NDS", "NES", "PSP"}


def test_short_names_for_omits_unknown_ids():
    assert short_names_for({999999}) == []


def test_short_names_for_roundtrips_each_short_name():
    for short in IGDB_PLATFORM_IDS:
        assert short in short_names_for(platform_ids_for([short]))


def test_short_names_for_empty():
    assert short_names_for(None) == []
    assert short_names_for(set()) == []
