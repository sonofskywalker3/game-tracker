"""igdb_resolve: batched IGDB collection fetch, membership sync, backfill, and
bundle-constituent reverse lookup."""
import models
import igdb_resolve


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_requests(monkeypatch, responses, sent):
    """responses: list of payloads returned in order; sent collects bodies."""
    def fake_post(url, headers=None, data=None, timeout=None):
        sent.append({"url": url, "data": data, "timeout": timeout})
        return _FakeResp(responses.pop(0))
    monkeypatch.setattr(igdb_resolve.requests, "post", fake_post)


def test_fetch_game_collections_batches_and_parses(monkeypatch):
    sent = []
    payload = [
        {"id": 100, "first_release_date": 900,
         "collections": [{"id": 39, "name": "Final Fantasy", "slug": "final-fantasy"}]},
        {"id": 200, "first_release_date": 1672531200,
         "version_parent": {"first_release_date": 1057017600},
         "collections": [{"id": 39, "name": "Final Fantasy", "slug": "final-fantasy"},
                          {"id": 5134, "name": "Compilation of Final Fantasy VII",
                           "slug": "compilation-of-final-fantasy-vii"}]},
        {"id": 300},   # no collections, no date
    ]
    _fake_requests(monkeypatch, [payload], sent)
    out = igdb_resolve.fetch_game_collections([100, 200, 300], "cid", "tok")
    assert len(sent) == 1
    assert sent[0]["timeout"] is not None
    assert "collections.name" in sent[0]["data"]
    assert out[100]["collections"][0]["name"] == "Final Fantasy"
    assert out[100]["original_release_ts"] == 900
    # remaster: original (version_parent) date wins
    assert out[200]["original_release_ts"] == 1057017600
    assert len(out[200]["collections"]) == 2
    assert out[300]["collections"] == []
    assert out[300]["original_release_ts"] is None


def test_fetch_game_collections_splits_large_batches(monkeypatch):
    sent = []
    ids = list(range(1, igdb_resolve.IGDB_BATCH_SIZE + 2))   # one over the cap
    _fake_requests(monkeypatch, [[], []], sent)
    igdb_resolve.fetch_game_collections(ids, "cid", "tok")
    assert len(sent) == 2


def test_fetch_game_collections_empty_skips_call(monkeypatch):
    sent = []
    _fake_requests(monkeypatch, [], sent)
    assert igdb_resolve.fetch_game_collections([], "cid", "tok") == {}
    assert sent == []


def test_sync_game_collections_replaces_memberships(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title, igdb_id) "
                 "VALUES (1, 'FF7', 'ff7', 100)")
    conn.commit()
    igdb_resolve.sync_game_collections(conn, 1, {
        "collections": [{"id": 39, "name": "Final Fantasy", "slug": "ff"}],
        "original_release_ts": 900,
    })
    rows = conn.execute("SELECT collection_id FROM game_collections WHERE game_id=1").fetchall()
    assert [r[0] for r in rows] == [39]
    assert conn.execute("SELECT original_release_ts FROM games WHERE id=1").fetchone()[0] == 900

    # re-sync with a different set replaces, never accumulates stale rows
    igdb_resolve.sync_game_collections(conn, 1, {
        "collections": [{"id": 5134, "name": "Compilation", "slug": "c"}],
        "original_release_ts": 900,
    })
    rows = conn.execute("SELECT collection_id FROM game_collections WHERE game_id=1").fetchall()
    assert [r[0] for r in rows] == [5134]
    names = {r[0] for r in conn.execute("SELECT name FROM collections").fetchall()}
    assert {"Final Fantasy", "Compilation"} <= names   # collection rows persist
    conn.close()


def test_sync_updates_renamed_collection(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title, igdb_id) "
                 "VALUES (1, 'G', 'g', 100)")
    conn.commit()
    igdb_resolve.sync_game_collections(conn, 1, {
        "collections": [{"id": 39, "name": "Old Name", "slug": "old"}],
        "original_release_ts": None})
    igdb_resolve.sync_game_collections(conn, 1, {
        "collections": [{"id": 39, "name": "New Name", "slug": "new"}],
        "original_release_ts": None})
    row = conn.execute("SELECT name, slug FROM collections WHERE id=39").fetchone()
    assert (row[0], row[1]) == ("New Name", "new")
    conn.close()


def test_backfill_collections(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title, igdb_id) "
                 "VALUES (1, 'FF7', 'ff7', 100)")
    conn.execute("INSERT INTO games (id, title, normalized_title, igdb_id) "
                 "VALUES (2, 'Hades', 'hades', 200)")
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (3, 'NoIgdb', 'noigdb')")
    conn.commit()
    sent = []
    _fake_requests(monkeypatch, [[
        {"id": 100, "first_release_date": 900,
         "collections": [{"id": 39, "name": "Final Fantasy", "slug": "ff"}]},
        {"id": 200, "collections": []},
    ]], sent)
    report = igdb_resolve.backfill_collections(conn, "cid", "tok")
    assert report["games"] == 2               # game 3 has no igdb_id -> skipped
    assert report["memberships"] == 1
    assert conn.execute("SELECT COUNT(*) FROM game_collections").fetchone()[0] == 1
    conn.close()


def test_resolve_bundle_constituents(monkeypatch):
    sent = []
    _fake_requests(monkeypatch, [[
        {"id": 76244, "name": "The Great Ace Attorney: Adventures", "game_type": 0},
        {"id": 146081, "name": "The Great Ace Attorney 2: Resolve", "game_type": 0},
    ]], sent)
    out = igdb_resolve.resolve_bundle_constituents(146075, "cid", "tok")
    assert [g["name"] for g in out] == [
        "The Great Ace Attorney: Adventures", "The Great Ace Attorney 2: Resolve"]
    assert "where bundles = (146075)" in sent[0]["data"]
