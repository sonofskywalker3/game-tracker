"""addon_parent: vendor-agnostic add-on -> parent-game resolution."""
from __future__ import annotations

import sqlite3

import pytest

import addon_parent
import models
from addon_parent import ParentRef
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE game_external_ids (
            game_id INTEGER NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
            source_title TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, external_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE TABLE dlc (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL, name TEXT NOT NULL,
            igdb_id INTEGER, kind TEXT DEFAULT 'dlc', owned INTEGER DEFAULT 0,
            source TEXT DEFAULT 'igdb', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_id, name), FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE TABLE dlc_external_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dlc_id INTEGER NOT NULL, source TEXT NOT NULL,
            external_id TEXT NOT NULL, source_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id), FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
        CREATE TABLE platforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, short_name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'modern_console'
        );
        CREATE TABLE game_platforms (
            game_id INTEGER NOT NULL, platform_id INTEGER NOT NULL, owned BOOLEAN DEFAULT 1,
            psprices_id TEXT, PRIMARY KEY (game_id, platform_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (platform_id) REFERENCES platforms(id) ON DELETE CASCADE
        );
        CREATE TABLE user_ratings (
            game_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'backlog', rating INTEGER,
            notes TEXT, priority INTEGER DEFAULT 5, hours_played REAL DEFAULT 0,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
    """)
    models.migrate_dlc_review_queue(c)
    c.commit()
    yield c
    c.close()


def _add_game(conn, title, *, source=None, ext=None):
    cur = conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                       (title, normalize_title(title)))
    gid = cur.lastrowid
    if source and ext:
        conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                     "VALUES (?, ?, ?)", (gid, source, ext))
    return gid


def test_ensure_parent_game_by_id(conn):
    gid = _add_game(conn, "Borderlands 4", source="xbox", ext="9MX6HKF5647G")
    pr = ParentRef(product_id="9MX6HKF5647G", name="Borderlands 4")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr)
    assert got == gid
    assert how == "id"  # matched existing by id


def test_ensure_parent_game_by_name_backfills_id(conn):
    gid = _add_game(conn, "Borderlands 4")  # exists, NO xbox id yet
    pr = ParentRef(product_id="9MX6HKF5647G", name="Borderlands 4")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr)
    assert got == gid          # matched the existing game by name
    assert how == "backfill"
    row = conn.execute("SELECT game_id FROM game_external_ids "
                       "WHERE source='xbox' AND external_id='9MX6HKF5647G'").fetchone()
    assert row["game_id"] == gid   # id backfilled onto the existing game


def test_ensure_parent_game_creates_when_missing(conn):
    pr = ParentRef(product_id="BNG8P3Q7C78Z", name="Rock Band 4")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr)
    assert got is not None
    assert how == "created"
    g = conn.execute("SELECT title FROM games WHERE id=?", (got,)).fetchone()
    assert g["title"] == "Rock Band 4"
    row = conn.execute("SELECT game_id FROM game_external_ids "
                       "WHERE source='xbox' AND external_id='BNG8P3Q7C78Z'").fetchone()
    assert row["game_id"] == got


def test_ensure_parent_game_create_missing_false_returns_none(conn):
    before = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    pr = ParentRef(product_id="UNKNOWNPID", name="Some Game")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr,
                                                create_missing=False)
    assert got is None
    assert how == ""
    after = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    assert after == before  # no game created


def test_ensure_parent_game_no_name_returns_none(conn):
    before = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    pr = ParentRef(product_id="UNKNOWNPID2", name=None)
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr,
                                                create_missing=True)
    assert got is None
    assert how == ""
    after = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    assert after == before  # no game created
