"""mark_ownership UPSERTs review items into dlc_review_queue."""
from __future__ import annotations

import sqlite3

import pytest

import models
from dlc_ownership import mark_ownership
from models import normalize_title


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    # Build the minimal schema needed by mark_ownership + dlc_review_queue.
    # We call the migrate_* helpers in dependency order (games must exist before
    # migrate_dlc, etc.).  The base games/dlc/dlc_external_ids tables are created
    # inline; then the dlc_review_queue migration is applied on top.
    c.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT    NOT NULL,
            normalized_title TEXT    NOT NULL,
            cover_url        TEXT,
            igdb_id          INTEGER,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dlc (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL,
            name       TEXT    NOT NULL,
            igdb_id    INTEGER,
            kind       TEXT    DEFAULT 'dlc',
            owned      INTEGER DEFAULT 0,
            source     TEXT    DEFAULT 'igdb',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (game_id, name),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS dlc_external_ids (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dlc_id       INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id),
            FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
    """)
    models.migrate_dlc_review_queue(c)
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _open_queue(conn):
    return conn.execute(
        "SELECT addon_title, source, external_id, reason, game_id "
        "FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL "
        "ORDER BY id"
    ).fetchall()


def test_no_parent_addon_persisted_with_reason(conn):
    addons = [{"title": "Unknown Title - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown Title - DLC"}]
    mark_ownership(conn, addons)
    rows = _open_queue(conn)
    assert len(rows) == 1
    assert rows[0]["addon_title"] == "Unknown Title - DLC"
    assert rows[0]["source"] == "nintendo"
    assert rows[0]["external_id"] == "70050000000003"
    assert rows[0]["reason"] == "no parent game"
    assert rows[0]["game_id"] is None


def test_ambiguous_parent_persisted(conn):
    # Two games whose normalized titles both prefix the addon at the same length:
    _add_game(conn, "Some Title")
    _add_game(conn, "Some Title")  # duplicate by name; both equal-length prefixes
    addons = [{"title": "Some Title DLC", "source": "xbox",
               "external_id": "BFR-1", "source_title": "Some Title DLC"}]
    mark_ownership(conn, addons)
    rows = _open_queue(conn)
    assert len(rows) == 1
    assert rows[0]["reason"] == "ambiguous parent"
    assert rows[0]["game_id"] is None


def test_ambiguous_dlc_persisted_with_game_id(conn):
    gid = _add_game(conn, "Game Q")
    conn.execute("INSERT INTO dlc (game_id, name) VALUES (?, 'Extra')", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name) VALUES (?, 'Extra ')", (gid,))  # trailing space → ties on normalize
    addons = [{"title": "Game Q Extra", "source": "steam",
               "external_id": "111", "source_title": "Extra"}]
    mark_ownership(conn, addons)
    rows = _open_queue(conn)
    assert len(rows) == 1
    assert rows[0]["reason"] == "ambiguous dlc"
    assert rows[0]["game_id"] == gid


def test_re_run_upserts_does_not_dupe(conn):
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    mark_ownership(conn, addons)
    mark_ownership(conn, addons)  # second run
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 1


def test_resolved_row_not_re_opened_by_rescrape(conn):
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    mark_ownership(conn, addons)
    conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP "
        "WHERE source = 'nintendo' AND external_id = '70050000000003'")
    mark_ownership(conn, addons)  # re-scrape
    rows = _open_queue(conn)
    assert rows == []  # still hidden from the open-queue view
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 1  # row is preserved, not duplicated


def test_existing_report_review_shape_unchanged(conn):
    """The in-memory OwnershipReport.review list must still contain the same
    items so the post-scrape inline UI keeps working."""
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    report = mark_ownership(conn, addons)
    assert len(report.review) == 1
    assert report.review[0].reason == "no parent game"
    assert report.review[0].addon_title == "Unknown - DLC"


def test_dry_run_does_not_persist(conn):
    """mark_ownership(..., dry_run=True) must not write to dlc_review_queue."""
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    mark_ownership(conn, addons, dry_run=True)
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 0
