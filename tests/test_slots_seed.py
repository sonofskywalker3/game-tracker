"""seed_default_slots inserts the four seed slots once, idempotently."""
import sqlite3

import pytest

from models import migrate_slots, seed_default_slots


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    migrate_slots(c)
    yield c
    c.close()


def test_seeds_four_slots(conn):
    seed_default_slots(conn)
    rows = conn.execute("SELECT label, platforms, requires_low_latency FROM slots ORDER BY sort_order").fetchall()
    labels = [r["label"] for r in rows]
    assert labels == ["Switch · Quick", "Switch · Long", "Garage · Console", "Long · Stream-safe"]


def test_seed_is_idempotent(conn):
    seed_default_slots(conn)
    seed_default_slots(conn)
    n = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    assert n == 4  # does not double-insert when slots already exist


def test_seed_skips_when_user_has_slots(conn):
    conn.execute("INSERT INTO slots (label, sort_order) VALUES ('Custom', 0)")
    seed_default_slots(conn)
    n = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    assert n == 1  # never clobbers existing user-defined slots
