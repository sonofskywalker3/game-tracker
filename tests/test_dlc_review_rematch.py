"""dlc_review.rematch_unresolved re-runs the full matcher over still-open review
rows (no re-scrape), clearing ones now resolvable by the Task-1 PSN title-id
fallback or any other path the matcher gained since they were queued."""
from __future__ import annotations

import sqlite3

import pytest

import dlc_review
import models
from models import normalize_title


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
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
        CREATE TABLE game_external_ids (
            game_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, external_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
    """)
    models.migrate_dlc_review_queue(c)
    c.commit()
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _add_game_ext(conn, game_id, source, external_id, source_title=None):
    conn.execute(
        "INSERT INTO game_external_ids (game_id, source, external_id, source_title) "
        "VALUES (?, ?, ?, ?)",
        (game_id, source, external_id, source_title))


def _seed_review(conn, addon_title, *, source, external_id, source_title=None,
                 reason="no parent game", game_id=None):
    cur = conn.execute(
        "INSERT INTO dlc_review_queue "
        "(addon_title, source, external_id, source_title, reason, game_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (addon_title, source, external_id, source_title or addon_title, reason, game_id))
    return cur.lastrowid


def test_psn_title_id_fallback_resolves_open_row(conn):
    """An open 'no parent game' PSN row whose name misses the game but whose
    title-id prefix matches a game_external_ids row is now resolved."""
    gid = _add_game(conn, "Like a Dragon")
    _add_game_ext(conn, gid, "playstation", "JP0177-PPSA24478_00-DELUXEEDITION000")
    review_id = _seed_review(
        conn, "Majima Outfit Pack", source="playstation",
        external_id="JP0177-PPSA24478_00-MAJIMAOUTFITPACK")

    report = dlc_review.rematch_unresolved(conn)

    assert report.resolved == 1
    assert report.marked == 1
    dlc_row = conn.execute(
        "SELECT owned, source FROM dlc WHERE game_id = ?", (gid,)).fetchone()
    assert dlc_row is not None
    assert dlc_row["owned"] == 1
    assert dlc_row["source"] == "playstation"
    row = conn.execute(
        "SELECT resolved_at FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone()
    assert row["resolved_at"] is not None


def test_psn_row_with_no_prefix_match_stays_open(conn):
    """A PSN 'no parent game' row whose title-id prefix matches nothing stays open."""
    gid = _add_game(conn, "Some Other Game")
    _add_game_ext(conn, gid, "playstation", "JP0177-PPSA99999_00-BASEGAME0000000")
    review_id = _seed_review(
        conn, "Orphan Pack", source="playstation",
        external_id="JP0177-PPSA24478_00-MAJIMAOUTFITPACK")

    report = dlc_review.rematch_unresolved(conn)

    assert report.resolved == 0
    row = conn.execute(
        "SELECT resolved_at FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone()
    assert row["resolved_at"] is None


def test_non_playstation_row_untouched(conn):
    """A non-PSN 'no parent game' row whose name still misses is left alone — the
    title-id fallback is source-guarded to playstation."""
    _add_game(conn, "Zelda")
    review_id = _seed_review(
        conn, "Mystery DLC", source="nintendo",
        external_id="JP0177-PPSA24478_00-MAJIMAOUTFITPACK")

    report = dlc_review.rematch_unresolved(conn)

    assert report.resolved == 0
    row = conn.execute(
        "SELECT resolved_at FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone()
    assert row["resolved_at"] is None


def test_idempotent_second_pass_no_duplicate(conn):
    """A second rematch pass after a successful one resolves nothing new and
    creates no duplicate DLC row."""
    gid = _add_game(conn, "Like a Dragon")
    _add_game_ext(conn, gid, "playstation", "JP0177-PPSA24478_00-DELUXEEDITION000")
    _seed_review(
        conn, "Majima Outfit Pack", source="playstation",
        external_id="JP0177-PPSA24478_00-MAJIMAOUTFITPACK")

    dlc_review.rematch_unresolved(conn)
    n_before = conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]

    report = dlc_review.rematch_unresolved(conn)

    assert report.resolved == 0
    n_after = conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]
    assert n_after == n_before


def test_name_rescue_runs_full_matcher(conn):
    """A row whose addon_title now name-prefix-matches an existing game is
    resolved too — proving the pass re-runs the full matcher, not just the PSN
    title-id path."""
    gid = _add_game(conn, "Hades")
    review_id = _seed_review(
        conn, "Hades Soundtrack", source="steam", external_id="STEAM-12345")

    report = dlc_review.rematch_unresolved(conn)

    assert report.resolved == 1
    dlc_row = conn.execute(
        "SELECT owned, source FROM dlc WHERE game_id = ?", (gid,)).fetchone()
    assert dlc_row is not None
    assert dlc_row["owned"] == 1
    row = conn.execute(
        "SELECT resolved_at FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone()
    assert row["resolved_at"] is not None
