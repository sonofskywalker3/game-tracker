"""GET /api/games/search?q= returns up to 10 library games matching the query."""
from __future__ import annotations

import sqlite3

import models


def _seed_games(db_path, titles):
    """Insert each title; auto-id."""
    from models import normalize_title
    with sqlite3.connect(db_path) as c:
        for title in titles:
            c.execute(
                "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                (title, normalize_title(title)))
        c.commit()


def test_search_matches_case_insensitive_substring(client):
    _seed_games(models.DB_PATH, [
        "The Witcher 3", "The Witcher 2", "Hollow Knight",
        "Hades", "Hades II", "Vampire Survivors",
    ])
    res = client.get("/api/games/search?q=witcher")
    titles = [g["title"] for g in res.get_json()]
    assert set(titles) == {"The Witcher 3", "The Witcher 2"}


def test_search_returns_id_title_cover_platforms(client):
    _seed_games(models.DB_PATH, ["Hollow Knight"])
    res = client.get("/api/games/search?q=hollow")
    g = res.get_json()[0]
    assert set(g) >= {"id", "title", "cover_url", "platforms"}
    assert isinstance(g["platforms"], list)


def test_search_limit_10(client):
    _seed_games(models.DB_PATH, [f"Pad Game {i}" for i in range(20)])
    res = client.get("/api/games/search?q=pad")
    assert len(res.get_json()) == 10


def test_search_empty_query_returns_empty(client):
    res = client.get("/api/games/search?q=")
    assert res.get_json() == []


def test_search_short_query_returns_empty(client):
    res = client.get("/api/games/search?q=a")  # single char
    assert res.get_json() == []
