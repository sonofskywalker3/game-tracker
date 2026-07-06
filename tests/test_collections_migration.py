"""Collections layer schema: collections + game_collections m2m and the
games.original_release_ts column (original-release sort key)."""
import models


def test_collections_tables_exist(temp_db):
    conn = models.get_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "collections" in tables
    assert "game_collections" in tables
    game_cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert "original_release_ts" in game_cols
    conn.close()


def test_game_collections_membership_and_cascade(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'G', 'g')")
    conn.execute("INSERT INTO collections (id, name, slug) VALUES (39, 'Final Fantasy', 'final-fantasy')")
    conn.execute("INSERT INTO game_collections (game_id, collection_id) VALUES (1, 39)")
    conn.commit()
    # duplicate membership is a no-op, not an error
    conn.execute("INSERT OR IGNORE INTO game_collections (game_id, collection_id) VALUES (1, 39)")
    n = conn.execute("SELECT COUNT(*) FROM game_collections").fetchone()[0]
    assert n == 1
    # deleting the game removes its memberships
    conn.execute("DELETE FROM games WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM game_collections").fetchone()[0] == 0
    conn.close()


def test_migration_is_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_collections(conn)
    models.migrate_collections(conn)
    conn.close()
