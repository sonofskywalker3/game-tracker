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


def _rank(conn, label):
    return [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_quick_slot_ranks_short_game_above_long(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short Game", hltb_main=300)
    long_ = _add_game(conn, "Long Game", hltb_main=2400)
    ids = _rank(conn, "Switch · Quick")
    assert ids.index(short) < ids.index(long_)
    conn.close()


def test_long_slot_ranks_long_game_above_short(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short Game", hltb_main=300)
    long_ = _add_game(conn, "Long Game", hltb_main=2400)
    ids = _rank(conn, "Switch · Long")
    assert ids.index(long_) < ids.index(short)
    conn.close()


def test_unknown_ttb_is_neutral(temp_db):
    conn = models.get_db()
    a = _add_game(conn, "A", hltb_main=None)
    b = _add_game(conn, "B", hltb_main=None)
    ids = _rank(conn, "Switch · Quick")
    assert a in ids and b in ids
    conn.close()
