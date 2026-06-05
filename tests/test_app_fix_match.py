"""Fix-match modal endpoints: shaped candidates + keep-current."""
from __future__ import annotations

import models


def _insert_game(title: str, cover: str | None = None) -> int:
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title, cover_url, needs_igdb_review, "
        "igdb_review_reason, igdb_id) VALUES (?, ?, ?, 1, 'bundle', 999)",
        (title, models.normalize_title(title), cover))
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    conn.commit()
    conn.close()
    return gid


def test_candidates_returns_shaped_list_and_current(client, monkeypatch):
    import config
    import igdb_dlc
    import igdb_match
    gid = _insert_game("Aria of Sorrow", cover="https://x/co_old.jpg")
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 110},
        {"igdb_id": 2, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 100},         # dup art -> collapsed
        {"igdb_id": 3, "name": "Aria of Sorrow Alter", "cover_url": "https://x/co_b.jpg",
         "source": "search", "score": 50},          # junk -> dropped
    ])
    res = client.get(f"/api/games/{gid}/igdb-candidates")
    assert res.status_code == 200
    body = res.get_json()
    assert [c["igdb_id"] for c in body["candidates"]] == [1]
    assert body["current"]["cover_url"] == "https://x/co_old.jpg"
    assert body["current"]["title"] == "Aria of Sorrow"


def test_keep_current_clears_review_without_changing_match(client):
    gid = _insert_game("Keep Me", cover="https://x/keep.jpg")
    res = client.post(f"/api/games/{gid}/igdb-keep")
    assert res.status_code == 200
    assert res.get_json() == {"success": True}
    conn = models.get_db()
    row = conn.execute(
        "SELECT igdb_id, cover_url, COALESCE(igdb_locked,0), "
        "COALESCE(needs_igdb_review,0), igdb_review_reason FROM games WHERE id=?",
        (gid,)).fetchone()
    conn.close()
    assert row[0] == 999                       # igdb_id unchanged
    assert row[1] == "https://x/keep.jpg"      # cover unchanged
    assert row[2] == 1                          # locked
    assert row[3] == 0 and row[4] is None       # review cleared


def test_keep_current_404_for_missing_game(client):
    res = client.post("/api/games/999999/igdb-keep")
    assert res.status_code == 404


def test_candidates_attach_platform_labels_and_drop_current_dup(client, monkeypatch):
    import config
    import igdb_dlc
    import igdb_match
    gid = _insert_game("Bugsnax", cover="https://x/co_same.jpg")
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 10, "name": "Bugsnax", "cover_url": "https://x/co_same.jpg",
         "source": "search", "platforms": [39], "score": 30},        # == current -> dropped
        {"igdb_id": 11, "name": "Bugsnax", "cover_url": "https://x/co_full.jpg",
         "source": "search", "platforms": [167, 48, 130], "score": 160},
    ])
    res = client.get(f"/api/games/{gid}/igdb-candidates")
    assert res.status_code == 200
    body = res.get_json()
    assert [c["igdb_id"] for c in body["candidates"]] == [11]         # current-dup removed
    assert body["candidates"][0]["platforms_label"] == "PS5 · PS4 · Switch"
    assert "platforms_label" in body["current"]
