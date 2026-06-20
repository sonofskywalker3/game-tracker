import barcode


def test_resolve_requires_upc(client):
    resp = client.get("/api/barcode/resolve")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "upc required"


def test_resolve_returns_cache_hit(client, monkeypatch):
    import models
    conn = models.get_db()
    barcode.cache_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=None)
    conn.commit()
    conn.close()

    resp = client.get("/api/barcode/resolve?upc=abc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["source"] == "cache"
    assert body["candidates"][0]["title"] == "Halo"


def test_resolve_miss_is_source_none(client, monkeypatch):
    # No Twitch creds in the temp config -> client_id None -> never calls IGDB;
    # force the UPC lookup to miss so we get source 'none'.
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: None)
    resp = client.get("/api/barcode/resolve?upc=999")
    assert resp.status_code == 200
    assert resp.get_json() == {"upc": "999", "source": "none", "candidates": []}


def test_post_game_with_upc_writes_cache(client):
    import models
    resp = client.post("/api/games", json={"title": "Celeste", "upc": "abc123"})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    conn = models.get_db()
    row = barcode.cache_get(conn, "abc123")
    conn.close()
    assert row is not None
    assert row["game_id"] == gid
    assert row["title"] == "Celeste"


def test_post_existing_game_with_upc_links_existing(client):
    import models
    first = client.post("/api/games", json={"title": "Hades"})
    gid = first.get_json()["game_id"]
    # Same title again -> 409 existing, but the UPC should still map to it.
    dup = client.post("/api/games", json={"title": "Hades", "upc": "ean999"})
    assert dup.status_code == 409

    conn = models.get_db()
    row = barcode.cache_get(conn, "ean999")
    conn.close()
    assert row["game_id"] == gid


def test_post_game_without_upc_writes_no_cache_row(client):
    import models
    client.post("/api/games", json={"title": "Tunic"})
    conn = models.get_db()
    count = conn.execute("SELECT COUNT(*) FROM barcode_cache").fetchone()[0]
    conn.close()
    assert count == 0
