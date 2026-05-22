import json
from pathlib import Path

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
