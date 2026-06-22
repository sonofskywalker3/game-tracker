import barcode
import models


def test_registry_table_exists(temp_db):
    conn = models.get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(barcode_registry)")}
    conn.close()
    assert {"upc", "igdb_id", "title", "platform", "game_id", "confirmed_at"} <= cols


def test_migration_renames_old_cache_preserving_rows(tmp_path, monkeypatch):
    db = tmp_path / "rename.db"
    monkeypatch.setattr(models, "DB_PATH", db)
    conn = models.get_db()
    # Simulate a pre-rename DB with the OLD table holding a row.
    conn.execute(
        "CREATE TABLE barcode_cache (upc TEXT PRIMARY KEY, igdb_id INTEGER, "
        "title TEXT, platform TEXT, game_id INTEGER, confirmed_at TEXT)"
    )
    conn.execute("INSERT INTO barcode_cache (upc, title) VALUES ('111', 'Halo')")
    conn.commit()
    models.migrate_barcode_registry(conn)
    row = conn.execute("SELECT title FROM barcode_registry WHERE upc='111'").fetchone()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert row[0] == "Halo"
    assert "barcode_cache" not in tables


def test_registry_upcs_for_game_lists_per_platform(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (7, 'Z', 'z')")
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (99, 'Other', 'other')")
    barcode.registry_put(conn, "AAA", platform="Switch", game_id=7)
    barcode.registry_put(conn, "BBB", platform="PS5", game_id=7)
    barcode.registry_put(conn, "CCC", platform="Switch", game_id=99)  # other game
    conn.commit()
    rows = barcode.registry_upcs_for_game(conn, 7)
    conn.close()
    assert rows == [{"upc": "BBB", "platform": "PS5"}, {"upc": "AAA", "platform": "Switch"}]
