"""dlc_review.resolve applies a user-picked decision to a queued review item."""
from __future__ import annotations

import sqlite3

import pytest

import dlc_review
import models
from dlc_ownership import mark_ownership
from models import normalize_title


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    # Minimal schema for the engine + review queue:
    c.executescript("""
        CREATE TABLE games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            normalized_title TEXT,
            cover_url TEXT,
            igdb_id INTEGER,
            user_id INTEGER NOT NULL DEFAULT 1,
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
    # The review queue migration (idempotent, schema-only) + the per-user column
    # the real schema carries (Task 9: dlc_review scoping defaults to owner=1).
    models.migrate_dlc_review_queue(c)
    c.execute("ALTER TABLE dlc_review_queue ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
    c.commit()
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _add_dlc(conn, game_id, name, *, owned=0):
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, kind, owned, source) "
        "VALUES (?, ?, 'dlc', ?, 'igdb')",
        (game_id, name, owned))
    return cur.lastrowid


def test_resolve_no_parent_with_picked_game(conn):
    # Queue a "no parent game" review item (addon's parent isn't in the library):
    mark_ownership(conn, [{"title": "Witcher 3 - HoS", "source": "steam",
                           "external_id": "378649", "source_title": "Witcher 3 - HoS"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    # Now the user adds the parent game and picks it:
    gid = _add_game(conn, "Witcher 3")
    match = dlc_review.resolve(conn, review_id, picked_game_id=gid)
    assert match.game_id == gid
    # The review row is marked resolved:
    row = conn.execute("SELECT resolved_at FROM dlc_review_queue WHERE id = ?",
                       (review_id,)).fetchone()
    assert row["resolved_at"] is not None
    # A new DLC row was created and marked owned:
    dlc_row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id = ?", (gid,)).fetchone()
    assert dlc_row["owned"] == 1
    assert dlc_row["source"] == "steam"


def test_resolve_ambiguous_dlc_with_picked_dlc_id(conn):
    gid = _add_game(conn, "Game Q")
    a = _add_dlc(conn, gid, "Extra")
    b = _add_dlc(conn, gid, "Extra ")  # trailing space -> ambiguous on normalize
    mark_ownership(conn, [{"title": "Game Q Extra", "source": "xbox",
                           "external_id": "X1", "source_title": "Extra"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    dlc_review.resolve(conn, review_id, picked_dlc_id=b)
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (b,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (a,)).fetchone()[0] == 0
    ext = conn.execute("SELECT dlc_id FROM dlc_external_ids WHERE external_id = 'X1'").fetchone()
    assert ext[0] == b


def test_resolve_ambiguous_dlc_with_create_new(conn):
    gid = _add_game(conn, "Game R")
    # These names normalize to "season pass plus" (ambiguous), but _clean_remainder
    # produces "Season Pass Plus" (no trailing punctuation), so force_create INSERT
    # won't collide with either pre-existing row:
    pre_a = _add_dlc(conn, gid, "Season Pass Plus!")
    pre_b = _add_dlc(conn, gid, "Season Pass Plus?")
    mark_ownership(conn, [{"title": "Game R Season Pass Plus", "source": "nintendo",
                           "external_id": "N1", "source_title": "Season Pass Plus"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    dlc_review.resolve(conn, review_id, create_new_dlc=True)
    # Neither pre-existing row got flipped:
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (pre_a,)).fetchone()[0] == 0
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (pre_b,)).fetchone()[0] == 0
    # A new row exists, owned + nintendo-sourced, ext id recorded:
    new_row = conn.execute(
        "SELECT id, owned, source FROM dlc WHERE game_id = ? AND id NOT IN (?, ?)",
        (gid, pre_a, pre_b)).fetchone()
    assert new_row is not None
    assert new_row["owned"] == 1
    assert new_row["source"] == "nintendo"
    ext = conn.execute("SELECT dlc_id FROM dlc_external_ids WHERE external_id = 'N1'").fetchone()
    assert ext[0] == new_row["id"]


def test_resolve_ambiguous_apply_leaves_row_open(conn):
    """A picked-game resolve whose apply only yields an 'ambiguous dlc' review
    outcome must NOT mark the row resolved or claim a false 'already owned'."""
    # Queue a "no parent game" review (parent not in the library yet):
    mark_ownership(conn, [{"title": "Game Z Extra", "source": "steam",
                           "external_id": "Z1", "source_title": "Extra"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    # The user adds the parent — which already has two DLC rows the add-on
    # matches ambiguously — and picks it:
    gid = _add_game(conn, "Game Z")
    a = _add_dlc(conn, gid, "Extra")
    b = _add_dlc(conn, gid, "Extra ")  # trailing space -> ambiguous on normalize
    match = dlc_review.resolve(conn, review_id, picked_game_id=gid)
    # The true outcome is reported (not a false "already owned" success):
    assert match.reason == "ambiguous dlc"
    assert match.dlc_id is None
    # The row stays open, refined to the ambiguous-dlc reason with the parent:
    row = conn.execute(
        "SELECT resolved_at, reason, game_id FROM dlc_review_queue WHERE id = ?",
        (review_id,)).fetchone()
    assert row["resolved_at"] is None
    assert row["reason"] == "ambiguous dlc"
    assert row["game_id"] == gid
    # Nothing was flipped or created:
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (a,)).fetchone()[0] == 0
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (b,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?",
                        (gid,)).fetchone()[0] == 2
    # The user can then finish the job with a specific pick:
    dlc_review.resolve(conn, review_id, picked_dlc_id=b)
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (b,)).fetchone()[0] == 1
    assert conn.execute("SELECT resolved_at FROM dlc_review_queue WHERE id = ?",
                        (review_id,)).fetchone()[0] is not None


def test_resolve_is_idempotent_on_already_resolved(conn):
    mark_ownership(conn, [{"title": "X - Y", "source": "steam",
                           "external_id": "9", "source_title": "X - Y"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    gid = _add_game(conn, "X")
    dlc_review.resolve(conn, review_id, picked_game_id=gid)
    # Second call must not raise and must not double-create:
    dlc_review.resolve(conn, review_id, picked_game_id=gid)
    n = conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (gid,)).fetchone()[0]
    assert n == 1


def test_resolve_with_missing_picked_game_raises(conn):
    mark_ownership(conn, [{"title": "A - B", "source": "steam",
                           "external_id": "8", "source_title": "A - B"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id, picked_game_id=999999)


def test_resolve_with_missing_picked_dlc_raises(conn):
    gid = _add_game(conn, "Game S")
    _add_dlc(conn, gid, "One")
    _add_dlc(conn, gid, "One ")
    mark_ownership(conn, [{"title": "Game S One", "source": "steam",
                           "external_id": "7", "source_title": "One"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id, picked_dlc_id=999999)


def test_resolve_requires_exactly_one_choice(conn):
    mark_ownership(conn, [{"title": "T - U", "source": "steam",
                           "external_id": "6", "source_title": "T - U"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id)  # nothing picked
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id, picked_game_id=1, picked_dlc_id=1)


def test_dismiss_marks_dismissed_at(conn):
    mark_ownership(conn, [{"title": "Some - Thing", "source": "steam",
                           "external_id": "55", "source_title": "Some - Thing"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    dlc_review.dismiss(conn, review_id)
    d = conn.execute("SELECT dismissed_at FROM dlc_review_queue WHERE id = ?",
                     (review_id,)).fetchone()[0]
    assert d is not None
    # Idempotent: second call doesn't update timestamp or raise:
    first = d
    dlc_review.dismiss(conn, review_id)
    d2 = conn.execute("SELECT dismissed_at FROM dlc_review_queue WHERE id = ?",
                      (review_id,)).fetchone()[0]
    assert d2 == first


def test_dismiss_missing_review_id_raises(conn):
    with pytest.raises(ValueError):
        dlc_review.dismiss(conn, 999999)


def test_resolve_on_dismissed_row_raises(conn):
    """A dismissed review row cannot be resolved — the user already said 'not a
    real add-on'. resolve must raise ValueError rather than silently un-dismiss."""
    mark_ownership(conn, [{"title": "Q - W", "source": "steam",
                           "external_id": "42", "source_title": "Q - W"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    dlc_review.dismiss(conn, review_id)
    gid = _add_game(conn, "Q")
    with pytest.raises(ValueError, match="dismissed"):
        dlc_review.resolve(conn, review_id, picked_game_id=gid)
