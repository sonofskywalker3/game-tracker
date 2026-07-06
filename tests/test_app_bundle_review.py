"""Bundle-split review endpoints (Settings > pending bundle splits)."""
import json

import pytest

import app as app_module
import models


@pytest.fixture
def no_creds(monkeypatch):
    """Keep endpoint tests offline: no Twitch creds -> no IGDB enrichment."""
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: (None, None))


@pytest.fixture
def catalog_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH",
                        tmp_path / "bundle_catalog.default.json")
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")


def _seed_review(title="Weird Bundle", constituents=("Game A", "Game B")):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, pid))
    conn.execute(
        "INSERT INTO bundle_review_queue (game_id, game_title, igdb_id, "
        "bundle_name, constituents_json, reason) VALUES (?, ?, 146075, ?, ?, ?)",
        (gid, title, "IGDB Bundle Name", json.dumps(list(constituents)),
         "title_mismatch"))
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return gid, rid


def test_list_pending_bundle_reviews(client):
    _, rid = _seed_review()
    res = client.get('/api/bundle-review')
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 1
    item = data["items"][0]
    assert item["id"] == rid
    assert item["game_title"] == "Weird Bundle"
    assert item["constituents"] == ["Game A", "Game B"]
    assert item["reason"] == "title_mismatch"


def test_approve_splits_bundle(client, no_creds, catalog_paths):
    gid, rid = _seed_review()
    res = client.post(f'/api/bundle-review/{rid}/approve')
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["count"] == 0
    conn = models.get_db()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    conn.close()
    assert "Weird Bundle" not in titles
    assert "Game A" in titles and "Game B" in titles


def test_approve_with_edited_constituents(client, no_creds, catalog_paths):
    _, rid = _seed_review()
    res = client.post(f'/api/bundle-review/{rid}/approve',
                      json={"constituents": ["Only One"]})
    assert res.status_code == 200
    conn = models.get_db()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    conn.close()
    assert "Only One" in titles and "Game A" not in titles


def test_approve_missing_returns_404(client, no_creds, catalog_paths):
    res = client.post('/api/bundle-review/424242/approve')
    assert res.status_code == 404


def test_approve_empty_constituents_returns_400(client, no_creds, catalog_paths):
    _, rid = _seed_review(constituents=())
    res = client.post(f'/api/bundle-review/{rid}/approve')
    assert res.status_code == 400


def test_dismiss_closes_item(client):
    _, rid = _seed_review()
    res = client.post(f'/api/bundle-review/{rid}/dismiss')
    assert res.status_code == 200
    assert res.get_json()["count"] == 0
    assert client.get('/api/bundle-review').get_json()["items"] == []
