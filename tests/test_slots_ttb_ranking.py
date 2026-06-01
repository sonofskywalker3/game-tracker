"""Time-to-beat is retained as first-class candidate data but no longer orders the
Quick/Long axis (that is now session_length). See the session-traits spec."""
import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_game(conn, title, hltb_main, priority=5):
    conn.execute("INSERT INTO games (title, normalized_title, hltb_main_minutes) VALUES (?, ?, ?)",
                 (title, models.normalize_title(title), hltb_main))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, 'backlog', ?)",
                 (gid, priority))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def test_time_to_beat_surfaced_per_candidate(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Timed Game", hltb_main=900)
    cand = next(c for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
                if c["game"]["id"] == gid)
    assert cand["time_to_beat_minutes"] == 900
    conn.close()


def test_time_to_beat_does_not_order_quick_slot(temp_db):
    conn = models.get_db()
    # Two null-session_length games differing only in HLTB length both qualify and
    # neither is excluded — TTB is no longer the slot axis.
    short = _add_game(conn, "Short Game", hltb_main=300)
    long_ = _add_game(conn, "Long Game", hltb_main=2400)
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))]
    assert short in ids and long_ in ids
    conn.close()


def test_unknown_ttb_is_neutral(temp_db):
    conn = models.get_db()
    a = _add_game(conn, "A", hltb_main=None)
    b = _add_game(conn, "B", hltb_main=None)
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))]
    assert a in ids and b in ids
    conn.close()
