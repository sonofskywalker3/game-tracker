from scrapers import steam
from scrapers.base import VALID_SOURCES


def test_parse_owned_games():
    payload = {"response": {"games": [
        {"appid": 620, "name": "Portal 2"},
        {"appid": 0, "name": "bad"},     # no appid -> skipped
        {"appid": 440, "name": ""},      # no name -> skipped
    ]}}
    games = steam.parse_owned_games(payload)
    assert len(games) == 1
    g = games[0]
    assert g.title == "Portal 2" and g.source == "steam" and g.platform == "Steam"
    assert g.external_id == "620" and g.kind == "game"
    assert "620" in (g.cover_url or "")


def test_parse_userdata_makes_id_only_addon_carriers():
    carriers = steam.parse_userdata({"rgOwnedApps": [620, 730, 12345]})
    assert [c.external_id for c in carriers] == ["620", "730", "12345"]
    assert all(c.kind == "addon" and c.source == "steam" for c in carriers)


def test_parsers_handle_empty():
    assert steam.parse_owned_games({}) == []
    assert steam.parse_userdata({}) == []


def test_steam_is_a_valid_source():
    assert "steam" in VALID_SOURCES
