"""Enrichment routes via the shared `client` fixture (temp DB + Flask test client)."""
import models


def _game_on_switch(conn, title="Hades", igdb_id=9):
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) "
                 "VALUES ('Switch','Switch','modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO games (title, normalized_title, igdb_id, cover_url) VALUES (?,?,?,?)",
                 (title, models.normalize_title(title), igdb_id, "c.jpg"))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title=?",
                       (models.normalize_title(title),)).fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?,?,1)", (gid, pid))
    conn.commit()
    return gid


def test_status_returns_shape(client):
    r = client.get("/api/enrichment/status")
    assert r.status_code == 200
    data = r.get_json()
    assert "status" in data and "remaining_eligible" in data and "remaining_today" in data
    assert "last_run_date" in data


def test_review_lists_pending(client):
    conn = models.get_db()
    gid = _game_on_switch(conn)
    conn.execute("INSERT INTO upc_review (game_id, platform, upc, product_title, cover_url, status, reason) "
                 "VALUES (?, 'Switch', '777', 'Hades', 'c.jpg', 'pending', 'near title match')", (gid,))
    conn.commit()
    conn.close()
    r = client.get("/api/enrichment/review")
    cands = r.get_json()["candidates"]
    assert len(cands) == 1
    assert cands[0]["title"] == "Hades" and cands[0]["upc"] == "777" and cands[0]["platform"] == "Switch"


def test_confirm_links_registry_and_clears_row(client):
    conn = models.get_db()
    gid = _game_on_switch(conn, igdb_id=55)
    conn.execute("INSERT INTO upc_review (game_id, platform, upc, product_title, cover_url, status, reason) "
                 "VALUES (?, 'Switch', '777', 'Hades', 'c.jpg', 'pending', 'x')", (gid,))
    conn.commit()
    rid = conn.execute("SELECT id FROM upc_review").fetchone()[0]
    conn.close()

    r = client.post(f"/api/enrichment/review/{rid}/confirm")
    assert r.status_code == 200 and r.get_json()["success"] is True

    conn = models.get_db()
    reg = conn.execute("SELECT game_id, igdb_id, platform FROM barcode_registry WHERE upc='777'").fetchone()
    assert (reg["game_id"], reg["igdb_id"], reg["platform"]) == (gid, 55, "Switch")
    assert conn.execute("SELECT COUNT(*) FROM upc_review WHERE id=?", (rid,)).fetchone()[0] == 0
    conn.close()


def test_reject_marks_dismissed(client):
    conn = models.get_db()
    gid = _game_on_switch(conn)
    conn.execute("INSERT INTO upc_review (game_id, platform, upc, product_title, cover_url, status, reason) "
                 "VALUES (?, 'Switch', '777', 'Hades', 'c.jpg', 'pending', 'x')", (gid,))
    conn.commit()
    rid = conn.execute("SELECT id FROM upc_review").fetchone()[0]
    conn.close()

    r = client.post(f"/api/enrichment/review/{rid}/reject")
    assert r.status_code == 200
    conn = models.get_db()
    assert conn.execute("SELECT status FROM upc_review WHERE id=?", (rid,)).fetchone()[0] == "dismissed"
    conn.close()


def test_confirm_missing_row_404(client):
    r = client.post("/api/enrichment/review/99999/confirm")
    assert r.status_code == 404


def test_reject_missing_row_404(client):
    r = client.post("/api/enrichment/review/99999/reject")
    assert r.status_code == 404
