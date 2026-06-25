"""sort-by-release must order a series by each game's pinned igdb_id release date,
never a fuzzy title re-search (which cross-matched same-franchise titles)."""
import app as app_module
import models


def _series_with_games(games):
    """games: list of (title, igdb_id). Returns (series_id, {title: game_id})."""
    conn = models.get_db()
    conn.execute("INSERT INTO series (name) VALUES ('Test Series')")
    sid = conn.execute("SELECT id FROM series WHERE name='Test Series'").fetchone()[0]
    ids = {}
    for title, igdb_id in games:
        conn.execute("INSERT INTO games (title, normalized_title, igdb_id) VALUES (?,?,?)",
                     (title, models.normalize_title(title), igdb_id))
        gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
        conn.execute("INSERT INTO user_ratings (game_id, status, series_id, series_order) "
                     "VALUES (?, 'backlog', ?, 0)", (gid, sid))
        ids[title] = gid
    conn.commit()
    conn.close()
    return sid, ids


def test_sort_by_release_orders_by_igdb_id_date(client, monkeypatch):
    sid, _ = _series_with_games([
        ("Final Fantasy XV", 359),
        ("Final Fantasy", 100),
        ("Final Fantasy IX", 421),
        ("No Pin Game", None),
    ])
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    captured = {}

    def fake_dates(igdb_ids, cid, sec):
        captured["ids"] = list(igdb_ids)
        return {100: 565000000, 421: 962928000, 359: 1480000000}  # 1987, 2000, 2016

    monkeypatch.setattr(app_module, "igdb_release_dates_by_id", fake_dates)
    resp = client.post(f"/api/series/{sid}/sort-by-release")
    assert resp.status_code == 200

    # The lookup was keyed by the games' igdb_ids (the None game is harmless).
    assert 100 in captured["ids"] and 359 in captured["ids"] and 421 in captured["ids"]

    conn = models.get_db()
    order = {r["title"]: r["series_order"] for r in conn.execute(
        "SELECT g.title, ur.series_order FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE ur.series_id=?", (sid,))}
    conn.close()
    assert order["Final Fantasy"] == 0      # 1987 earliest
    assert order["Final Fantasy IX"] == 1   # 2000
    assert order["Final Fantasy XV"] == 2   # 2016
    assert order["No Pin Game"] == 3        # no igdb_id / no date sorts last


def test_sort_by_release_empty_series_400(client):
    conn = models.get_db()
    conn.execute("INSERT INTO series (name) VALUES ('Empty')")
    sid = conn.execute("SELECT id FROM series WHERE name='Empty'").fetchone()[0]
    conn.commit()
    conn.close()
    assert client.post(f"/api/series/{sid}/sort-by-release").status_code == 400


def test_igdb_release_dates_by_id_batches_and_parses(monkeypatch):
    import app as a

    sent = {}

    def fake_post(url, headers, data):
        sent["url"] = url
        sent["data"] = data

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return [{"id": 421, "first_release_date": 962928000},
                        {"id": 100}]  # 100 has no date -> omitted
        return _R()

    monkeypatch.setattr(a, "requests", type("M", (), {"post": staticmethod(fake_post),
                                                      "RequestException": Exception}))
    monkeypatch.setattr("fetch_covers.get_access_token", lambda cid, sec: "tok")
    out = a.igdb_release_dates_by_id([421, 100, 421], "cid", "sec")
    assert out == {421: 962928000}
    assert "where id = (100,421)" in sent["data"]  # deduped + sorted, single call


def test_igdb_release_dates_by_id_empty_skips_call(monkeypatch):
    import app as a

    def boom(*a_, **k_):
        raise AssertionError("must not call IGDB for empty id list")

    monkeypatch.setattr("fetch_covers.get_access_token", boom)
    assert a.igdb_release_dates_by_id([None, 0], "cid", "sec") == {}
