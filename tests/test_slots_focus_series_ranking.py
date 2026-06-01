import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_game(conn, title, platform="Switch", series_id=None, series_role=None):
    conn.execute(
        "INSERT INTO games (title, normalized_title, series_role, series_role_source) "
        "VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(title), series_role,
         "catalog" if series_role else None))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform)))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority, series_id) "
                 "VALUES (?, 'backlog', 5, ?)", (gid, series_id))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _set_focus(conn, label, series_id):
    conn.execute("UPDATE slots SET focus_series_id=? WHERE label=?", (series_id, label))
    conn.commit()


def _ids(conn, label):
    return [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_focus_series_boosts_series_games(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    in_series = _add_game(conn, "Zelda BotW", series_id=sid)
    other = _add_game(conn, "Some Other Game")
    _set_focus(conn, "Switch · Quick", sid)
    ids = _ids(conn, "Switch · Quick")
    assert ids.index(in_series) < ids.index(other)
    conn.close()


def test_long_slot_routes_mainline_above_spinoff(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    mainline = _add_game(conn, "Zelda Mainline", series_id=sid, series_role="mainline")
    spinoff = _add_game(conn, "Zelda Spinoff", series_id=sid, series_role="spinoff")
    _set_focus(conn, "Switch · Long", sid)       # min_session_minutes set -> long slot
    ids = _ids(conn, "Switch · Long")
    assert ids.index(mainline) < ids.index(spinoff)
    conn.close()


def test_short_slot_routes_spinoff_above_mainline(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    mainline = _add_game(conn, "Zelda Mainline", series_id=sid, series_role="mainline")
    spinoff = _add_game(conn, "Zelda Spinoff", series_id=sid, series_role="spinoff")
    _set_focus(conn, "Switch · Quick", sid)      # max_session_minutes set -> short slot
    ids = _ids(conn, "Switch · Quick")
    assert ids.index(spinoff) < ids.index(mainline)
    conn.close()


def test_focus_series_respects_platform_filter(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    off_platform = _add_game(conn, "Zelda PS", platform="PS", series_id=sid, series_role="mainline")
    _set_focus(conn, "Switch · Long", sid)       # Switch-only slot
    ids = _ids(conn, "Switch · Long")
    assert off_platform not in ids               # platform hard-filter still wins
    conn.close()
