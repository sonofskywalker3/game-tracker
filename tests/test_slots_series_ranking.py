import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_game(conn, title, series_id=None, status="backlog", priority=5):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority, series_id) VALUES (?, ?, ?, ?)",
                 (gid, status, priority, series_id))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _slot_id(conn, label):
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def test_same_series_as_last_history_game_is_boosted(temp_db):
    conn = models.get_db()
    ff = _series(conn, "Final Fantasy")
    sid = _slot_id(conn, "Switch · Long")
    ff_done = _add_game(conn, "FF VII", series_id=ff, status="completed")
    conn.execute("INSERT INTO slot_history (slot_id, game_id, outcome) VALUES (?, ?, 'completed')",
                 (sid, ff_done))
    conn.commit()
    ff_next = _add_game(conn, "FF IX", series_id=ff, priority=5)
    other = _add_game(conn, "Random RPG", series_id=None, priority=9)  # higher priority; only the series boost should flip it
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Long"))]
    assert ids.index(ff_next) < ids.index(other)
    conn.close()


def test_no_history_no_series_boost(temp_db):
    conn = models.get_db()
    ff = _series(conn, "Final Fantasy")
    a = _add_game(conn, "FF IX", series_id=ff, priority=5)
    b = _add_game(conn, "Random RPG", series_id=None, priority=9)
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Long"))]
    assert ids.index(b) < ids.index(a)
    conn.close()
