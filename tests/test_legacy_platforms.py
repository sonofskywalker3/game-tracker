"""Tests for legacy-console platform seeding (migrate_seed_legacy_platforms).

Mirrors the migration-test shape in test_dlc_review_queue_migration.py.
"""
from __future__ import annotations

import sqlite3

import pytest

from models import (
    LEGACY_CONSOLE,
    LEGACY_PLATFORM_SEED,
    classify_platform,
    migrate_seed_legacy_platforms,
)

PLATFORMS_DDL = """
CREATE TABLE platforms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'modern_console'
)
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(PLATFORMS_DDL)
    yield c
    c.close()


def test_seed_constant_all_classify_legacy():
    # Every seeded short_name must round-trip through the era classifier as legacy,
    # so seeded rows can never disagree with classify_platform.
    for _name, short in LEGACY_PLATFORM_SEED:
        assert classify_platform(short) == LEGACY_CONSOLE, short


def test_seed_includes_3ds():
    shorts = {short for _name, short in LEGACY_PLATFORM_SEED}
    assert "3DS" in shorts


def test_migration_inserts_all_legacy_platforms(conn):
    migrate_seed_legacy_platforms(conn)
    rows = {
        r["short_name"]: r["category"]
        for r in conn.execute("SELECT short_name, category FROM platforms").fetchall()
    }
    for _name, short in LEGACY_PLATFORM_SEED:
        assert rows.get(short) == LEGACY_CONSOLE, short


def test_migration_is_idempotent(conn):
    migrate_seed_legacy_platforms(conn)
    migrate_seed_legacy_platforms(conn)  # must not raise or duplicate
    n = conn.execute("SELECT COUNT(*) FROM platforms").fetchone()[0]
    assert n == len(LEGACY_PLATFORM_SEED)


def test_migration_does_not_clobber_existing(conn):
    conn.execute(
        "INSERT INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        ("Nintendo Switch", "Switch", "modern_console"),
    )
    migrate_seed_legacy_platforms(conn)
    row = conn.execute(
        "SELECT category FROM platforms WHERE short_name = 'Switch'"
    ).fetchone()
    assert row["category"] == "modern_console"  # untouched
