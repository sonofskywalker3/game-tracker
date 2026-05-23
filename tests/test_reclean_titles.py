"""Tests for the display-only title reclean migration (workstream 2, Part A).

Recomputes games.title with the current clean_title rules. Never touches
normalized_title — recomputing the match key and merging the duplicates it
surfaces is the dedup workstream.
"""
import sqlite3

from models import reclean_display_titles


def _games_conn(rows):
    """In-memory games table with the real UNIQUE(normalized_title) constraint."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE games ("
        " id INTEGER PRIMARY KEY,"
        " title TEXT NOT NULL,"
        " normalized_title TEXT NOT NULL UNIQUE,"
        " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.executemany(
        "INSERT INTO games (id, title, normalized_title) VALUES (?, ?, ?)", rows
    )
    return conn


def test_updates_display_title_but_never_normalized():
    # normalized_title is a deliberately stale value; reclean must leave it alone.
    conn = _games_conn([(1, "Bayonetta™", "STALE_KEY")])
    reclean_display_titles(conn)
    row = conn.execute("SELECT title, normalized_title FROM games WHERE id = 1").fetchone()
    assert row["title"] == "Bayonetta"
    assert row["normalized_title"] == "STALE_KEY"


def test_returns_only_changed_rows():
    conn = _games_conn([(1, "Bayonetta™", "a"), (2, "Hollow Knight", "b")])
    changes = reclean_display_titles(conn)
    assert changes == [{"id": 1, "original": "Bayonetta™", "cleaned": "Bayonetta"}]


def test_dry_run_writes_nothing():
    conn = _games_conn([(1, "Bayonetta™", "a")])
    changes = reclean_display_titles(conn, dry_run=True)
    assert changes == [{"id": 1, "original": "Bayonetta™", "cleaned": "Bayonetta"}]
    row = conn.execute("SELECT title FROM games WHERE id = 1").fetchone()
    assert row["title"] == "Bayonetta™"  # unchanged on disk


def test_idempotent():
    conn = _games_conn([(1, "Bayonetta™", "a")])
    reclean_display_titles(conn)
    assert reclean_display_titles(conn) == []


def test_collisions_cannot_raise_because_normalized_is_untouched():
    # Two rows whose cleaned display titles are identical. Because reclean leaves
    # normalized_title alone, UNIQUE(normalized_title) can't fire — both survive
    # as the duplicate pair the dedup workstream will merge.
    conn = _games_conn([
        (1, "Fantasy Life i", "fantasy life i"),
        (2, "Fantasy Life i - Nintendo Switch 2 Edition",
         "fantasy life i nintendo switch 2 edition"),
    ])
    reclean_display_titles(conn)  # must not raise sqlite3.IntegrityError
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM games")}
    assert titles == {1: "Fantasy Life i", 2: "Fantasy Life i"}
    norms = {r["normalized_title"] for r in conn.execute("SELECT normalized_title FROM games")}
    assert norms == {"fantasy life i", "fantasy life i nintendo switch 2 edition"}
