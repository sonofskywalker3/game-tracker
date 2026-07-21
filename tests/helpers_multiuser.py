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
