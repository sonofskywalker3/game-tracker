import sqlite3

from dedup import (
    base_key, compute_merged_curation, find_duplicate_groups, group_candidates,
    merge_games, refresh_normalized_titles, strip_edition_key,
)
from models import normalize_title


def test_base_key_normalizes_via_clean_title():
    # Strips platform-edition suffix + lowercases + drops punctuation.
    assert base_key("Brotato - Nintendo Switch 2 Edition") == "brotato"
    assert base_key("AI: The Somnium Files") == "ai the somnium files"


def test_strip_edition_key_removes_known_qualifier():
    assert strip_edition_key("the outer worlds spacers choice edition") == "the outer worlds"
    assert strip_edition_key("disco elysium the final cut") == "disco elysium"
    assert strip_edition_key("dont starve console edition") == "dont starve"


def test_strip_edition_key_leaves_plain_titles():
    assert strip_edition_key("hollow knight") == "hollow knight"
    # "Together" is not an edition qualifier — must NOT be stripped.
    assert strip_edition_key("dont starve together") == "dont starve together"


def _games_conn(rows, dismissed=()):
    """In-memory games + not_duplicates. rows: (id, title)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
                 "normalized_title TEXT)")
    conn.execute("CREATE TABLE not_duplicates (game_id_lo INTEGER, game_id_hi INTEGER, "
                 "PRIMARY KEY (game_id_lo, game_id_hi))")
    conn.executemany("INSERT INTO games (id, title) VALUES (?, ?)", rows)
    for a, b in dismissed:
        conn.execute("INSERT INTO not_duplicates (game_id_lo, game_id_hi) VALUES (?, ?)",
                     (min(a, b), max(a, b)))
    return conn


def test_definite_groups_exact_key_matches():
    conn = _games_conn([(1, "Brotato"), (2, "Brotato - Nintendo Switch 2 Edition"),
                        (3, "Hollow Knight")])
    result = find_duplicate_groups(conn)
    assert [sorted(g) for g in result["definite"]] == [[1, 2]]


def test_candidate_edition_match():
    conn = _games_conn([(1, "The Outer Worlds"),
                        (2, "The Outer Worlds: Spacer's Choice Edition")])
    cands = find_duplicate_groups(conn)["candidates"]
    assert len(cands) == 1
    assert {cands[0]["a"], cands[0]["b"]} == {1, 2}
    assert cands[0]["reason"] == "edition"


def test_candidate_containment_match():
    conn = _games_conn([(1, "Connection Haunted"),
                        (2, "/Connection Haunted <SERVER ERROR>")])
    cands = find_duplicate_groups(conn)["candidates"]
    assert len(cands) == 1
    assert cands[0]["reason"] == "contains"


def test_candidate_fuzzy_match():
    conn = _games_conn([(1, "Celeste"), (2, "Celest")])
    cands = find_duplicate_groups(conn)["candidates"]
    assert len(cands) == 1
    assert cands[0]["reason"] == "similar"


def test_siblings_are_not_definite():
    conn = _games_conn([(1, "Don't Starve"), (2, "Don't Starve Together")])
    result = find_duplicate_groups(conn)
    assert result["definite"] == []  # different keys -> never auto-merged


def test_dismissed_pair_excluded():
    conn = _games_conn([(1, "Connection Haunted"),
                        (2, "/Connection Haunted <SERVER ERROR>")],
                       dismissed=[(1, 2)])
    assert find_duplicate_groups(conn)["candidates"] == []


def test_merged_curation_takes_furthest_status_and_max_values():
    rows = [
        {"status": "playing", "rating": 3, "hours_played": 5.0, "priority": 5,
         "notes": "on PS4", "series_id": None, "series_order": None,
         "started_at": "2025-01-01", "completed_at": None, "sort_order": 12},
        {"status": "completed", "rating": 4, "hours_played": 20.0, "priority": 7,
         "notes": "100%'d on Switch", "series_id": 3, "series_order": 1,
         "started_at": "2025-02-01", "completed_at": "2025-03-01", "sort_order": None},
    ]
    merged = compute_merged_curation(rows)
    assert merged["status"] == "completed"
    assert merged["rating"] == 4
    assert merged["hours_played"] == 20.0
    assert merged["priority"] == 7
    assert merged["series_id"] == 3
    assert merged["started_at"] == "2025-01-01"   # earliest
    assert merged["completed_at"] == "2025-03-01"  # latest
    assert merged["sort_order"] == 12              # survivor (first row) wins
    assert "on PS4" in merged["notes"] and "100%'d on Switch" in merged["notes"]


def test_merged_curation_all_defaults():
    rows = [
        {"status": "backlog", "rating": None, "hours_played": 0, "priority": 5,
         "notes": None, "series_id": None, "series_order": None,
         "started_at": None, "completed_at": None, "sort_order": None},
        {"status": "backlog", "rating": None, "hours_played": 0, "priority": 5,
         "notes": None, "series_id": None, "series_order": None,
         "started_at": None, "completed_at": None, "sort_order": None},
    ]
    merged = compute_merged_curation(rows)
    assert merged["status"] == "backlog"
    assert merged["rating"] is None
    assert merged["notes"] is None


def _full_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT NOT NULL,
            normalized_title TEXT NOT NULL UNIQUE, cover_url TEXT,
            updated_at TIMESTAMP);
        CREATE TABLE platforms (id INTEGER PRIMARY KEY, short_name TEXT UNIQUE);
        CREATE TABLE game_platforms (game_id INTEGER, platform_id INTEGER,
            owned BOOLEAN DEFAULT 1, psprices_id TEXT,
            PRIMARY KEY (game_id, platform_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE);
        CREATE TABLE game_external_ids (game_id INTEGER, source TEXT, external_id TEXT,
            source_title TEXT, PRIMARY KEY (source, external_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
        CREATE TABLE game_tags (game_id INTEGER, tag_id INTEGER,
            PRIMARY KEY (game_id, tag_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE);
        CREATE TABLE user_ratings (game_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'backlog',
            rating INTEGER, notes TEXT, priority INTEGER DEFAULT 5,
            hours_played REAL DEFAULT 0, started_at DATE, completed_at DATE,
            sort_order INTEGER, series_id INTEGER, series_order INTEGER,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE);
        INSERT INTO platforms (id, short_name) VALUES (1, 'Switch'), (2, 'PS4');
    """)
    return conn


def _add_game(conn, gid, title, platform_ids=(), ext=None, rating=None):
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (?, ?, ?)",
                 (gid, title, normalize_title(title)))
    for pid in platform_ids:
        conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid))
    if ext:
        conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) VALUES (?, ?, ?)",
                     (gid, ext[0], ext[1]))
    r = rating or {}
    conn.execute("INSERT INTO user_ratings (game_id, status, rating, hours_played) "
                 "VALUES (?, ?, ?, ?)", (gid, r.get("status", "backlog"),
                                         r.get("rating"), r.get("hours_played", 0)))


def test_merge_moves_external_ids_platforms_and_deletes_drop():
    conn = _full_conn()
    _add_game(conn, 1, "Don't Starve", platform_ids=[1],
              rating={"status": "completed", "hours_played": 12})
    _add_game(conn, 2, "Don't Starve - Nintendo Switch Edition", platform_ids=[1],
              ext=("nintendo", "N1"))
    _add_game(conn, 3, "Don't Starve: Console Edition", platform_ids=[2],
              ext=("playstation", "P1"))

    merge_games(conn, survivor_id=1, drop_ids=[2, 3], title="Don't Starve")

    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    plats = {r[0] for r in conn.execute(
        "SELECT platform_id FROM game_platforms WHERE game_id = 1")}
    assert plats == {1, 2}
    exts = {(r["source"], r["external_id"]) for r in conn.execute(
        "SELECT source, external_id FROM game_external_ids WHERE game_id = 1")}
    assert exts == {("nintendo", "N1"), ("playstation", "P1")}
    surv = conn.execute("SELECT title, normalized_title FROM games WHERE id = 1").fetchone()
    assert surv["title"] == "Don't Starve"
    assert surv["normalized_title"] == "dont starve"
    ur = conn.execute("SELECT status, hours_played FROM user_ratings WHERE game_id = 1").fetchone()
    assert ur["status"] == "completed"
    assert ur["hours_played"] == 12


def test_merge_dry_run_writes_nothing():
    conn = _full_conn()
    _add_game(conn, 1, "Disco Elysium", platform_ids=[2], ext=("playstation", "P9"))
    _add_game(conn, 2, "Disco Elysium: The Final Cut", platform_ids=[2])
    plan = merge_games(conn, survivor_id=1, drop_ids=[2], title="Disco Elysium", dry_run=True)
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    assert plan["survivor_id"] == 1 and plan["drop_ids"] == [2]


def test_refresh_updates_keys_to_fresh_clean_title():
    conn = _full_conn()
    # stale normalized_title (old key with edition suffix still present)
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES "
                 "(1, 'Brotato', 'brotato nintendo switch 2 edition')")
    changed = refresh_normalized_titles(conn)
    assert conn.execute("SELECT normalized_title FROM games WHERE id = 1").fetchone()[0] == "brotato"
    assert {c["id"] for c in changed} == {1}


def test_refresh_dry_run_writes_nothing():
    conn = _full_conn()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES "
                 "(1, 'Brotato', 'stale')")
    refresh_normalized_titles(conn, dry_run=True)
    assert conn.execute("SELECT normalized_title FROM games WHERE id = 1").fetchone()[0] == "stale"


def _c(a, b, reason="similar", score=0.9):
    return {"a": a, "b": b, "reason": reason, "score": score}


def test_group_candidates_clusters_connected_pairs():
    # 1-2 and 2-3 are one family (transitive); 10-11 is a separate family.
    groups = group_candidates([_c(1, 2), _c(2, 3), _c(10, 11)])
    assert [g["members"] for g in groups] == [[1, 2, 3], [10, 11]]


def test_group_candidates_orders_members_and_pairs():
    groups = group_candidates([_c(5, 2)])
    assert groups[0]["members"] == [2, 5]
    assert groups[0]["pairs"] == [[2, 5]]


def test_group_candidates_sorts_by_size_descending():
    groups = group_candidates([_c(1, 2), _c(10, 11), _c(11, 12)])
    assert [len(g["members"]) for g in groups] == [3, 2]


def test_group_candidates_empty():
    assert group_candidates([]) == []
