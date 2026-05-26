"""Tests for migrate_dlc_review_queue (mirrors test_external_ids_migration.py shape)."""
from __future__ import annotations

import sqlite3

import pytest

from models import migrate_dlc_review_queue


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")  # FK target
    yield c
    c.close()


def test_creates_table_and_columns(conn):
    migrate_dlc_review_queue(conn)
    cols = _columns(conn, "dlc_review_queue")
    assert set(cols) == {
        "id", "addon_title", "source", "external_id", "source_title",
        "reason", "game_id", "created_at", "resolved_at", "dismissed_at",
    }


def test_creates_indexes(conn):
    migrate_dlc_review_queue(conn)
    idx = _indexes(conn, "dlc_review_queue")
    assert "uq_dlc_review_vendor_id" in idx
    assert "idx_dlc_review_open" in idx


def test_is_idempotent(conn):
    migrate_dlc_review_queue(conn)
    migrate_dlc_review_queue(conn)  # must not raise


def test_partial_unique_blocks_vendor_id_dupes(conn):
    migrate_dlc_review_queue(conn)
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
        "VALUES ('A', 'nintendo', '70050000000003', 'no parent game')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
            "VALUES ('A2', 'nintendo', '70050000000003', 'no parent game')")


def test_partial_unique_allows_null_vendor_id_rows(conn):
    migrate_dlc_review_queue(conn)
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
        "VALUES ('A', NULL, NULL, 'no parent game')")
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
        "VALUES ('B', NULL, NULL, 'no parent game')")  # allowed
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 2


def test_fk_set_null_on_game_delete(conn):
    migrate_dlc_review_queue(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO games (id, title) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, reason, game_id) VALUES ('A', 'ambiguous dlc', 1)")
    conn.execute("DELETE FROM games WHERE id = 1")
    g = conn.execute("SELECT game_id FROM dlc_review_queue WHERE addon_title='A'").fetchone()[0]
    assert g is None
