"""Route input-validation fixes: bad client values must 400, not 500, and the
IGDB search query must be sanitized before hitting the Apicalypse body."""
import models
import app as app_module


def _add_game(conn, title="G"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def test_igdb_pick_rejects_non_integer_id(client, temp_db):
    conn = models.get_db()
    gid = _add_game(conn)
    conn.close()
    resp = client.post(f"/api/games/{gid}/igdb-pick",
                       json={"igdb_id": "the-legend-of-zelda"})
    assert resp.status_code == 400
    conn = models.get_db()
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?", (gid,)).fetchone()[0] is None
    conn.close()


def test_igdb_pick_accepts_numeric_string(client, temp_db):
    conn = models.get_db()
    gid = _add_game(conn)
    conn.close()
    resp = client.post(f"/api/games/{gid}/igdb-pick", json={"igdb_id": "1711"})
    assert resp.status_code == 200
    conn = models.get_db()
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?", (gid,)).fetchone()[0] == 1711
    conn.close()


def test_update_game_non_numeric_ttb_is_400(client, temp_db):
    conn = models.get_db()
    gid = _add_game(conn)
    conn.close()
    resp = client.put(f"/api/games/{gid}",
                      json={"time_to_beat_override_minutes": "abc"})
    assert resp.status_code == 400


def test_update_game_non_numeric_input_lag_is_400(client, temp_db):
    conn = models.get_db()
    gid = _add_game(conn)
    conn.close()
    resp = client.put(f"/api/games/{gid}", json={"input_lag_override": "abc"})
    assert resp.status_code == 400


def test_igdb_search_strips_double_quotes(client, temp_db, monkeypatch):
    import fetch_covers
    import requests as requests_module
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda *a: "tok")
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_post(url, headers=None, data=None, **kw):
        captured["data"] = data
        return FakeResp()

    monkeypatch.setattr(requests_module, "post", fake_post)
    resp = client.get('/api/igdb/search?q=Peter%20Jackson%27s%20%22King%20Kong%22')
    assert resp.status_code == 200
    assert 'search "Peter Jackson\'s King Kong";' in captured["data"]
