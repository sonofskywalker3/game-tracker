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


def _addon(title, ext, source="xbox"):
    return {"title": title, "source": source, "external_id": ext, "source_title": title}


def _fake_resolver(mapping):
    """mapping: {addon_ext: ParentRef | None} -> a ParentResolver."""
    return lambda ids: {i: mapping.get(i) for i in ids}


def test_resolve_and_link_creates_parent_and_owns_addon(conn):
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 1
    assert rep.created_parents == 1
    d = conn.execute("SELECT d.owned, d.game_id FROM dlc d "
                     "JOIN games g ON g.id=d.game_id WHERE g.title='Borderlands 4'").fetchone()
    assert d["owned"] == 1
    assert conn.execute("SELECT 1 FROM dlc_external_ids "
                        "WHERE source='xbox' AND external_id='ADDON1'").fetchone()


def test_resolve_and_link_shared_parent_for_many_addons(conn):
    rb = ParentRef("RBPID", "Rock Band 4")
    addons = [_addon("Song A", "S1"), _addon("Song B", "S2"), _addon("Song C", "S3")]
    resolver = _fake_resolver({"S1": rb, "S2": rb, "S3": rb})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 3
    assert rep.created_parents == 1  # parent created once, not thrice
    n_games = conn.execute("SELECT COUNT(*) FROM games WHERE title='Rock Band 4'").fetchone()[0]
    assert n_games == 1
    n_dlc = conn.execute("SELECT COUNT(*) FROM dlc WHERE owned=1").fetchone()[0]
    assert n_dlc == 3


def test_resolve_and_link_backfills_existing_game(conn):
    gid = _add_game(conn, "Borderlands 4")  # exists, no xbox id
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 1
    assert rep.backfilled_ids == 1
    assert rep.created_parents == 0
    row = conn.execute("SELECT game_id FROM game_external_ids "
                       "WHERE source='xbox' AND external_id='PARENTPID'").fetchone()
    assert row["game_id"] == gid


def test_resolve_and_link_clears_open_review_row(conn):
    conn.execute("INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
                 "VALUES ('Gilded Glory Pack', 'xbox', 'ADDON1', 'no parent game')")
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.review_cleared == 1
    r = conn.execute("SELECT resolved_at FROM dlc_review_queue "
                     "WHERE source='xbox' AND external_id='ADDON1'").fetchone()
    assert r["resolved_at"] is not None


def test_resolve_and_link_unresolved_passes_through(conn):
    addons = [_addon("Mystery DLC", "M1")]
    resolver = _fake_resolver({"M1": None})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 0
    assert len(rep.unresolved) == 1
    assert rep.unresolved[0]["external_id"] == "M1"


def test_resolve_and_link_mixed_batch(conn):
    # One resolvable + one unresolved (resolver returns None) in a single call --
    # the realistic scrape shape: some add-ons map to a parent, some don't.
    addons = [_addon("Gilded Glory Pack", "ADDON1"), _addon("Mystery DLC", "M1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4"),
                               "M1": None})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 1
    assert len(rep.unresolved) == 1
    assert rep.unresolved[0]["external_id"] == "M1"


def test_resolve_and_link_addon_without_external_id(conn):
    # An add-on carrying no vendor id can't be resolved (resolver is never asked
    # for it); it must pass through to unresolved without erroring.
    addons = [{"title": "Mystery", "source": "xbox",
               "external_id": None, "source_title": "Mystery"}]
    resolver = _fake_resolver({})  # returns None for anything
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 0
    assert len(rep.unresolved) == 1
    assert rep.unresolved[0] is addons[0]


def test_resolve_and_link_ambiguous_dlc_leaves_review_open(conn):
    # Drive apply_addon_to_parent down its AMBIGUOUS-dlc branch: two existing dlc
    # rows whose names normalize to the same key as the add-on's remainder, so
    # match_equal returns AMBIGUOUS. The add-on must NOT be marked, and its open
    # review row must stay open (resolve_and_link only clears on marked/owned).
    gid = _add_game(conn, "Hades", source="xbox", ext="PARENTPID")
    # "Soundtrack" and "SOUNDTRACK!" are distinct raw names (so UNIQUE(game_id,
    # name) is satisfied) that both norm() to "soundtrack".
    conn.execute("INSERT INTO dlc (game_id, name, owned) VALUES (?, 'Soundtrack', 0)", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name, owned) VALUES (?, 'SOUNDTRACK!', 0)", (gid,))
    # "Hades Soundtrack" -> remainder "soundtrack" (parent norm "hades" stripped).
    addon = _addon("Hades Soundtrack", "ADDON_AMB")
    conn.execute("INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
                 "VALUES ('Hades Soundtrack', 'xbox', 'ADDON_AMB', 'no parent game')")
    resolver = _fake_resolver({"ADDON_AMB": ParentRef("PARENTPID", "Hades")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", [addon], resolver)
    assert rep.linked == 0
    assert rep.review_cleared == 0
    r = conn.execute("SELECT resolved_at FROM dlc_review_queue "
                     "WHERE source='xbox' AND external_id='ADDON_AMB'").fetchone()
    assert r["resolved_at"] is None  # ambiguity leaves the review open


def test_resolve_and_link_idempotent(conn):
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    n_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    n_dlc = conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]
    rep2 = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep2.linked == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == n_games
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == n_dlc
