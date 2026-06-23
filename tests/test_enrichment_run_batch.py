"""enrichment.run_batch: writes, idempotency, quota cap (uses temp_db)."""
import enrichment
import models


def _setup(conn, title, short, *, igdb_id=5, category="modern_console"):
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
                 (short, short, category))
    pid = conn.execute("SELECT id FROM platforms WHERE short_name=?", (short,)).fetchone()[0]
    conn.execute("INSERT INTO games (title, normalized_title, igdb_id, cover_url) VALUES (?, ?, ?, ?)",
                 (title, models.normalize_title(title), igdb_id, "cov.jpg"))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title=?",
                       (models.normalize_title(title),)).fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?, ?, 1)",
                 (gid, pid))
    conn.commit()
    return gid


def test_confident_writes_registry(temp_db):
    conn = models.get_db()
    gid = _setup(conn, "Hades", "Switch", igdb_id=42)
    res = enrichment.run_batch(
        conn, search_fn=lambda q: [{"title": "Hades (Nintendo Switch)", "upc": "999"}],
        remaining_fn=lambda: None)
    assert res["found"] == 1
    row = conn.execute("SELECT game_id, igdb_id, platform, cover_url FROM barcode_registry "
                       "WHERE upc='999'").fetchone()
    assert (row["game_id"], row["igdb_id"], row["platform"], row["cover_url"]) == (gid, 42, "Switch", "cov.jpg")
    conn.close()


def test_uncertain_writes_pending_review(temp_db):
    conn = models.get_db()
    gid = _setup(conn, "Doom Eternal", "Switch")
    res = enrichment.run_batch(
        conn, search_fn=lambda q: [{"title": "Doom Eternal (PlayStation 5)", "upc": "777"}],
        remaining_fn=lambda: None)
    assert res["queued"] == 1
    row = conn.execute("SELECT status, upc, game_id FROM upc_review WHERE game_id=?", (gid,)).fetchone()
    assert (row["status"], row["upc"]) == ("pending", "777")
    conn.close()


def test_no_match_writes_no_match_row(temp_db):
    conn = models.get_db()
    gid = _setup(conn, "Stardew Valley", "Switch")
    res = enrichment.run_batch(
        conn, search_fn=lambda q: [{"title": "USB Cable", "upc": "1"}], remaining_fn=lambda: None)
    assert res["no_match"] == 1
    row = conn.execute("SELECT status, upc FROM upc_review WHERE game_id=?", (gid,)).fetchone()
    assert (row["status"], row["upc"]) == ("no_match", None)
    conn.close()


def test_rerun_is_idempotent_no_duplicate_work(temp_db):
    conn = models.get_db()
    _setup(conn, "Hades", "Switch")
    calls = []

    def fn(q):
        calls.append(q)
        return [{"title": "Hades (Nintendo Switch)", "upc": "999"}]
    enrichment.run_batch(conn, search_fn=fn, remaining_fn=lambda: None)
    enrichment.run_batch(conn, search_fn=fn, remaining_fn=lambda: None)  # nothing eligible now
    assert len(calls) == 1  # second batch selected nothing
    conn.close()


def test_budget_caps_calls(temp_db):
    conn = models.get_db()
    for t in ("A", "B", "C", "D"):
        _setup(conn, t, "Switch")
    calls = []

    def fn(q):
        calls.append(q)
        return [{"title": "x", "upc": "z"}]
    res = enrichment.run_batch(conn, budget=2, search_fn=fn, remaining_fn=lambda: None)
    assert len(calls) == 2 and res["calls_used"] == 2
    conn.close()


def test_stops_when_remaining_quota_low(temp_db):
    conn = models.get_db()
    for t in ("A", "B", "C"):
        _setup(conn, t, "Switch")
    calls = []
    # remaining is healthy before the 1st call, then at the safety margin before the 2nd.
    # A correct before-the-call check makes exactly 1 call; an after-the-call check would make 2.
    seq = iter([50, enrichment.UPC_ENRICH_QUOTA_SAFETY_MARGIN])

    def fn(q):
        calls.append(q)
        return [{"title": "x", "upc": "z"}]
    enrichment.run_batch(conn, budget=10, search_fn=fn,
                         remaining_fn=lambda: next(seq, enrichment.UPC_ENRICH_QUOTA_SAFETY_MARGIN))
    assert len(calls) == 1
    conn.close()
