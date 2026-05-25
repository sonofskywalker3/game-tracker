import json
from pathlib import Path

from scrapers.xbox import parse_orders

FIXTURE = Path(__file__).parent / "fixtures" / "xbox_orders_sample.json"


def _body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_extracts_games_and_addons_skips_subscriptions():
    by_kind = {}
    for g in parse_orders([_body()]):
        by_kind.setdefault(g.kind, set()).add(g.title)
    assert by_kind["game"] == {"Sample Quest", "Another Game"}
    assert by_kind["addon"] == {"Sample Quest - Season Pass"}
    assert "Game Pass Ultimate" not in by_kind.get("game", set())
    assert "Game Pass Ultimate" not in by_kind.get("addon", set())


def test_maps_fields():
    by = {g.title: g for g in parse_orders([_body()])}
    g = by["Sample Quest"]
    assert g.platform == "Xbox"
    assert g.source == "xbox"
    assert g.external_id == "9PSAMPLE0001"
    assert g.cover_url.endswith("sq.png")


def test_dedups_across_overlapping_responses():
    games = parse_orders([_body(), _body()])  # same payload captured twice
    assert sum(1 for g in games if g.title == "Another Game") == 1
