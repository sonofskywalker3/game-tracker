import json
from pathlib import Path

from scrapers import playstation
from scrapers.playstation import _extract, parse_games

FIXTURE = Path(__file__).parent / "fixtures" / "playstation_purchased_sample.json"


def _games():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {g.title: g for g in _extract(payload)}


def test_extracts_all_games():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(_extract(payload)) == 2


def test_maps_platform_and_id_and_cover():
    by = _games()
    assert by["Returnal Sample"].platform == "PS5"
    assert by["Returnal Sample"].external_id == "UP0000-PPSA00001_00-SAMPLE0000000001"
    assert by["Returnal Sample"].cover_url.endswith("returnal.png")
    assert by["Bloodborne Sample"].platform == "PS4"
    assert by["Returnal Sample"].source == "playstation"


def test_falls_back_to_titleid_and_default_platform():
    games = parse_games([{"name": "Mystery Game", "titleId": "PPSA99999_00"}])
    assert games[0].external_id == "PPSA99999_00"
    assert games[0].platform == "PS4"  # default when platform missing


def test_skips_items_without_name():
    assert parse_games([{"productId": "X"}, {"name": "Keeper", "platform": "PS5"}]) \
        and len(parse_games([{"productId": "X"}, {"name": "Keeper"}])) == 1


def _game_page_bodies():
    """A trimmed game-page GraphQL body: base game + owned/priced/unavailable add-ons.

    Shape mirrors .recon/psn_store_* (objects nested under data; parse walks recursively).
    """
    return [{
        "data": {"productRetrieve": {"relatedItems": [
            {"id": "UP1063-PPSA06812_00-0000000000000000", "name": "Ys VIII",
             "storeDisplayClassification": "FULL_GAME", "price": None,
             "platforms": ["PS4"]},
            {"id": "UP1063-PPSA06812_00-YS08JPDLC00N0060", "name": "Ys VIII - Bait Set",
             "storeDisplayClassification": "ITEM", "platforms": ["PS4"],
             "price": {"basePrice": "Purchased"}},
            {"id": "UP1063-PPSA06812_00-YS08JPDLC00N0099", "name": "Ys VIII - Recipe Pack",
             "storeDisplayClassification": "ITEM", "platforms": ["PS4"],
             "price": {"basePrice": "$0.99"}},
            {"id": "UP4497-PPSA03974_00-EXPANSION1B00000", "name": "Pre-Order Bonus",
             "storeDisplayClassification": "VEHICLE", "platforms": ["PS5"],
             "price": {"basePrice": "Unavailable"}},
        ]}}
    }]


def test_parse_addons_keeps_only_purchased():
    addons = playstation.parse_addons(_game_page_bodies())
    assert [a.external_id for a in addons] == ["UP1063-PPSA06812_00-YS08JPDLC00N0060"]
    a = addons[0]
    assert a.kind == "addon"
    assert a.source == "playstation"
    assert a.title == "Ys VIII - Bait Set"
    assert a.source_title == "Ys VIII - Bait Set"
    assert a.platform == "PS4"


def test_parse_addons_excludes_base_game_even_if_owned():
    bodies = [{"x": [{"id": "UP1063-PPSA06812_00-0000000000000000", "name": "Ys VIII",
                      "storeDisplayClassification": "FULL_GAME",
                      "price": {"basePrice": "Purchased"}}]}]
    assert playstation.parse_addons(bodies) == []


def test_parse_addons_dedupes_by_id():
    bodies = _game_page_bodies() + _game_page_bodies()
    addons = playstation.parse_addons(bodies)
    assert len(addons) == 1


def test_parse_addons_skips_malformed_bodies():
    assert playstation.parse_addons([None, {}, {"data": None}, 42]) == []
