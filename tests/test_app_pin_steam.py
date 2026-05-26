"""POST /api/games/<id>/steam records a Steam appid in game_external_ids."""
from __future__ import annotations

import models


def _insert_game(title: str) -> int:
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, models.normalize_title(title)),
    )
    gid = conn.execute(
        "SELECT id FROM games WHERE title = ?", (title,)
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return gid


def test_pin_steam_happy(client):
    gid = _insert_game("Vampire Survivors")
    res = client.post(
        f"/api/games/{gid}/steam",
        json={"url": "https://store.steampowered.com/app/1794680/Vampire_Survivors/"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["appid"] == 1794680
    assert body["game"]["id"] == gid
    # Verify the row landed in game_external_ids:
    conn = models.get_db()
    row = conn.execute(
        "SELECT game_id, source, external_id FROM game_external_ids WHERE source='steam'"
    ).fetchone()
    conn.close()
    assert tuple(row) == (gid, "steam", "1794680")


def test_pin_steam_rejects_non_steam_url(client):
    gid = _insert_game("Vampire Survivors Non-Steam")
    res = client.post(
        f"/api/games/{gid}/steam",
        json={"url": "https://www.igdb.com/games/vampire-survivors"})
    assert res.status_code == 400
    assert "Steam" in (res.get_json() or {}).get("error", "")


def test_pin_steam_rejects_empty(client):
    gid = _insert_game("Vampire Survivors Empty")
    res = client.post(f"/api/games/{gid}/steam", json={"url": ""})
    assert res.status_code == 400


def test_pin_steam_rejects_missing_url(client):
    gid = _insert_game("Vampire Survivors Missing")
    res = client.post(f"/api/games/{gid}/steam", json={})
    assert res.status_code == 400


def test_pin_steam_404_on_missing_game(client):
    res = client.post(
        "/api/games/9999/steam",
        json={"url": "https://store.steampowered.com/app/1/"})
    assert res.status_code == 404


def test_pin_steam_idempotent(client):
    gid = _insert_game("Vampire Survivors Idempotent")
    url = "https://store.steampowered.com/app/1794680/"
    client.post(f"/api/games/{gid}/steam", json={"url": url})
    res = client.post(f"/api/games/{gid}/steam", json={"url": url})
    assert res.status_code == 200
    conn = models.get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM game_external_ids WHERE source='steam'"
    ).fetchone()[0]
    conn.close()
    assert n == 1
