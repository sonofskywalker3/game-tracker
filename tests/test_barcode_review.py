"""Barcode approval-queue tests (Spec: barcode-approval-queue-design)."""
from __future__ import annotations

import sqlite3

from tests.helpers_multiuser import seed_barcode_review

# mu_db fixture comes from conftest's re-export (tests/conftest.py); importing
# it here too would shadow the fixture parameter name below and trip ruff's
# F811 (see tests/test_multiuser_isolation.py for the same convention).


def test_migration_creates_barcode_link_review(mu_db: sqlite3.Connection) -> None:
    cols = {c[1] for c in mu_db.execute(
        "PRAGMA table_info(barcode_link_review)").fetchall()}
    assert cols == {
        "id", "user_id", "upc", "platform", "igdb_id", "title",
        "cover_url", "game_id", "status", "created_at", "resolved_at",
    }
    # UNIQUE(user_id, upc): a second row for the same (user, upc) is rejected.
    mu_db.execute(
        "INSERT INTO barcode_link_review (user_id, upc, status) VALUES (1, 'U1', 'pending')")
    try:
        mu_db.execute(
            "INSERT INTO barcode_link_review (user_id, upc, status) VALUES (1, 'U1', 'pending')")
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "expected UNIQUE(user_id, upc) to reject the duplicate"


def test_seed_helper_inserts_pending_row(mu_db: sqlite3.Connection) -> None:
    rid = seed_barcode_review(mu_db, 2, upc="0123456789012", title="Halo")
    row = mu_db.execute(
        "SELECT user_id, upc, title, status FROM barcode_link_review WHERE id = ?",
        (rid,)).fetchone()
    assert (row["user_id"], row["upc"], row["title"], row["status"]) == (
        2, "0123456789012", "Halo", "pending")
