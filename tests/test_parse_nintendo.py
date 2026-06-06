import json
from pathlib import Path

from scrapers.nintendo import classify_nsuid, parse_orders

FIXTURE = Path(__file__).parent / "fixtures" / "nintendo_orders_sample.json"


def _body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_keeps_games_emits_addons_skips_hardware_and_malformed():
    by_kind = {}
    for g in parse_orders([_body()]):
        by_kind.setdefault(g.kind, set()).add(g.title)
    assert by_kind["game"] == {"Sample Switch Game", "Sample Switch 2 Game", "Sample Collection"}
    assert by_kind["addon"] == {"Sample Game - Nintendo Switch 2 Edition Upgrade Pack"}
    assert "Nintendo GameCube Controller" not in by_kind["game"]


def test_classify_nsuid():
    assert classify_nsuid("70010000000001") == "game"   # base game
    assert classify_nsuid("70070000000004") == "game"    # bundle
    assert classify_nsuid("70050000000003") == "addon"   # DLC (7005)
    assert classify_nsuid("120833") is None              # hardware (short non-NSUID id)
    assert classify_nsuid("") is None
    assert classify_nsuid(None) is None


def test_maps_fields_and_nsuid_id():
    by = {g.title: g for g in parse_orders([_body()])}
    g = by["Sample Switch Game"]
    assert g.platform == "Switch"
    assert g.source == "nintendo"
    assert g.external_id == "70010000000001"
    assert g.source_title == "Sample Switch Game"


def test_captures_url_key_for_addon():
    # The add-on's eShop slug (urlKey) is kept so the parent pass can fetch its
    # product page; games without one fall back to None.
    by = {g.title: g for g in parse_orders([_body()])}
    addon = by["Sample Game - Nintendo Switch 2 Edition Upgrade Pack"]
    assert addon.url_key == "sample-game-switch-2-edition-upgrade-pack-70050000000003-switch-2"


def test_switch_2_folds_into_switch():
    by = {g.title: g for g in parse_orders([_body()])}
    assert by["Sample Switch 2 Game"].platform == "Switch"


def test_cover_url_always_none():
    # Covers come from the IGDB pipeline, not the Nintendo hero art (wrong aspect).
    assert all(g.cover_url is None for g in parse_orders([_body()]))


def test_dedups_nsuid_across_overlapping_orders():
    games = parse_orders([_body(), _body()])  # same payload captured twice
    assert sum(1 for g in games if g.external_id == "70010000000001") == 1
