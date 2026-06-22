import models


def _add_platform(conn, name, short, category):
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category),
    )


def test_has_digital_market_seeded_by_category_and_overrides(temp_db):
    conn = models.get_db()
    _add_platform(conn, "PlayStation 5", "PS5", "modern_console")
    _add_platform(conn, "Super Nintendo", "SNES", "legacy_console")
    _add_platform(conn, "Nintendo 3DS", "3DS", "legacy_console")
    conn.commit()
    models.migrate_platform_digital_market(conn)
    got = dict(conn.execute(
        "SELECT short_name, has_digital_market FROM platforms").fetchall())
    conn.close()
    assert got["PS5"] == 1      # modern -> digital market
    assert got["SNES"] == 0     # pure cartridge legacy
    assert got["3DS"] == 1      # legacy-with-eShop override


def test_mobile_and_subscription_categories_seeded(temp_db):
    conn = models.get_db()
    models.migrate_seed_extra_platforms(conn)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT short_name, category FROM platforms").fetchall()}
    conn.close()
    assert rows.get("iOS") == "mobile"
    assert rows.get("Android") == "mobile"
    assert rows.get("GamePass") == "subscription"
    assert rows.get("PSPlus") == "subscription"


def test_seed_extra_platforms_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_seed_extra_platforms(conn)
    models.migrate_seed_extra_platforms(conn)  # second run = no error, no dupes
    n = conn.execute("SELECT COUNT(*) FROM platforms WHERE short_name='iOS'").fetchone()[0]
    conn.close()
    assert n == 1


def test_game_platform_format_column_and_backfill(temp_db):
    conn = models.get_db()
    _add_platform(conn, "PlayStation 5", "PS5", "modern_console")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'A','a')")
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (2,'B','b')")
    # Per-game 'physical' is a tag named 'Physical', not a column.
    conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical','custom')")
    tid = conn.execute("SELECT id FROM tags WHERE name='Physical'").fetchone()[0]
    conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (1, ?)", (tid,))
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (1, ?)", (pid,))
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (2, ?)", (pid,))
    conn.commit()
    models.migrate_game_platform_format(conn)
    fmts = dict(conn.execute("SELECT game_id, format FROM game_platforms").fetchall())
    conn.close()
    assert fmts[1] == "physical"
    assert fmts[2] == "digital"
