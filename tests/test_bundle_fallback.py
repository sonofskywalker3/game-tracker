"""IGDB bundle fallback: assess auto-vs-review, split through the shared
bundle-catalog path, and the review-queue operations."""
import json

import pytest

import bundle_fallback as bf
import igdb_resolve
import models


# --- assess_bundle_split (pure) ---------------------------------------------

def _c(name, game_type=0, igdb_id=1):
    return {"id": igdb_id, "name": name, "game_type": game_type}


def test_assess_auto_on_exact_title_and_main_constituents():
    verdict, names, reason = bf.assess_bundle_split(
        "the great ace attorney chronicles",
        "The Great Ace Attorney Chronicles",
        [_c("The Great Ace Attorney: Adventures"),
         _c("The Great Ace Attorney 2: Resolve")])
    assert verdict == "auto"
    assert names == ["The Great Ace Attorney: Adventures",
                     "The Great Ace Attorney 2: Resolve"]
    assert reason is None


def test_assess_filters_addon_types_keeps_remaster():
    verdict, names, _ = bf.assess_bundle_split(
        "cool bundle", "Cool Bundle",
        [_c("Base Game", game_type=0),
         _c("Some DLC", game_type=1),
         _c("Remastered Thing", game_type=9)])
    assert verdict == "auto"
    assert names == ["Base Game", "Remastered Thing"]


def test_assess_single_constituent_is_auto():
    # Game + season-pass bundles legitimately resolve to one base game.
    verdict, names, _ = bf.assess_bundle_split(
        "xenoblade chronicles 3 bundle", "Xenoblade Chronicles 3 Bundle",
        [_c("Xenoblade Chronicles 3")])
    assert verdict == "auto"
    assert names == ["Xenoblade Chronicles 3"]


def test_assess_review_on_title_mismatch():
    verdict, names, reason = bf.assess_bundle_split(
        "totally different local title", "Some IGDB Bundle",
        [_c("Game A"), _c("Game B")])
    assert verdict == "review"
    assert names == ["Game A", "Game B"]
    assert reason == "title_mismatch"


def test_assess_review_when_no_usable_constituents():
    for constituents in ([], [_c("Just DLC", game_type=1)]):
        verdict, names, reason = bf.assess_bundle_split(
            "some bundle", "Some Bundle", constituents)
        assert verdict == "review"
        assert names == []
        assert reason == "no_constituents"


def test_assess_review_when_too_many_constituents():
    many = [_c(f"Game {i}") for i in range(bf.MAX_AUTO_CONSTITUENTS + 1)]
    verdict, names, reason = bf.assess_bundle_split(
        "mega bundle", "Mega Bundle", many)
    assert verdict == "review"
    assert len(names) == bf.MAX_AUTO_CONSTITUENTS + 1
    assert reason == "too_many_constituents"


# --- handle_enriched_bundle ---------------------------------------------------

@pytest.fixture
def catalog_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH",
                        tmp_path / "bundle_catalog.default.json")
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _add_game(conn, title, platform="Switch"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name=?",
                       (platform,)).fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, pid))
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit()
    return gid


def _titles(conn):
    return {r[0] for r in conn.execute("SELECT title FROM games")}


def _payload(igdb_id=146075, name="The Great Ace Attorney Chronicles"):
    return {"id": igdb_id, "name": name, "game_type": 3}


def test_handle_auto_splits_and_caches_catalog_entry(monkeypatch, temp_db, catalog_paths):
    import igdb_dlc
    conn = models.get_db()
    gid = _add_game(conn, "The Great Ace Attorney Chronicles")
    monkeypatch.setattr(igdb_resolve, "resolve_bundle_constituents",
                        lambda i, c, t: [_c("The Great Ace Attorney: Adventures"),
                                         _c("The Great Ace Attorney 2: Resolve")])
    enriched: list[int] = []
    monkeypatch.setattr(igdb_dlc, "enrich_game",
                        lambda c, g, ci, to, **kw: enriched.append(g) or
                        {"matched": False, "cover_set": False, "added": 0, "existing": 0})
    monkeypatch.setattr(igdb_resolve, "fetch_game_collections", lambda ids, c, t: {})
    result = bf.handle_enriched_bundle(conn, gid, _payload(), "cid", "tok")
    assert result["action"] == "split"
    assert len(enriched) == 2  # both new constituents get best-effort enrichment
    titles = _titles(conn)
    assert "The Great Ace Attorney Chronicles" not in titles
    assert "The Great Ace Attorney: Adventures" in titles
    assert "The Great Ace Attorney 2: Resolve" in titles
    # runtime cache landed in the per-user catalog (the shared seed file format)
    entry = models.load_bundle_catalog()["the great ace attorney chronicles"]
    assert entry["type"] == "compilation"
    assert entry["igdb_id"] == 146075
    assert entry["constituents"] == ["The Great Ace Attorney: Adventures",
                                     "The Great Ace Attorney 2: Resolve"]
    conn.close()


def test_handle_catalog_hit_expands_without_igdb_call(monkeypatch, temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "The Great Ace Attorney Chronicles")
    models.add_bundle_catalog_entry(
        "the great ace attorney chronicles",
        {"type": "compilation",
         "constituents": ["The Great Ace Attorney: Adventures"]})

    def boom(*a, **k):
        raise AssertionError("catalog hit must not hit IGDB")
    monkeypatch.setattr(igdb_resolve, "resolve_bundle_constituents", boom)
    result = bf.handle_enriched_bundle(conn, gid, _payload(), "cid", "tok")
    assert result["action"] == "applied_catalog"
    assert "The Great Ace Attorney Chronicles" not in _titles(conn)
    assert "The Great Ace Attorney: Adventures" in _titles(conn)
    conn.close()


def test_handle_low_confidence_queues_review_and_keeps_game(monkeypatch, temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    monkeypatch.setattr(igdb_resolve, "resolve_bundle_constituents",
                        lambda i, c, t: [_c("Game A"), _c("Game B")])
    result = bf.handle_enriched_bundle(
        conn, gid, _payload(name="Some Other Bundle"), "cid", "tok")
    assert result["action"] == "queued"
    assert result["reason"] == "title_mismatch"
    assert "Weird Local Name" in _titles(conn)          # nothing destroyed
    assert models.load_bundle_catalog() == {}           # no cache write
    row = conn.execute("SELECT * FROM bundle_review_queue").fetchone()
    assert row["game_id"] == gid
    assert row["igdb_id"] == 146075
    assert row["reason"] == "title_mismatch"
    assert json.loads(row["constituents_json"]) == ["Game A", "Game B"]
    conn.close()


def test_handle_does_not_requeue_open_review(monkeypatch, temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    monkeypatch.setattr(igdb_resolve, "resolve_bundle_constituents",
                        lambda i, c, t: [_c("Game A")])
    bf.handle_enriched_bundle(conn, gid, _payload(name="Other"), "cid", "tok")
    bf.handle_enriched_bundle(conn, gid, _payload(name="Other"), "cid", "tok")
    n = conn.execute("SELECT COUNT(*) FROM bundle_review_queue").fetchone()[0]
    assert n == 1
    conn.close()


def test_handle_missing_game_returns_none(temp_db, catalog_paths):
    conn = models.get_db()
    assert bf.handle_enriched_bundle(conn, 99999, _payload(), "cid", "tok") is None
    conn.close()


# --- review queue operations --------------------------------------------------

def _queue_row(conn, gid, *, igdb_id=146075, constituents=("Game A", "Game B"),
               reason="title_mismatch"):
    conn.execute(
        "INSERT INTO bundle_review_queue (game_id, game_title, igdb_id, "
        "bundle_name, constituents_json, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (gid, "Weird Local Name", igdb_id, "Other",
         json.dumps(list(constituents)), reason))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_pending_reviews_lists_open_rows_with_decoded_constituents(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    rid = _queue_row(conn, gid)
    done = _queue_row(conn, gid, igdb_id=2)
    conn.execute("UPDATE bundle_review_queue SET dismissed_at = CURRENT_TIMESTAMP "
                 "WHERE id = ?", (done,))
    conn.commit()
    items = bf.pending_reviews(conn)
    assert [i["id"] for i in items] == [rid]
    assert items[0]["constituents"] == ["Game A", "Game B"]
    assert items[0]["game_title"] == "Weird Local Name"
    conn.close()


def test_approve_review_splits_and_resolves(temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    rid = _queue_row(conn, gid)
    report = bf.approve_review(conn, rid, client_id=None, token=None)
    assert report["action"] == "split"
    assert "Weird Local Name" not in _titles(conn)
    assert "Game A" in _titles(conn) and "Game B" in _titles(conn)
    assert models.load_bundle_catalog()["weird local name"]["constituents"] == [
        "Game A", "Game B"]
    row = conn.execute("SELECT resolved_at FROM bundle_review_queue WHERE id = ?",
                       (rid,)).fetchone()
    assert row["resolved_at"] is not None
    conn.close()


def test_approve_review_with_edited_constituents(temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    rid = _queue_row(conn, gid)
    bf.approve_review(conn, rid, client_id=None, token=None,
                      constituents=["Only This One"])
    assert "Only This One" in _titles(conn)
    assert "Game A" not in _titles(conn)
    conn.close()


def test_approve_review_rejects_closed_or_missing(temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    rid = _queue_row(conn, gid)
    bf.dismiss_review(conn, rid)
    with pytest.raises(ValueError):
        bf.approve_review(conn, rid, client_id=None, token=None)
    with pytest.raises(ValueError):
        bf.approve_review(conn, 424242, client_id=None, token=None)
    conn.close()


def test_approve_review_rejects_empty_constituents(temp_db, catalog_paths):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    rid = _queue_row(conn, gid, constituents=())
    with pytest.raises(ValueError):
        bf.approve_review(conn, rid, client_id=None, token=None)
    conn.close()


def test_dismiss_review_sets_dismissed(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Weird Local Name")
    rid = _queue_row(conn, gid)
    bf.dismiss_review(conn, rid)
    row = conn.execute("SELECT dismissed_at FROM bundle_review_queue WHERE id = ?",
                       (rid,)).fetchone()
    assert row["dismissed_at"] is not None
    assert bf.pending_reviews(conn) == []
    conn.close()
