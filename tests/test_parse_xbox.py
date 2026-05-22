import json
from pathlib import Path

from scrapers.xbox import parse_orders

FIXTURE = Path(__file__).parent / "fixtures" / "xbox_orders_sample.json"


def _body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_extracts_only_games():
    titles = {g.title for g in parse_orders([_body()])}
    assert titles == {"Sample Quest", "Another Game"}  # DLC + subscription excluded


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
