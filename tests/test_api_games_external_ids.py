"""GET /api/games/<id> includes an external_ids dict (used by the 'Change cover' button)."""
from __future__ import annotations

import sqlite3

import models


def _seed(db_path, *, with_ids: bool):
    """Add one game (id=1) + optionally two external_ids rows."""
    with sqlite3.connect(db_path) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'X', 'x')")
        if with_ids:
            c.execute(
                "INSERT INTO game_external_ids (game_id, source, external_id, source_title) "
                "VALUES (1, 'steam', '1794680', 'X')")
            c.execute(
                "INSERT INTO game_external_ids (game_id, source, external_id, source_title) "
                "VALUES (1, 'playstation', 'EP1234-X', 'X')")
        c.commit()


def test_get_game_includes_external_ids_dict(client):
    """Check the conftest fixture's db path attribute - replace `models.DB_PATH`
    below with whatever attribute the conftest fixture actually monkeypatches.
    Look at `tests/test_app_pin_steam.py` to see how the existing test reads
    DB_PATH."""
    _seed(models.DB_PATH, with_ids=True)
    res = client.get("/api/games/1")
    assert res.status_code == 200
    body = res.get_json()
    assert body["external_ids"] == {"steam": "1794680", "playstation": "EP1234-X"}


def test_get_game_empty_external_ids_is_empty_object(client):
    _seed(models.DB_PATH, with_ids=False)
    res = client.get("/api/games/1")
    body = res.get_json()
    assert body["external_ids"] == {}
