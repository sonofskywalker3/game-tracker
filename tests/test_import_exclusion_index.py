"""Tests for the one-load-per-run exclusion index (import_scraped.is_excluded)."""
import json

import import_scraped as imp
import models


def _write_excluded(monkeypatch, tmp_path, entries):
    path = tmp_path / "excluded_games.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(imp, "EXCLUDED_GAMES_PATH", path)
    return path


def _g(title, platform, source="playstation", external_id=None, cover_url=None):
    return {"title": title, "platform": platform, "source": source,
            "external_id": external_id, "cover_url": cover_url, "source_title": title}


def test_import_games_loads_exclusions_once(temp_db, tmp_path, monkeypatch):
    """The exclusion file is read ONCE per import run, not once per scraped game."""
    _write_excluded(monkeypatch, tmp_path, [
        {"source": "playstation", "external_id": "EXC1",
         "normalized_title": imp.match_key("Some Tool"), "title": "Some Tool"}])
    loads: list[int] = []
    real_load = imp.load_excluded_games

    def counting_load():
        loads.append(1)
        return real_load()

    monkeypatch.setattr(imp, "load_excluded_games", counting_load)
    conn = models.get_db()
    stats = imp.import_games(conn, [
        _g("Some Tool", "PS4", external_id="EXC1"),
        _g("Game A", "PS4", external_id="A1"),
        _g("Game B", "PS4", external_id="B1"),
    ], "playstation")
    conn.commit()
    assert stats.skipped_excluded == 1
    assert stats.new_games == 2
    assert len(loads) == 1
    conn.close()


def test_is_excluded_accepts_prebuilt_index(tmp_path, monkeypatch):
    """A prebuilt index gives the same answers as the load-per-call default."""
    _write_excluded(monkeypatch, tmp_path, [
        {"source": "nintendo", "external_id": "ID1",
         "normalized_title": imp.match_key("Transfer Tool"), "title": "Transfer Tool"},
        {"source": None, "external_id": None,
         "normalized_title": imp.match_key("Batman"), "title": "Batman"}])
    index = imp.build_exclusion_index()
    assert imp.is_excluded("nintendo", "ID1", "Whatever Renamed", index) is True
    assert imp.is_excluded("playstation", "ID1", "x", index) is False
    assert imp.is_excluded(None, None, "Batman", index) is True
    assert imp.is_excluded("nintendo", "OTHER", "A Real Game", index) is False
