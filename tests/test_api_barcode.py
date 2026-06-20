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
