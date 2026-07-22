"""Reusable multi-user test infrastructure.

Built for Task 6 (scoping the games routes) and reused by Task 9's full
isolation sweep. Provides:

- ``mu_db``  : a temp-DB fixture (full schema + owner user 1 + a second user 2),
- ``client_as``: a Flask test client bound to a given user via the session,
- ``seed_game``: insert a game owned by a given user and return its id.

Auth is OFF in tests (no OAuth env), so ``_bind_user`` binds ``session["user_id"]``
unconditionally and ``identity.current_user_id()`` returns that id -- no OAuth
mocking is ever needed to act as a particular user.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

import app as app_module
import models
from models import normalize_title

# The owner is always user 1 (seeded by migrate_users). A second real user lets
# the isolation tests prove one user cannot see or mutate the other's data.
SECOND_USER_ID = 2
SECOND_USER_EMAIL = "second@localhost"


@pytest.fixture
def mu_db(tmp_path, monkeypatch) -> Iterator[sqlite3.Connection]:
    """Point the app at a throwaway DB with the FULL schema + two users.

    Mirrors ``tests/conftest.py::temp_db``: monkeypatch ``models.DB_PATH`` and
    build the schema exactly the way the app does on startup (``init_db`` +
    ``migrate_db``), so every migration -- including the users table and
    ``games.user_id`` -- is present without a hand-maintained schema list.
    ``migrate_users`` seeds the owner as user 1; this adds user 2. Yields an
    open connection to the temp DB (seed_game writes through it)."""
    db_path = tmp_path / "mu_games.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    models.init_db()
    models.migrate_db()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, is_owner) VALUES (?, ?, 0)",
        (SECOND_USER_ID, SECOND_USER_EMAIL),
    )
    conn.commit()

    app_module.app.config["TESTING"] = True
    try:
        yield conn
    finally:
        conn.close()


def client_as(user_id: int) -> FlaskClient:
    """A Flask test client whose session is bound to ``user_id``.

    Every request this client makes runs with ``current_user_id() == user_id``.
    Use with the ``mu_db`` fixture already active (it points the app at the temp
    DB)."""
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return client


def seed_game(conn: sqlite3.Connection, user_id: int, title: str) -> int:
    """Insert a game owned by ``user_id`` (+ a backlog rating) and return its id.

    Sets ``normalized_title`` via ``normalize_title`` so the per-user
    ``UNIQUE(user_id, normalized_title)`` constraint is respected, and commits so
    the app's own ``get_db()`` connection (a separate handle on the same file)
    sees the row."""
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title, user_id) VALUES (?, ?, ?)",
        (title, normalize_title(title), user_id),
    )
    game_id = cur.lastrowid
    conn.execute(
        "INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')",
        (game_id,),
    )
    conn.commit()
    return game_id


def set_status(conn: sqlite3.Connection, game_id: int, status: str) -> None:
    """Force a game's user_ratings status (e.g. 'playing') and commit."""
    conn.execute(
        "INSERT INTO user_ratings (game_id, status) VALUES (?, ?) "
        "ON CONFLICT(game_id) DO UPDATE SET status = excluded.status",
        (game_id, status),
    )
    conn.commit()


def seed_slot(conn: sqlite3.Connection, user_id: int, label: str,
              *, platforms: list[str] | None = None, sort_order: int = 0) -> int:
    """Insert a slot (a per-user root) owned by ``user_id`` and return its id."""
    cur = conn.execute(
        "INSERT INTO slots (label, sort_order, platforms, user_id) VALUES (?, ?, ?, ?)",
        (label, sort_order, json.dumps(platforms or []), user_id),
    )
    conn.commit()
    return cur.lastrowid


def seed_tag(conn: sqlite3.Connection, user_id: int, name: str,
             category: str = "custom") -> int:
    """Insert a tag (a per-user root) owned by ``user_id`` and return its id."""
    cur = conn.execute(
        "INSERT INTO tags (name, category, user_id) VALUES (?, ?, ?)",
        (name, category, user_id),
    )
    conn.commit()
    return cur.lastrowid


def seed_game_tag(conn: sqlite3.Connection, game_id: int, tag_id: int) -> None:
    """Link a game to a tag (the game_tags child m2m) and commit."""
    conn.execute(
        "INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)",
        (game_id, tag_id),
    )
    conn.commit()


def seed_collection_membership(conn: sqlite3.Connection, game_id: int,
                               collection_id: int, name: str) -> None:
    """Put a game into a (shared-catalog) collection via game_collections."""
    conn.execute(
        "INSERT OR IGNORE INTO collections (id, name, slug) VALUES (?, ?, ?)",
        (collection_id, name, name.lower().replace(" ", "-")),
    )
    conn.execute(
        "INSERT OR IGNORE INTO game_collections (game_id, collection_id) VALUES (?, ?)",
        (game_id, collection_id),
    )
    conn.commit()


def seed_decider_chat(conn: sqlite3.Connection, game_id: int, user_id: int,
                      *, slot_label: str = "Quick",
                      messages: list[dict] | None = None) -> int:
    """Insert a saved decider chat (decider_chats root) for a game/user."""
    payload = messages or [{"role": "user", "content": "what next"}]
    cur = conn.execute(
        "INSERT INTO decider_chats (game_id, slot_label, messages, user_id) "
        "VALUES (?, ?, ?, ?)",
        (game_id, slot_label, json.dumps(payload), user_id),
    )
    conn.commit()
    return cur.lastrowid


def seed_dlc(conn: sqlite3.Connection, game_id: int, name: str,
             *, owned: int = 0) -> int:
    """Insert a DLC row (child of games) and return its id."""
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, owned, source) VALUES (?, ?, ?, 'manual')",
        (game_id, name, owned),
    )
    conn.commit()
    return cur.lastrowid


def seed_profile(conn: sqlite3.Connection, user_id: int, *,
                 work_start_min: int | None = None) -> None:
    """Insert/replace this user's user_profile row (a per-user root)."""
    conn.execute(
        "INSERT INTO user_profile (user_id, work_start_min) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET work_start_min = excluded.work_start_min",
        (user_id, work_start_min),
    )
    conn.commit()
