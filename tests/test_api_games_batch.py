"""POST /api/games/batch — shared backend for multi-select add and bulk import."""
import models
import app as app_module


def _no_enrich(monkeypatch):
    """Neutralize best-effort IGDB enrichment (no network in tests)."""
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: (None, None))


def _seed_platform(short_name="Switch", name="Nintendo Switch"):
    conn = models.get_db()
    row = conn.execute(
        "SELECT id FROM platforms WHERE short_name = ?", (short_name,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO platforms (name, short_name) VALUES (?, ?)",
                     (name, short_name))
        conn.commit()
    conn.close()


def test_batch_add_two_new_games(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    _seed_platform()
    resp = client.post("/api/games/batch", json={"games": [
        {"title": "Chrono Trigger", "platforms": ["Switch"]},
        {"title": "Hades", "cover_url": "https://img/hades.jpg"},
    ]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["added"] == 2
    assert data["skipped"] == 0
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["added", "added"]
    assert all(r["game_id"] for r in data["results"])

    conn = models.get_db()
    row = conn.execute(
        "SELECT g.id, g.cover_url, ur.status FROM games g "
        "JOIN user_ratings ur ON ur.game_id = g.id WHERE g.title = 'Hades'").fetchone()
    assert row["status"] == "backlog"
    assert row["cover_url"] == "https://img/hades.jpg"
    plats = conn.execute(
        "SELECT p.short_name FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
        "JOIN games g ON g.id = gp.game_id WHERE g.title = 'Chrono Trigger'").fetchall()
    assert [p["short_name"] for p in plats] == ["Switch"]
    conn.close()


def test_batch_skips_existing_game(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Hades', 'hades')")
    existing_id = conn.execute("SELECT id FROM games WHERE title='Hades'").fetchone()[0]
    conn.commit()
    conn.close()

    resp = client.post("/api/games/batch", json={"games": [
        {"title": "Hades"},
        {"title": "Celeste"},
    ]})
    data = resp.get_json()
    assert data["added"] == 1
    assert data["skipped"] == 1
    hades = next(r for r in data["results"] if r["title"] == "Hades")
    assert hades["status"] == "exists"
    assert hades["game_id"] == existing_id


def test_batch_dedupes_within_batch(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    resp = client.post("/api/games/batch", json={"games": [
        {"title": "Celeste"},
        {"title": "CELESTE™"},   # normalizes to the same title
    ]})
    data = resp.get_json()
    assert data["added"] == 1
    assert data["skipped"] == 1
    assert [r["status"] for r in data["results"]] == ["added", "exists"]
    assert data["results"][1]["game_id"] == data["results"][0]["game_id"]


def test_batch_missing_title_is_error_not_fatal(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    resp = client.post("/api/games/batch", json={"games": [
        {"title": "   "},
        {"title": "Tunic"},
    ]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["added"] == 1
    assert [r["status"] for r in data["results"]] == ["error", "added"]


def test_batch_rejects_empty_or_malformed_body(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    assert client.post("/api/games/batch", json={}).status_code == 400
    assert client.post("/api/games/batch", json={"games": []}).status_code == 400
    assert client.post("/api/games/batch", json={"games": "nope"}).status_code == 400


def test_batch_rejects_oversized_batch(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    games = [{"title": f"Game {i}"} for i in range(app_module.MAX_BATCH_ADD + 1)]
    resp = client.post("/api/games/batch", json={"games": games})
    assert resp.status_code == 400


def test_batch_unknown_platform_ignored(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    resp = client.post("/api/games/batch", json={"games": [
        {"title": "Okami", "platforms": ["NotARealPlatform"]},
    ]})
    data = resp.get_json()
    assert data["added"] == 1
    conn = models.get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM game_platforms gp JOIN games g ON g.id = gp.game_id "
        "WHERE g.title = 'Okami'").fetchone()[0]
    conn.close()
    assert n == 0


def test_batch_enriches_with_shared_token(client, temp_db, monkeypatch):
    """One token fetch for the whole batch; each added game gets best-effort enrich."""
    import igdb_dlc
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    token_calls = []
    enriched = []
    monkeypatch.setattr(igdb_dlc, "get_access_token",
                        lambda cid, sec: token_calls.append(cid) or "tok")
    monkeypatch.setattr(igdb_dlc, "enrich_game",
                        lambda conn, gid, cid, tok, **kw: enriched.append(gid) or {})
    resp = client.post("/api/games/batch", json={"games": [
        {"title": "Ori"},
        {"title": "Fez"},
    ]})
    assert resp.get_json()["added"] == 2
    assert len(token_calls) == 1
    assert len(enriched) == 2


def test_batch_enrichment_failure_does_not_fail_add(client, temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: ("cid", "sec"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda cid, sec: "tok")

    def boom(*a, **kw):
        raise RuntimeError("igdb down")
    monkeypatch.setattr(igdb_dlc, "enrich_game", boom)
    resp = client.post("/api/games/batch", json={"games": [{"title": "Braid"}]})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["added"] == 1
    assert data["results"][0]["status"] == "added"
