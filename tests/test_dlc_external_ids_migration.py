import sqlite3

import pytest

from models import migrate_dlc_external_ids


def _conn_with_dlc():
    """A DB with a dlc table but no dlc_external_ids (pre-migration shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE dlc (id INTEGER PRIMARY KEY, name TEXT)")
    return conn


def test_migration_creates_table():
    conn = _conn_with_dlc()
    migrate_dlc_external_ids(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(dlc_external_ids)").fetchall()}
    assert cols == {"id", "dlc_id", "source", "external_id", "source_title", "created_at"}


def test_migration_is_idempotent():
    conn = _conn_with_dlc()
    migrate_dlc_external_ids(conn)
    migrate_dlc_external_ids(conn)  # second run must not raise
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(dlc_external_ids)").fetchall()}
    assert "idx_dlc_ext_dlc" in indexes


def test_source_external_id_is_unique():
    conn = _conn_with_dlc()
    migrate_dlc_external_ids(conn)
    conn.execute("INSERT INTO dlc (id, name) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO dlc_external_ids (dlc_id, source, external_id) VALUES (1, 'nintendo', 'N1')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dlc_external_ids (dlc_id, source, external_id) VALUES (1, 'nintendo', 'N1')")


def test_fk_to_dlc_is_enforced():
    conn = _conn_with_dlc()
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_dlc_external_ids(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dlc_external_ids (dlc_id, source, external_id) VALUES (999, 'xbox', 'X1')")
