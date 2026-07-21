"""Owner-seeded identity accessors for the multi-user migration.

User #1 is always the app owner (single-tenant installs backfill to this id).
The owner's Google account is recognized by email (BACKLOGQUEST_OWNER_EMAIL)
and claims user #1 on first login; every other Google identity gets its own
new row.
"""
from __future__ import annotations

import os
import sqlite3

from flask import g, has_app_context

OWNER_USER_ID = 1


def owner_email() -> str:
    """Return the configured owner email (falls back to a local default)."""
    return os.environ.get("BACKLOGQUEST_OWNER_EMAIL", "owner@localhost").strip()


def user_for_sub(conn: sqlite3.Connection, sub: str) -> sqlite3.Row | None:
    """Look up a user row by Google subject id, or None if not registered."""
    return conn.execute("SELECT * FROM users WHERE google_sub = ?", (sub,)).fetchone()


def upsert_google_user(conn: sqlite3.Connection, sub: str, email: str, name: str | None) -> int:
    """Return the user id for a verified Google identity, creating the row if new.

    The owner (matched by BACKLOGQUEST_OWNER_EMAIL) claims user #1 on first login."""
    existing = user_for_sub(conn, sub)
    if existing:
        return existing["id"]
    if email.lower() == owner_email().lower():
        conn.execute(
            "UPDATE users SET google_sub = ?, display_name = ? WHERE id = ?",
            (sub, name, OWNER_USER_ID),
        )
        conn.commit()
        return OWNER_USER_ID
    cur = conn.execute(
        "INSERT INTO users (google_sub, email, display_name) VALUES (?, ?, ?)",
        (sub, email, name),
    )
    conn.commit()
    return cur.lastrowid


def set_request_user(user_id: int | None) -> None:
    g.acting_user_id = user_id


def current_user_id() -> int:
    """The user whose data this request may touch. Falls back to the owner when no
    user is bound (owner-only mode, local dev, the existing test suite) and when
    called outside any Flask app context (background threads, CLI)."""
    if not has_app_context():
        return OWNER_USER_ID
    uid = getattr(g, "acting_user_id", None)
    return uid if uid is not None else OWNER_USER_ID
