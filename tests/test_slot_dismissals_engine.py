import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_game(conn, title, status="backlog"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _slot_id(conn, label):
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def test_dismissed_game_excluded(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    sid = _slot_id(conn, "Switch · Quick")
    assert any(c["game"]["id"] == gid for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick")))
    slots.dismiss_suggestion(conn, sid, gid)
    assert all(c["game"]["id"] != gid for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick")))
    conn.close()


def test_dismissals_cleared_on_pin(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    other = _add_game(conn, "Other")
    sid = _slot_id(conn, "Switch · Quick")
    slots.dismiss_suggestion(conn, sid, gid)
    slots.pin_game(conn, sid, other, "go")
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals WHERE slot_id=?", (sid,)).fetchone()[0] == 0
    conn.close()


def test_dismissals_cleared_on_outcome(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    pinned = _add_game(conn, "Pinned", status="playing")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, pinned, "beat it")
    slots.dismiss_suggestion(conn, sid, gid)
    slots.apply_outcome(conn, sid, "complete")
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals WHERE slot_id=?", (sid,)).fetchone()[0] == 0
    conn.close()


def test_beat_chase_does_not_clear_dismissals(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    pinned = _add_game(conn, "Pinned")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, pinned, "beat it")
    slots.dismiss_suggestion(conn, sid, gid)
    slots.apply_outcome(conn, sid, "beat", chase=True, new_goal="plat")
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals WHERE slot_id=?", (sid,)).fetchone()[0] == 1
    conn.close()
