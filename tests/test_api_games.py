import models


def _ensure_platform(name, short, category):
    conn = models.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category),
    )
    conn.commit()
    conn.close()


def _insert_game(title, short_name, physical=False):
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, models.normalize_title(title)),
    )
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    pid = conn.execute(
        "SELECT id FROM platforms WHERE short_name = ?", (short_name,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid)
    )
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    if physical:
        conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical', 'custom')")
        tid = conn.execute("SELECT id FROM tags WHERE name = 'Physical'").fetchone()[0]
        conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tid))
    conn.commit()
    conn.close()
    return gid


def test_normalize_endpoint_is_display_only_and_collision_safe(client):
    # Two rows whose cleaned display titles collide. The endpoint must re-clean
    # the display title without recomputing normalized_title, so the
    # UNIQUE(normalized_title) constraint can't fire (no 500).
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [
            ("Fantasy Life i", "fantasy life i"),
            ("Fantasy Life i - Nintendo Switch 2 Edition",
             "fantasy life i nintendo switch 2 edition"),
        ],
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/games/normalize", json={})
    assert resp.status_code == 200
    assert resp.get_json()["cleaned_count"] == 1  # only the edition row's display changes

    conn = models.get_db()
    rows = {r["normalized_title"]: r["title"]
            for r in conn.execute("SELECT normalized_title, title FROM games")}
    conn.close()
    assert rows == {
        "fantasy life i": "Fantasy Life i",
        "fantasy life i nintendo switch 2 edition": "Fantasy Life i",
    }


def test_api_games_exposes_categories_and_physical(client):
    _ensure_platform("PlayStation 4", "PS4", "modern_console")
    _ensure_platform("PlayStation 3", "PS3", "legacy_console")
    # "PC" is seeded by init_db as category 'pc'.
    _insert_game("Modern Disc Game", "PS4", physical=True)
    _insert_game("Retro Game", "PS3")
    _insert_game("Desktop Game", "PC")

    rows = client.get("/api/games").get_json()
    by_title = {g["title"]: g for g in rows}

    assert by_title["Modern Disc Game"]["categories"] == ["modern_console"]
    assert by_title["Modern Disc Game"]["physical"] is True
    assert by_title["Retro Game"]["categories"] == ["legacy_console"]
    assert by_title["Retro Game"]["physical"] is False
    assert by_title["Desktop Game"]["categories"] == ["pc"]


def test_duplicates_endpoint_lists_definite_and_candidates(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Brotato", "brotato"),
         ("Brotato - Nintendo Switch 2 Edition", "brotato nsw2"),
         ("The Outer Worlds", "the outer worlds"),
         ("The Outer Worlds: Spacer's Choice Edition", "the outer worlds spacers choice edition")],
    )
    conn.commit()
    conn.close()

    body = client.get("/api/duplicates").get_json()
    keys = {g["id"]: g for g in body["games"]}
    assert any(sorted(group) for group in body["definite"])      # Brotato pair
    assert any(c["reason"] == "edition" for c in body["candidates"])
    # each referenced game is described for the modal
    some_id = body["definite"][0][0]
    assert "title" in keys[some_id] and "platforms" in keys[some_id]


def test_merge_endpoint_merges_and_refreshes(client):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Disco Elysium", "disco elysium"),
         ("Disco Elysium: The Final Cut", "disco elysium the final cut")],
    )
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    survivor = rows["Disco Elysium"]
    drop = rows["Disco Elysium: The Final Cut"]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (survivor,))
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (drop,))
    conn.commit()
    conn.close()

    resp = client.post("/api/games/merge", json={
        "survivor_id": survivor, "drop_ids": [drop], "title": "Disco Elysium"})
    assert resp.status_code == 200
    conn = models.get_db()
    titles = [r["title"] for r in conn.execute("SELECT title FROM games")]
    conn.close()
    assert titles == ["Disco Elysium"]


def test_dismiss_endpoint_records_not_duplicate(client):
    conn = models.get_db()
    conn.executemany("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                     [("Don't Starve", "dont starve"),
                      ("Don't Starve Together", "dont starve together")])
    rows = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM games")}
    a, b = rows["Don't Starve"], rows["Don't Starve Together"]
    conn.commit()
    conn.close()

    resp = client.post("/api/duplicates/dismiss", json={"game_id_a": a, "game_id_b": b})
    assert resp.status_code == 200
    conn = models.get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM not_duplicates WHERE game_id_lo = ? AND game_id_hi = ?",
                       (min(a, b), max(a, b))).fetchone()[0]
    conn.close()
    assert cnt == 1
