"""Behavior lock for slots.rank_candidates: pins exact ranked order, scores and
reasons for a small varied library (platforms, tag affinity, fatigue, dismissals,
session lengths, started boost).

This test passed on the pre-batching implementation (2 queries per candidate) and
must stay green across the batched-query refactor: any diff in ranking output is a
regression, not an acceptable side effect.
"""
import models
import slots


def _platform_id(conn, short_name):
    return conn.execute(
        "SELECT id FROM platforms WHERE short_name=?", (short_name,)).fetchone()[0]


def _add_game(conn, title, platform_short, *, tags=(), status="backlog", priority=5,
              session_length=None, rating=None):
    conn.execute(
        "INSERT INTO games (title, normalized_title, session_length) VALUES (?, ?, ?)",
        (title, models.normalize_title(title), session_length))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform_short)))
    for t in tags:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (t,))
        tid = conn.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)",
                     (gid, tid))
    conn.execute(
        "INSERT INTO user_ratings (game_id, status, priority, rating) "
        "VALUES (?, ?, ?, ?)", (gid, status, priority, rating))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _build_library(conn):
    """Varied fixture: returns {name: game_id}."""
    ids = {}
    # Affinity source: rated 9 with Roguelike (completed -> never a candidate).
    ids["rated_donor"] = _add_game(conn, "Rated Donor", "PS", tags=("Roguelike",),
                                   status="completed", rating=9)
    # Fatigue source: completed, tagged Farming, logged in the Quick slot's
    # history below.
    ids["fatigue_donor"] = _add_game(conn, "Fatigue Donor", "Switch", tags=("Farming",),
                                     status="completed")
    # Candidates on Switch.
    ids["continue_me"] = _add_game(conn, "Continue Me", "Switch", status="playing",
                                   session_length="short")
    ids["tasty_rogue"] = _add_game(conn, "Tasty Rogue", "Switch", tags=("Roguelike",),
                                   priority=8, session_length="short")
    ids["farm_again"] = _add_game(conn, "Farm Again", "Switch", tags=("Farming",),
                                  priority=6)
    ids["long_epic"] = _add_game(conn, "Long Epic", "Switch", session_length="long")
    ids["plain_filler"] = _add_game(conn, "Plain Filler", "Switch", priority=3)
    ids["dismissed_one"] = _add_game(conn, "Dismissed One", "Switch", priority=9,
                                     session_length="short")
    # Not on Switch: must be platform-filtered out of both Switch slots.
    ids["ps_only"] = _add_game(conn, "PS Only", "PS", priority=9)

    quick = _slot(conn, "Switch · Quick")
    conn.execute(
        "INSERT INTO slot_history (slot_id, game_id, goal, outcome) "
        "VALUES (?, ?, NULL, 'completed')", (quick["id"], ids["fatigue_donor"]))
    conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (?, ?)",
                 (quick["id"], ids["dismissed_one"]))
    conn.commit()
    return ids


def _ranked(conn, label):
    return [(c["game"]["id"], c["score"], c["reasons"])
            for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_quick_slot_ranking_locked(temp_db):
    conn = models.get_db()
    ids = _build_library(conn)
    assert _ranked(conn, "Switch · Quick") == [
        # playing (+1000) + short-session fit (+25)
        (ids["continue_me"], 1075.0, ["Fits a quick session", "Continue playing"]),
        # priority 8 (+15) + Roguelike affinity (9 avg, 1 game -> +0.9) + short (+25)
        (ids["tasty_rogue"], 90.9,
         ["High priority (8/10)", "Matches your taste", "Fits a quick session"]),
        # priority 3 (-10), nothing else
        (ids["plain_filler"], 40.0, []),
        # priority 6 (+5) - fatigue (20)
        (ids["farm_again"], 35.0, ["Similar to what you just finished"]),
    ]
    conn.close()


def test_long_slot_ranking_locked(temp_db):
    """Same library through 'Switch · Long': no dismissal/history there, long games
    boosted instead of excluded, short games lose the quick-fit boost."""
    conn = models.get_db()
    ids = _build_library(conn)
    assert _ranked(conn, "Switch · Long") == [
        (ids["continue_me"], 1050.0, ["Continue playing"]),
        # 'long' session earns the long-sitting boost here (+25)
        (ids["long_epic"], 75.0, ["Worth a long sitting"]),
        # dismissed only in the Quick slot -> ranks normally here
        (ids["dismissed_one"], 70.0, ["High priority (9/10)"]),
        (ids["tasty_rogue"], 65.9, ["High priority (8/10)", "Matches your taste"]),
        (ids["plain_filler"], 40.0, []),
        # fatigue applies globally
        (ids["farm_again"], 35.0, ["Similar to what you just finished"]),
    ]
    conn.close()
