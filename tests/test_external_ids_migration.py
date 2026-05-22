import sqlite3

import pytest

from models import migrate_external_ids


def _conn_without_table():
    """A DB with games but no game_external_ids (pre-migration shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    return conn


def test_migration_creates_table():
    conn = _conn_without_table()
    migrate_external_ids(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(game_external_ids)").fetchall()}
    assert cols == {"game_id", "source", "external_id", "source_title", "created_at"}


def test_migration_is_idempotent():
    conn = _conn_without_table()
    migrate_external_ids(conn)
    migrate_external_ids(conn)  # second run must not raise


def test_source_external_id_is_unique():
    conn = _conn_without_table()
    migrate_external_ids(conn)
    conn.execute("INSERT INTO games (id, title) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO game_external_ids (game_id, source, external_id) VALUES (1, 'playstation', 'C1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO game_external_ids (game_id, source, external_id) VALUES (1, 'playstation', 'C1')"
        )
