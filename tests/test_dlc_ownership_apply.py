"""Direct tests for dlc_ownership._apply_addon_to_parent.

The helper is the inner per-addon block extracted from mark_ownership and is
reused by dlc_review.resolve to land a user-picked decision (forced parent,
forced dlc, or forced create).
"""
from __future__ import annotations

import sqlite3

import pytest

from dlc_ownership import OwnershipReport, _apply_addon_to_parent
from models import normalize_title


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            normalized_title TEXT,
            cover_url TEXT,
            igdb_id INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE dlc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            igdb_id INTEGER,
            kind TEXT DEFAULT 'dlc',
            owned INTEGER DEFAULT 0,
            source TEXT DEFAULT 'igdb',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_id, name),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE TABLE dlc_external_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dlc_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id),
            FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
    """)
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _add_dlc(conn, game_id, name, *, owned=0):
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, kind, owned, source) VALUES (?, ?, 'dlc', ?, 'igdb')",
        (game_id, name, owned))
    return cur.lastrowid


def test_reconciles_by_name_when_parent_is_forced(conn):
    gid = _add_game(conn, "The Witcher 3")
    dlc_id = _add_dlc(conn, gid, "Hearts of Stone")
    report = OwnershipReport()
    addon = {"title": "The Witcher 3 - Hearts of Stone", "source": "steam",
             "external_id": "378649", "source_title": "Hearts of Stone"}
    parent_norm = normalize_title("The Witcher 3")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "The Witcher 3"}, addon, dry_run=False)
    assert report.reconciled == 1
    owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (dlc_id,)).fetchone()[0]
    assert owned == 1
    ext = conn.execute(
        "SELECT dlc_id, external_id FROM dlc_external_ids WHERE source = 'steam'"
    ).fetchall()
    assert [(dlc_id, "378649")] == [(r[0], r[1]) for r in ext]


def test_creates_when_no_matching_dlc(conn):
    gid = _add_game(conn, "Some Game")
    report = OwnershipReport()
    addon = {"title": "Some Game - Season Pass", "source": "nintendo",
             "external_id": "70050000000003", "source_title": "Some Game - Season Pass"}
    parent_norm = normalize_title("Some Game")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Some Game"}, addon, dry_run=False)
    assert report.created == 1
    row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id = ?", (gid,)).fetchone()
    assert row["name"] == "Season Pass"
    assert row["owned"] == 1
    assert row["source"] == "nintendo"


def test_forced_dlc_id_flips_that_specific_row(conn):
    gid = _add_game(conn, "Game X")
    # Two ambiguously-equal rows (would have been ambiguous-dlc in the engine):
    a_id = _add_dlc(conn, gid, "DLC One")
    b_id = _add_dlc(conn, gid, "DLC One ")  # trailing space -> different name, same normalized
    report = OwnershipReport()
    addon = {"title": "Game X DLC One", "source": "xbox",
             "external_id": "BFR-1", "source_title": "DLC One"}
    parent_norm = normalize_title("Game X")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Game X"}, addon, dry_run=False, forced_dlc_id=b_id)
    assert report.reconciled == 1
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (b_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (a_id,)).fetchone()[0] == 0
    ext = conn.execute("SELECT dlc_id FROM dlc_external_ids WHERE external_id = 'BFR-1'").fetchone()
    assert ext[0] == b_id


def test_force_create_bypasses_reconcile(conn):
    gid = _add_game(conn, "Game Y")
    existing = _add_dlc(conn, gid, "Bonus")  # equality-reconcile target if not bypassed
    report = OwnershipReport()
    # Different name so the create branch doesn't collide with "Bonus":
    addon = {"title": "Game Y - Bonus Deluxe", "source": "playstation",
             "external_id": "EP1234-001", "source_title": "Bonus Deluxe"}
    parent_norm = normalize_title("Game Y")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Game Y"}, addon, dry_run=False, force_create=True)
    assert report.created == 1
    assert report.marked == 1                 # invariant: marked = created + reconciled
    assert report.reconciled == 0             # we explicitly bypassed reconcile
    # The pre-existing "Bonus" row stays at owned=0 (force_create did not flip it):
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (existing,)).fetchone()[0] == 0
    # A new "Bonus Deluxe" row exists, owned + sourced:
    new_row = conn.execute(
        "SELECT id, owned, source FROM dlc WHERE game_id = ? AND id != ?",
        (gid, existing)).fetchone()
    assert new_row is not None
    assert new_row["owned"] == 1
    assert new_row["source"] == "playstation"


def test_force_create_unique_collision_raises(conn):
    """force_create=True with a name that already exists is the user explicitly
    saying 'these existing rows are not a match, make a new one' — but the new
    row would collide with an existing UNIQUE(game_id, name). The function
    raises sqlite3.IntegrityError so the caller (dlc_review.resolve) can surface
    the collision to the UI rather than silently flipping the existing row.
    """
    gid = _add_game(conn, "Game Y")
    existing = _add_dlc(conn, gid, "Bonus")
    report = OwnershipReport()
    addon = {"title": "Game Y - Bonus", "source": "playstation",
             "external_id": "EP1234-001", "source_title": "Bonus"}
    parent_norm = normalize_title("Game Y")
    with pytest.raises(sqlite3.IntegrityError):
        _apply_addon_to_parent(conn, report, gid, parent_norm,
                               {gid: "Game Y"}, addon, dry_run=False, force_create=True)
    # The existing row is unchanged:
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (existing,)).fetchone()[0] == 0
    # No new row was inserted:
    n = conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (gid,)).fetchone()[0]
    assert n == 1


def test_dry_run_writes_nothing(conn):
    gid = _add_game(conn, "Game Z")
    report = OwnershipReport()
    addon = {"title": "Game Z - Extra", "source": "steam",
             "external_id": "999", "source_title": "Extra"}
    parent_norm = normalize_title("Game Z")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Game Z"}, addon, dry_run=True)
    assert report.created == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (gid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 0
