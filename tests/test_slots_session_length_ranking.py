import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_game(conn, title, session_length=None, priority=5):
    conn.execute(
        "INSERT INTO games (title, normalized_title, session_length, session_length_source) "
        "VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(title), session_length,
         "catalog" if session_length else None))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, 'backlog', ?)",
                 (gid, priority))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _ids(conn, label):
    return [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_quick_slot_excludes_long_session_games(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short One", session_length="short")
    long_ = _add_game(conn, "Long One", session_length="long")
    ids = _ids(conn, "Switch · Quick")          # max_session_minutes = 60
    assert short in ids
    assert long_ not in ids                      # clean split: long lives only in Long
    conn.close()


def test_quick_slot_boosts_short_above_null(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short One", session_length="short")
    neutral = _add_game(conn, "Neutral One", session_length=None)
    ids = _ids(conn, "Switch · Quick")
    assert ids.index(short) < ids.index(neutral)
    conn.close()


def test_long_slot_boosts_long_and_allows_short(temp_db):
    conn = models.get_db()
    long_ = _add_game(conn, "Long One", session_length="long")
    short = _add_game(conn, "Short One", session_length="short")
    ids = _ids(conn, "Switch · Long")            # min_session_minutes = 60
    assert long_ in ids and short in ids         # short still allowed in Long
    assert ids.index(long_) < ids.index(short)   # long boosted above
    conn.close()


def test_null_session_length_neutral_in_quick(temp_db):
    conn = models.get_db()
    a = _add_game(conn, "A", session_length=None)
    b = _add_game(conn, "B", session_length=None)
    ids = _ids(conn, "Switch · Quick")
    assert a in ids and b in ids                 # null never excluded from Quick
    conn.close()


def test_candidate_dict_includes_time_to_beat(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Timed", session_length="short")
    conn.execute("UPDATE games SET hltb_main_minutes = 600 WHERE id = ?", (gid,))
    conn.commit()
    cand = next(c for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
                if c["game"]["id"] == gid)
    assert cand["time_to_beat_minutes"] == 600   # retained as first-class data
    conn.close()
