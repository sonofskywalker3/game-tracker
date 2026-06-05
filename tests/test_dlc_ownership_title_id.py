"""PSN parent resolution by PS-Store title-id prefix (source-guarded fallback).

PlayStation add-on names routinely omit the game name, so name-prefix matching
in `parent_of` fails. Every PS Store product id is `REGION-TITLEID_00-CONCEPT16`;
the title-id prefix `external_id.rsplit('-', 1)[0]` is shared by the base GAME's
id stored in game_external_ids (source='playstation'), and is unique per game.
"""
from __future__ import annotations

import sqlite3

import pytest

import models
from dlc_ownership import (
    parent_by_title_id,
    psn_prefix_map,
    title_id_prefix,
    mark_ownership,
)
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


def _add_game_ext(conn, game_id, external_id, *, source="playstation", source_title=None):
    conn.execute(
        "INSERT INTO game_external_ids (game_id, source, external_id, source_title) "
        "VALUES (?, ?, ?, ?)",
        (game_id, source, external_id, source_title))


# --- title_id_prefix -------------------------------------------------------

def test_title_id_prefix_strips_concept():
    assert title_id_prefix("JP0177-PPSA24478_00-MAJIMAOUTFITPACK") == "JP0177-PPSA24478_00"


def test_title_id_prefix_no_dash_is_none():
    assert title_id_prefix("NODASHHERE") is None


def test_title_id_prefix_none_is_none():
    assert title_id_prefix(None) is None


def test_title_id_prefix_leading_dash_is_none():
    # A leading-dash id splits to an empty prefix; falsy -> None, not "".
    assert title_id_prefix("-CONCEPT") is None


# --- parent_by_title_id ----------------------------------------------------

def test_parent_by_title_id_single_match():
    prefix_map = {"JP0177-PPSA24478_00": {7}}
    assert parent_by_title_id(prefix_map, "playstation",
                              "JP0177-PPSA24478_00-MAJIMAOUTFITPACK") == 7


def test_parent_by_title_id_wrong_source_is_none():
    prefix_map = {"JP0177-PPSA24478_00": {7}}
    assert parent_by_title_id(prefix_map, "xbox",
                              "JP0177-PPSA24478_00-MAJIMAOUTFITPACK") is None


def test_parent_by_title_id_unknown_prefix_is_none():
    prefix_map = {"JP0177-PPSA24478_00": {7}}
    assert parent_by_title_id(prefix_map, "playstation",
                              "US9999-PPSA99999_00-SOMETHING") is None


def test_parent_by_title_id_ambiguous_is_none():
    prefix_map = {"JP0177-PPSA24478_00": {7, 9}}
    assert parent_by_title_id(prefix_map, "playstation",
                              "JP0177-PPSA24478_00-MAJIMAOUTFITPACK") is None


# --- psn_prefix_map --------------------------------------------------------

def test_psn_prefix_map_builds_from_playstation_rows(conn):
    g = _add_game(conn, "Like a Dragon")
    _add_game_ext(conn, g, "JP0177-PPSA24478_00-DELUXEEDITION000")
    _add_game_ext(conn, g, "NODASH", source="playstation")  # skipped: no dash
    _add_game_ext(conn, g, "XBOX-THING-001", source="xbox")  # skipped: not psn
    pm = psn_prefix_map(conn)
    # NODASH has no '-' -> skipped; XBOX row excluded by source filter:
    assert pm == {"JP0177-PPSA24478_00": {g}}


# --- mark_ownership end-to-end --------------------------------------------

def test_mark_ownership_psn_title_id_happy_path(conn):
    g = _add_game(conn, "Like a Dragon: Infinite Wealth")
    _add_game_ext(conn, g, "JP0177-PPSA24478_00-DELUXEEDITION000")
    report = mark_ownership(conn, [{
        "title": "Legendary Outfit Pack PS4 & PS5",
        "source": "playstation",
        "external_id": "JP0177-PPSA24478_00-MAJIMAOUTFITPACK",
        "source_title": "Legendary Outfit Pack PS4 & PS5",
    }])
    assert report.marked == 1
    assert report.review == []
    row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id = ?", (g,)).fetchone()
    assert row is not None
    assert row["owned"] == 1
    assert row["source"] == "playstation"


def test_mark_ownership_psn_no_base_game_goes_to_review(conn):
    # A playstation add-on whose title-id prefix has no base game row:
    report = mark_ownership(conn, [{
        "title": "Legendary Outfit Pack PS4 & PS5",
        "source": "playstation",
        "external_id": "US9999-PPSA99999_00-SOMEADDON",
        "source_title": "Legendary Outfit Pack PS4 & PS5",
    }])
    assert report.marked == 0
    assert len(report.review) == 1
    assert report.review[0].reason == "no parent game"


def test_mark_ownership_non_playstation_source_guard(conn):
    # A base game with a matching-looking playstation prefix exists, but the
    # add-on is sourced from xbox -> source guard keeps it out of the fallback.
    g = _add_game(conn, "Like a Dragon: Infinite Wealth")
    _add_game_ext(conn, g, "JP0177-PPSA24478_00-DELUXEEDITION000")
    report = mark_ownership(conn, [{
        "title": "Legendary Outfit Pack",
        "source": "xbox",
        "external_id": "JP0177-PPSA24478_00-MAJIMAOUTFITPACK",
        "source_title": "Legendary Outfit Pack",
    }])
    assert report.marked == 0
    assert len(report.review) == 1
    assert report.review[0].reason == "no parent game"


def test_mark_ownership_ambiguous_name_rescued_by_title_id(conn):
    # TWO games with the same normalized title tie as name-prefixes for the
    # add-on (parent_of -> AMBIGUOUS). The PSN title-id prefix uniquely matches
    # ONE of them in game_external_ids, so the fallback resolves the tie.
    g1 = _add_game(conn, "Like a Dragon")
    g2 = _add_game(conn, "Like a Dragon")  # same normalized title -> ties g1
    _add_game_ext(conn, g1, "JP0177-PPSA24478_00-DELUXEEDITION000")  # only g1 has the prefix
    report = mark_ownership(conn, [{
        "title": "Like a Dragon Legendary Outfit Pack",
        "source": "playstation",
        "external_id": "JP0177-PPSA24478_00-MAJIMAOUTFITPACK",
        "source_title": "Like a Dragon Legendary Outfit Pack",
    }])
    assert report.marked == 1
    assert report.review == []
    row = conn.execute("SELECT owned, source FROM dlc WHERE game_id = ?", (g1,)).fetchone()
    assert row is not None and row["owned"] == 1 and row["source"] == "playstation"
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (g2,)).fetchone()[0] == 0


def test_mark_ownership_ambiguous_name_no_title_id_match_stays_ambiguous(conn):
    # Inverse regression: the name ties AMBIGUOUS, but the add-on's title-id
    # prefix matches NO base game -> the fallback can't break the tie, so it is
    # reported as "ambiguous parent" (NOT "no parent game").
    _add_game(conn, "Like a Dragon")
    _add_game(conn, "Like a Dragon")  # same normalized title -> ties
    report = mark_ownership(conn, [{
        "title": "Like a Dragon Legendary Outfit Pack",
        "source": "playstation",
        "external_id": "US9999-PPSA99999_00-MAJIMAOUTFITPACK",  # prefix unknown
        "source_title": "Like a Dragon Legendary Outfit Pack",
    }])
    assert report.marked == 0
    assert len(report.review) == 1
    assert report.review[0].reason == "ambiguous parent"
