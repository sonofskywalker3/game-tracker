import sqlite3

from dedup import base_key, find_duplicate_groups, strip_edition_key


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
