"""Per-slot eligibility + ranking."""
import models
import slots


def _platform_id(conn, short_name):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (short_name,)).fetchone()[0]


def _add_game(conn, title, platform_short, tags=(), status="backlog",
              hltb_main=None, priority=5):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform_short)))
    for t in tags:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (t,))
        tid = conn.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tid))
    if hltb_main is not None:
        conn.execute("UPDATE games SET hltb_main_minutes=? WHERE id=?", (hltb_main, gid))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, ?, ?)",
                 (gid, status, priority))
    conn.commit()
    return gid


def _slot(conn, label):
    row = conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone()
    return dict(row)


def test_platform_hard_filter(temp_db):
    conn = models.get_db()
    sw = _add_game(conn, "Switch Game", "Switch", tags=("Puzzle",))
    _add_game(conn, "PS Game", "PS", tags=("Puzzle",))
    cands = slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
    ids = [c["game"]["id"] for c in cands]
    assert sw in ids
    assert all(c["game"]["title"] != "PS Game" for c in cands)
    conn.close()


def test_low_latency_slot_excludes_lag_sensitive(temp_db):
    conn = models.get_db()
    fighting = _add_game(conn, "Fighter", "PS", tags=("Fighting",))
    turn = _add_game(conn, "Tactics", "PS", tags=("Strategy",))
    # Garage · Console requires low latency -> only the lag-sensitive (Fighting) qualifies
    cands = slots.rank_candidates(conn, _slot(conn, "Garage · Console"))
    ids = [c["game"]["id"] for c in cands]
    assert fighting in ids
    assert turn not in ids
    conn.close()


def test_stream_safe_slot_excludes_lag_sensitive(temp_db):
    conn = models.get_db()
    fighting = _add_game(conn, "Fighter", "PS", tags=("Fighting",))
    turn = _add_game(conn, "Tactics", "PS", tags=("Strategy",))
    cands = slots.rank_candidates(conn, _slot(conn, "Long · Stream-safe"))
    ids = [c["game"]["id"] for c in cands]
    assert turn in ids          # lag-tolerant qualifies
    assert fighting not in ids  # lag-sensitive excluded
    conn.close()


def test_excludes_completed_and_pinned(temp_db):
    conn = models.get_db()
    done = _add_game(conn, "Done", "Switch", tags=("Puzzle",), status="completed")
    pinned = _add_game(conn, "Pinned", "Switch", tags=("Puzzle",))
    conn.execute("UPDATE slots SET current_game_id=? WHERE label='Switch · Long'", (pinned,))
    conn.commit()
    cands = slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
    ids = [c["game"]["id"] for c in cands]
    assert done not in ids       # completed games are not candidates
    assert pinned not in ids     # already slotted elsewhere
    conn.close()


def test_higher_priority_ranks_first(temp_db):
    conn = models.get_db()
    _add_game(conn, "Low", "Switch", tags=("Puzzle",), priority=2)
    hi = _add_game(conn, "High", "Switch", tags=("Puzzle",), priority=9)
    cands = slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
    assert cands[0]["game"]["id"] == hi
    conn.close()
