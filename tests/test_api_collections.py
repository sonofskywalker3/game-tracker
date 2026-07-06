"""Collections API: browse list, detail with chronological ordering, backfill,
and best-effort sync hooks on add / igdb-pick."""
import models
import app as app_module


def _add_game(conn, title, *, igdb_id=None, cover=None, status="backlog",
              original_ts=None):
    conn.execute(
        "INSERT INTO games (title, normalized_title, igdb_id, cover_url, "
        "original_release_ts) VALUES (?, ?, ?, ?, ?)",
        (title, models.normalize_title(title), igdb_id, cover, original_ts))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)",
                 (gid, status))
    return gid


def _collection(conn, cid, name, *game_ids):
    conn.execute("INSERT OR IGNORE INTO collections (id, name, slug) VALUES (?, ?, ?)",
                 (cid, name, name.lower().replace(" ", "-")))
    for gid in game_ids:
        conn.execute("INSERT OR IGNORE INTO game_collections (game_id, collection_id) "
                     "VALUES (?, ?)", (gid, cid))


def test_collections_list_counts_and_sorts(client, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "FF7", cover="https://img/a.jpg")
    b = _add_game(conn, "FF8")
    c = _add_game(conn, "Hades")
    _collection(conn, 39, "Final Fantasy", a, b)
    _collection(conn, 7, "Hades Series", c)
    _collection(conn, 99, "Empty Collection")   # no owned games -> hidden
    conn.commit()
    conn.close()

    data = client.get("/api/collections").get_json()
    names = [c["name"] for c in data["collections"]]
    assert names == ["Final Fantasy", "Hades Series"]   # by owned count desc
    ff = data["collections"][0]
    assert ff["owned_count"] == 2
    assert ff["covers"] == ["https://img/a.jpg"]


def test_collection_detail_sorts_by_original_release(client, temp_db):
    conn = models.get_db()
    remaster = _add_game(conn, "Symphonia Remastered", original_ts=1057017600)
    late = _add_game(conn, "Newer Game", original_ts=1600000000)
    undated = _add_game(conn, "Undated Game")
    _collection(conn, 5, "Tales", late, remaster, undated)
    conn.commit()
    conn.close()

    data = client.get("/api/collections/5").get_json()
    assert data["name"] == "Tales"
    titles = [g["title"] for g in data["games"]]
    assert titles == ["Symphonia Remastered", "Newer Game", "Undated Game"]


def test_collection_detail_404(client, temp_db):
    assert client.get("/api/collections/12345").status_code == 404


def test_backfill_endpoint_requires_credentials(client, temp_db, monkeypatch):
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: (None, None))
    assert client.post("/api/collections/backfill").status_code == 400


def test_backfill_endpoint_runs_and_reports(client, temp_db, monkeypatch):
    import igdb_dlc
    import igdb_resolve
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda cid, sec: "tok")
    monkeypatch.setattr(igdb_resolve, "backfill_collections",
                        lambda conn, cid, tok, progress=None:
                        {"games": 5, "collections": 3, "memberships": 9})
    data = client.post("/api/collections/backfill").get_json()
    assert data == {"games": 5, "collections": 3, "memberships": 9}


def test_create_game_syncs_collections_best_effort(client, temp_db, monkeypatch):
    """After enrichment pins an igdb_id, the new game's collections sync in the
    same request; a resolve failure never fails the add."""
    import igdb_dlc
    import igdb_resolve
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")

    def fake_enrich(conn, gid, cid, tok, **kw):
        conn.execute("UPDATE games SET igdb_id = 100 WHERE id = ?", (gid,))
        conn.commit()
        return {}
    monkeypatch.setattr(igdb_dlc, "enrich_game", fake_enrich)
    monkeypatch.setattr(igdb_resolve, "fetch_game_collections",
                        lambda ids, cid, tok: {100: {
                            "collections": [{"id": 39, "name": "FF", "slug": "ff"}],
                            "original_release_ts": 900}})
    resp = client.post("/api/games", json={"title": "FF7"})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]
    conn = models.get_db()
    rows = conn.execute("SELECT collection_id FROM game_collections WHERE game_id=?",
                        (gid,)).fetchall()
    conn.close()
    assert [r[0] for r in rows] == [39]

    # failure path: resolve blows up -> add still succeeds
    def boom(*a, **k):
        raise RuntimeError("igdb down")
    monkeypatch.setattr(igdb_resolve, "fetch_game_collections", boom)
    resp = client.post("/api/games", json={"title": "FF8"})
    assert resp.status_code == 201


def test_igdb_pick_resyncs_collections(client, temp_db, monkeypatch):
    import igdb_dlc
    import igdb_resolve
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda cid, sec: "tok")
    monkeypatch.setattr(igdb_resolve, "fetch_game_collections",
                        lambda ids, cid, tok: {777: {
                            "collections": [{"id": 8, "name": "Zelda", "slug": "z"}],
                            "original_release_ts": None}})
    conn = models.get_db()
    gid = _add_game(conn, "BotW")
    conn.commit()
    conn.close()
    resp = client.post(f"/api/games/{gid}/igdb-pick", json={"igdb_id": 777})
    assert resp.status_code == 200
    conn = models.get_db()
    rows = conn.execute("SELECT collection_id FROM game_collections WHERE game_id=?",
                        (gid,)).fetchall()
    conn.close()
    assert [r[0] for r in rows] == [8]
