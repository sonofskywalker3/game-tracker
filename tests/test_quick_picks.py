"""get_quick_picks: quick_session must reflect game length, not logged hours
(hours_played defaults to 0, which made every backlog game 'quick')."""
import models
from recommendation import get_quick_picks


def _add(conn, title, *, status="backlog", session_length=None,
         hltb_main=None, ttb_override=None, tag=None):
    conn.execute(
        "INSERT INTO games (title, normalized_title, session_length, "
        "hltb_main_minutes, time_to_beat_override_minutes) VALUES (?, ?, ?, ?, ?)",
        (title, models.normalize_title(title), session_length, hltb_main, ttb_override))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    if tag:
        conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES (?, 'genre')", (tag,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()[0]
        conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tag_id))
    conn.commit()
    return gid


def test_quick_session_excludes_long_unstarted_game(temp_db):
    conn = models.get_db()
    long_id = _add(conn, "Hundred Hour JRPG", session_length="long", hltb_main=6000)
    short_id = _add(conn, "Short Indie Gem", session_length="short")
    picks = get_quick_picks(conn, count=5)
    ids = [r["id"] for r in picks["quick_session"]]
    assert short_id in ids
    assert long_id not in ids
    conn.close()


def test_quick_session_includes_short_time_to_beat(temp_db):
    conn = models.get_db()
    gid = _add(conn, "Five Hour Romp", hltb_main=300)
    picks = get_quick_picks(conn, count=5)
    assert gid in [r["id"] for r in picks["quick_session"]]
    conn.close()


def test_quick_session_ttb_override_wins(temp_db):
    """A long HLTB estimate corrected short by the user still qualifies."""
    conn = models.get_db()
    gid = _add(conn, "Actually Short", hltb_main=6000, ttb_override=240)
    picks = get_quick_picks(conn, count=5)
    assert gid in [r["id"] for r in picks["quick_session"]]
    conn.close()


def test_quick_session_keeps_short_genre_tags(temp_db):
    conn = models.get_db()
    gid = _add(conn, "Untimed Puzzler", tag="Puzzle")
    picks = get_quick_picks(conn, count=5)
    assert gid in [r["id"] for r in picks["quick_session"]]
    conn.close()
