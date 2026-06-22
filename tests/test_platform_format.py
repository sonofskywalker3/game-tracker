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
