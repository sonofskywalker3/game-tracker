"""Tests for migrate_slots / migrate_slot_history (mirrors test_dlc_review_queue_migration.py)."""
from __future__ import annotations

import sqlite3

import pytest

from models import migrate_slots, migrate_slot_history


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")  # FK target
    yield c
    c.close()


def test_slots_creates_table_and_columns(conn):
    migrate_slots(conn)
    cols = _columns(conn, "slots")
    assert set(cols) == {
        "id", "label", "sort_order", "platforms", "max_session_minutes",
        "min_session_minutes", "requires_low_latency", "context_notes",
        "current_game_id", "goal",
    }


def test_slots_is_idempotent(conn):
    migrate_slots(conn)
    migrate_slots(conn)  # must not raise


def test_slots_current_game_fk_set_null_on_delete(conn):
    migrate_slots(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO games (id, title) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO slots (label, sort_order, current_game_id) VALUES ('S', 0, 1)")
    conn.execute("DELETE FROM games WHERE id = 1")
    g = conn.execute("SELECT current_game_id FROM slots WHERE label='S'").fetchone()[0]
    assert g is None


def test_history_creates_table_and_columns(conn):
    migrate_slot_history(conn)
    cols = _columns(conn, "slot_history")
    assert set(cols) == {
        "id", "slot_id", "game_id", "goal",
        "pinned_at", "removed_at", "outcome",
    }


def test_history_is_idempotent(conn):
    migrate_slot_history(conn)
    migrate_slot_history(conn)  # must not raise


def test_history_accepts_outcomes(conn):
    migrate_slot_history(conn)
    for outcome in ("beat", "completed", "dropped", "shelved"):
        conn.execute(
            "INSERT INTO slot_history (slot_id, game_id, outcome) VALUES (1, 1, ?)",
            (outcome,))
    n = conn.execute("SELECT COUNT(*) FROM slot_history").fetchone()[0]
    assert n == 4
