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


def test_classify_platform_knows_mobile_and_subscription():
    assert models.classify_platform("iOS") == "mobile"
    assert models.classify_platform("Android") == "mobile"
    assert models.classify_platform("GamePass") == "subscription"
    assert models.classify_platform("PSPlus") == "subscription"
    assert models.classify_platform("PS5") == "modern_console"
    assert models.classify_platform("SNES") == "legacy_console"


def test_platform_category_migration_idempotent_for_extra(temp_db):
    conn = models.get_db()
    models.migrate_seed_extra_platforms(conn)   # seed mobile + subscription
    models.migrate_platform_category(conn)      # the re-derive that used to clobber
    cats = {r[0]: r[1] for r in conn.execute(
        "SELECT short_name, category FROM platforms").fetchall()}
    conn.close()
    assert cats["iOS"] == "mobile"
    assert cats["Android"] == "mobile"
    assert cats["GamePass"] == "subscription"
    assert cats["PSPlus"] == "subscription"


def _tag_physical(conn, game_id):
    conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES ('Physical','custom')")
    tid = conn.execute("SELECT id FROM tags WHERE name='Physical'").fetchone()[0]
    conn.execute("INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)", (game_id, tid))


def _clear_reconcile_flag(conn):
    """The temp_db fixture's migrate_db already stamps the one-time reconcile flag;
    clear it so these tests exercise a first-ever run."""
    conn.execute("DELETE FROM schema_flags WHERE name = ?",
                 (models.TAGGED_PHYSICAL_FLAG,))
    conn.commit()


def test_tagged_games_to_physical_flips_digital_and_null_for_tagged_only(temp_db):
    conn = models.get_db()
    _add_platform(conn, "Nintendo Switch", "Switch", "modern_console")
    _add_platform(conn, "PlayStation 5", "PS5", "modern_console")
    sid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]

    # game 1: tagged Physical, owned digital on Switch + NULL on PS5 -> both flip
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (1,'Kirby','kirby')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (1, ?, 'digital')", (sid,))
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (1, ?, NULL)", (pid,))
    _tag_physical(conn, 1)

    # game 2: tagged Physical, already 'both' on Switch -> must NOT downgrade
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (2,'Both','both')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (2, ?, 'both')", (sid,))
    _tag_physical(conn, 2)

    # game 3: NOT tagged, owned digital -> untouched (most of the library)
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (3,'Digital','digital')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (3, ?, 'digital')", (sid,))
    conn.commit()
    _clear_reconcile_flag(conn)

    models.migrate_tagged_games_to_physical(conn)

    fmts = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT gp.game_id, p.short_name, gp.format FROM game_platforms gp "
        "JOIN platforms p ON p.id = gp.platform_id").fetchall()}
    conn.close()
    assert fmts[(1, "Switch")] == "physical"   # digital -> physical
    assert fmts[(1, "PS5")] == "physical"       # NULL -> physical (all owned platforms)
    assert fmts[(2, "Switch")] == "both"        # never downgraded
    assert fmts[(3, "Switch")] == "digital"     # untagged, untouched


def test_tagged_games_to_physical_is_idempotent(temp_db):
    conn = models.get_db()
    _add_platform(conn, "Nintendo Switch", "Switch", "modern_console")
    sid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO games (id,title,normalized_title) VALUES (1,'K','k')")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) VALUES (1, ?, 'digital')", (sid,))
    _tag_physical(conn, 1)
    conn.commit()
    _clear_reconcile_flag(conn)

    models.migrate_tagged_games_to_physical(conn)
    models.migrate_tagged_games_to_physical(conn)  # second run = no-op (flag gate)
    fmt = conn.execute("SELECT format FROM game_platforms WHERE game_id=1").fetchone()[0]
    conn.close()
    assert fmt == "physical"
