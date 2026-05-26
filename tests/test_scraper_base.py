import pytest
from dataclasses import asdict

from scrapers.base import ScrapedGame, write_scrape, read_scrape


def test_scraped_game_defaults_source_title_to_title():
    g = ScrapedGame(title="Hades", platform="PS5", source="playstation")
    assert g.source_title == "Hades"


def test_write_and_read_roundtrip(tmp_path):
    games = [
        ScrapedGame("Hades", "PS5", "playstation", external_id="P1", cover_url="u"),
        ScrapedGame("Journey", "PS4", "playstation", status_hint="played"),
        ScrapedGame("極限脱出", "PS4", "playstation"),  # non-ASCII (ensure_ascii=False)
    ]
    out = write_scrape("playstation", games, out_dir=tmp_path)
    assert out.exists()
    rows = read_scrape(out)
    assert [r["title"] for r in rows] == ["Hades", "Journey", "極限脱出"]
    assert rows[0]["external_id"] == "P1"
    assert rows[1]["source_title"] == "Journey"
    assert rows[1]["status_hint"] == "played"
    assert rows[2]["title"] == "極限脱出"  # non-ASCII survives the JSON round-trip


def test_write_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError):
        write_scrape("fake_vendor", [], out_dir=tmp_path)


def test_scrapedgame_kind_defaults_to_game():
    g = ScrapedGame(title="X", platform="Switch", source="nintendo")
    assert g.kind == "game"
    assert asdict(g)["kind"] == "game"


def test_scrapedgame_kind_addon_round_trips():
    g = ScrapedGame(title="X - Season Pass", platform="Xbox", source="xbox", kind="addon")
    assert asdict(g)["kind"] == "addon"
