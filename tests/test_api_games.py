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
