import sqlite3

import pytest

from models import migrate_not_duplicates


def _conn_with_games():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    conn.executemany("INSERT INTO games (id, title) VALUES (?, ?)",
                     [(1, "A"), (2, "B")])
    return conn


def test_migration_creates_table():
    conn = _conn_with_games()
    migrate_not_duplicates(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(not_duplicates)").fetchall()}
    assert cols == {"game_id_lo", "game_id_hi", "created_at"}


def test_migration_is_idempotent():
    conn = _conn_with_games()
    migrate_not_duplicates(conn)
    migrate_not_duplicates(conn)  # must not raise
    conn.execute("INSERT INTO not_duplicates (game_id_lo, game_id_hi) VALUES (1, 2)")


def test_pair_is_unique():
    conn = _conn_with_games()
    migrate_not_duplicates(conn)
    conn.execute("INSERT INTO not_duplicates (game_id_lo, game_id_hi) VALUES (1, 2)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO not_duplicates (game_id_lo, game_id_hi) VALUES (1, 2)")


def test_fk_cascade_deletes_pair_when_game_deleted():
    conn = _conn_with_games()
    migrate_not_duplicates(conn)
    conn.execute("INSERT INTO not_duplicates (game_id_lo, game_id_hi) VALUES (1, 2)")
    conn.execute("DELETE FROM games WHERE id = 2")
    assert conn.execute("SELECT COUNT(*) FROM not_duplicates").fetchone()[0] == 0
