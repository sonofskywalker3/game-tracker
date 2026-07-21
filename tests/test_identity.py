import sqlite3

import identity
import models


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    models.migrate_users(conn)
    return conn


def test_owner_seeded_as_user_1(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    conn = _db()
    row = conn.execute("SELECT id, email, is_owner FROM users WHERE id = 1").fetchone()
    assert row["is_owner"] == 1
    assert row["email"] == "owner@example.com"


def test_upsert_google_user_pins_sub_to_owner_by_email(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    conn = _db()
    uid = identity.upsert_google_user(conn, "sub-123", "owner@example.com", "Owner")
    assert uid == identity.OWNER_USER_ID          # owner claim pins sub to user #1
    again = identity.upsert_google_user(conn, "sub-123", "owner@example.com", "Owner")
    assert again == identity.OWNER_USER_ID          # idempotent


def test_upsert_creates_new_user_for_new_sub(monkeypatch):
    monkeypatch.setenv("BACKLOGQUEST_OWNER_EMAIL", "owner@example.com")
    conn = _db()
    uid = identity.upsert_google_user(conn, "sub-999", "tester@example.com", "Tester")
    assert uid != identity.OWNER_USER_ID
    assert conn.execute("SELECT google_sub FROM users WHERE id = ?", (uid,)).fetchone()["google_sub"] == "sub-999"
