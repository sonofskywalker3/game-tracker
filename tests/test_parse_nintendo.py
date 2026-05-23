import json
from pathlib import Path

from scrapers.nintendo import is_game_nsuid, parse_orders

FIXTURE = Path(__file__).parent / "fixtures" / "nintendo_orders_sample.json"


def _body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_keeps_games_skips_dlc_hardware_and_malformed():
    titles = {g.title for g in parse_orders([_body()])}
    # 7005 DLC, hardware (6-digit id), nameless and id-less items are all excluded.
    assert titles == {"Sample Switch Game", "Sample Switch 2 Game", "Sample Collection"}
    assert "Nintendo GameCube Controller" not in titles


def test_is_game_nsuid_excludes_dlc_and_hardware():
    assert is_game_nsuid("70010000000001") is True   # base game
    assert is_game_nsuid("70070000000004") is True   # bundle
    assert is_game_nsuid("70050000000003") is False  # DLC (7005)
    assert is_game_nsuid("120833") is False          # hardware (short non-NSUID id)
    assert is_game_nsuid("") is False
    assert is_game_nsuid(None) is False


def test_maps_fields_and_nsuid_id():
    by = {g.title: g for g in parse_orders([_body()])}
    g = by["Sample Switch Game"]
    assert g.platform == "Switch"
    assert g.source == "nintendo"
    assert g.external_id == "70010000000001"
    assert g.source_title == "Sample Switch Game"


def test_switch_2_folds_into_switch():
    by = {g.title: g for g in parse_orders([_body()])}
    assert by["Sample Switch 2 Game"].platform == "Switch"


def test_cover_url_always_none():
    # Covers come from the IGDB pipeline, not the Nintendo hero art (wrong aspect).
    assert all(g.cover_url is None for g in parse_orders([_body()]))


def test_dedups_nsuid_across_overlapping_orders():
    games = parse_orders([_body(), _body()])  # same payload captured twice
    assert sum(1 for g in games if g.external_id == "70010000000001") == 1
