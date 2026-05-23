import json
from pathlib import Path

from scrapers.nintendo import parse_orders

FIXTURE = Path(__file__).parent / "fixtures" / "nintendo_orders_sample.json"


def _body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_keeps_games_skips_dlc_and_malformed():
    titles = {g.title for g in parse_orders([_body()])}
    # 7005 DLC, the nameless item, and the id-less item are all excluded.
    assert titles == {"Sample Switch Game", "Sample Switch 2 Game", "Sample Collection"}


def test_maps_fields_and_nsuid_id():
    by = {g.title: g for g in parse_orders([_body()])}
    g = by["Sample Switch Game"]
    assert g.platform == "Switch"
    assert g.source == "nintendo"
    assert g.external_id == "70010000000001"
    assert g.source_title == "Sample Switch Game"
    assert g.cover_url == (
        "https://assets.nintendo.com/image/upload/"
        "store/software/switch/70010000000001/abc123cover"
    )


def test_switch_2_folds_into_switch():
    by = {g.title: g for g in parse_orders([_body()])}
    assert by["Sample Switch 2 Game"].platform == "Switch"


def test_bundle_without_image_has_no_cover():
    by = {g.title: g for g in parse_orders([_body()])}
    assert by["Sample Collection"].cover_url is None


def test_dedups_nsuid_across_overlapping_orders():
    games = parse_orders([_body(), _body()])  # same payload captured twice
    assert sum(1 for g in games if g.external_id == "70010000000001") == 1
