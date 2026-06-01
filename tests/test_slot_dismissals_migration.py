"""migrate_slot_dismissals: composite-PK table, FK cascade, idempotent."""
import sqlite3

import pytest

from models import migrate_slot_dismissals


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    c.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, label TEXT)")
    yield c
    c.close()


def test_creates_table(conn):
    migrate_slot_dismissals(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(slot_dismissals)").fetchall()}
    assert cols == {"slot_id", "game_id", "created_at"}


def test_is_idempotent(conn):
    migrate_slot_dismissals(conn)
    migrate_slot_dismissals(conn)  # must not raise


def test_composite_pk_blocks_dupes(conn):
    migrate_slot_dismissals(conn)
    conn.execute("INSERT INTO slots (id, label) VALUES (1, 'S')")
    conn.execute("INSERT INTO games (id, title) VALUES (7, 'G')")
    conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (1, 7)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (1, 7)")


def test_fk_cascade_on_slot_delete(conn):
    migrate_slot_dismissals(conn)
    conn.execute("INSERT INTO slots (id, label) VALUES (1, 'S')")
    conn.execute("INSERT INTO games (id, title) VALUES (7, 'G')")
    conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (1, 7)")
    conn.execute("DELETE FROM slots WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals").fetchone()[0] == 0
