"""DLC review endpoints: count, list (with candidates), resolve, dismiss."""
from __future__ import annotations

import sqlite3

import models


def _seed_review(db_path: str, **kw) -> int:
    """Insert one review row; returns its id."""
    defaults = {"addon_title": "Some Add-on", "source": "nintendo",
                "external_id": "70050000000003", "source_title": "Some Add-on",
                "reason": "no parent game", "game_id": None}
    defaults.update(kw)
    with sqlite3.connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO dlc_review_queue (addon_title, source, external_id, "
            "source_title, reason, game_id) VALUES (?, ?, ?, ?, ?, ?)",
            (defaults["addon_title"], defaults["source"], defaults["external_id"],
             defaults["source_title"], defaults["reason"], defaults["game_id"]))
        c.commit()
        return cur.lastrowid


def test_count_zero_when_empty(client):
    res = client.get("/api/dlc/review/count")
    assert res.get_json() == {"count": 0}


def test_count_excludes_resolved_and_dismissed(client):
    _seed_review(models.DB_PATH)  # open (default external_id)
    rid = _seed_review(models.DB_PATH, external_id="x1")
    with sqlite3.connect(models.DB_PATH) as c:
        c.execute("UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (rid,))
        c.commit()
    rid2 = _seed_review(models.DB_PATH, external_id="x2")
    with sqlite3.connect(models.DB_PATH) as c:
        c.execute("UPDATE dlc_review_queue SET dismissed_at = CURRENT_TIMESTAMP WHERE id = ?", (rid2,))
        c.commit()
    res = client.get("/api/dlc/review/count")
    assert res.get_json() == {"count": 1}


def test_list_returns_items_with_candidates_shape(client):
    with sqlite3.connect(models.DB_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'X', 'x')")
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (2, 'Y', 'y')")
        c.commit()
    _seed_review(models.DB_PATH, reason="ambiguous parent",
                 addon_title="X DLC", external_id="amb1")
    res = client.get("/api/dlc/review")
    body = res.get_json()
    assert "items" in body
    assert "count" in body
    item = body["items"][0]
    assert {"id", "addon_title", "source", "external_id", "source_title",
            "reason", "game_id", "candidates"} <= set(item)
    assert "games" in item["candidates"]
    assert "dlc" in item["candidates"]


def test_list_inlines_ambiguous_parent_candidates(client):
    """For an 'ambiguous parent' review row, the server re-derives candidate
    parents from the current library and inlines them in `candidates.games`.
    The unique normalized_title constraint means only one game can match the
    addon prefix at any given length, so we verify that the matching game is
    returned as the sole candidate."""
    from models import normalize_title
    with sqlite3.connect(models.DB_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'Foo', ?)",
                  (normalize_title("Foo"),))
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (2, 'Bar', ?)",
                  (normalize_title("Bar"),))
        c.commit()
    # Manually seed a review row with reason="ambiguous parent" for "Foo DLC";
    # the endpoint re-derives candidates — "Foo" matches as a prefix of "foo dlc".
    _seed_review(models.DB_PATH, reason="ambiguous parent",
                 addon_title="Foo DLC", external_id="amb2", game_id=None)
    res = client.get("/api/dlc/review")
    item = res.get_json()["items"][0]
    candidate_ids = sorted(g["id"] for g in item["candidates"]["games"])
    # "Foo" (normalized "foo") is the only prefix match for addon "foo dlc".
    assert candidate_ids == [1]


def test_list_inlines_ambiguous_dlc_candidates(client):
    """For 'ambiguous dlc', server should re-derive candidate dlc rows under the
    known parent game and inline them in candidates.dlc."""
    from models import normalize_title
    with sqlite3.connect(models.DB_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'Game Q', ?)",
                  (normalize_title("Game Q"),))
        c.execute("INSERT INTO dlc (game_id, name) VALUES (1, 'Extra')")
        c.execute("INSERT INTO dlc (game_id, name) VALUES (1, 'Extra ')")  # normalize-equal
        c.commit()
    _seed_review(models.DB_PATH, reason="ambiguous dlc",
                 addon_title="Game Q Extra", external_id="amb3", game_id=1)
    res = client.get("/api/dlc/review")
    item = res.get_json()["items"][0]
    names = sorted(d["name"] for d in item["candidates"]["dlc"])
    assert names == sorted(["Extra", "Extra "])


def test_resolve_with_picked_game(client):
    with sqlite3.connect(models.DB_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'W', 'w')")
        c.commit()
    rid = _seed_review(models.DB_PATH, addon_title="W - Pass", external_id="r1")
    res = client.post(f"/api/dlc/review/{rid}/resolve", json={"game_id": 1})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["count"] == 0
    with sqlite3.connect(models.DB_PATH) as c:
        owned = c.execute("SELECT owned FROM dlc WHERE game_id = 1").fetchone()
    assert owned[0] == 1


def test_resolve_404_on_missing_game(client):
    rid = _seed_review(models.DB_PATH, external_id="m1")
    res = client.post(f"/api/dlc/review/{rid}/resolve", json={"game_id": 99999})
    assert res.status_code == 404


def test_resolve_400_on_no_choice(client):
    rid = _seed_review(models.DB_PATH, external_id="m2")
    res = client.post(f"/api/dlc/review/{rid}/resolve", json={})
    assert res.status_code == 400


def test_resolve_400_on_multiple_choices(client):
    rid = _seed_review(models.DB_PATH, external_id="m3")
    res = client.post(f"/api/dlc/review/{rid}/resolve",
                      json={"game_id": 1, "dlc_id": 1})
    assert res.status_code == 400


def test_dismiss_marks_dismissed(client):
    rid = _seed_review(models.DB_PATH, external_id="d1")
    res = client.post(f"/api/dlc/review/{rid}/dismiss", json={})
    assert res.status_code == 200
    assert res.get_json()["count"] == 0
    with sqlite3.connect(models.DB_PATH) as c:
        d = c.execute("SELECT dismissed_at FROM dlc_review_queue WHERE id = ?",
                      (rid,)).fetchone()[0]
    assert d is not None


def test_dismiss_404_on_missing_review_id(client):
    res = client.post("/api/dlc/review/999/dismiss", json={})
    assert res.status_code == 404
