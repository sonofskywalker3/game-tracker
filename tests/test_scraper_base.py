import pytest

from scrapers.base import ScrapedGame, write_scrape, read_scrape


def test_scraped_game_defaults_source_title_to_title():
    g = ScrapedGame(title="Hades", platform="PS5", source="playstation")
    assert g.source_title == "Hades"


def test_write_and_read_roundtrip(tmp_path):
    games = [
        ScrapedGame("Hades", "PS5", "playstation", external_id="P1", cover_url="u"),
        ScrapedGame("Celeste", "Switch", "playstation"),
    ]
    out = write_scrape("playstation", games, out_dir=tmp_path)
    assert out.exists()
    rows = read_scrape(out)
    assert [r["title"] for r in rows] == ["Hades", "Celeste"]
    assert rows[0]["external_id"] == "P1"
    assert rows[1]["source_title"] == "Celeste"


def test_write_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError):
        write_scrape("steam", [], out_dir=tmp_path)
