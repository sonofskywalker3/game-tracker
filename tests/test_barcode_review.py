"""Barcode approval-queue tests (Spec: barcode-approval-queue-design)."""
from __future__ import annotations

import sqlite3

import pytest

import barcode
from tests.helpers_multiuser import (
    app_ctx_as,
    client_as,
    seed_barcode_review,
    seed_game,
)

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


def test_queue_upsert_then_pending_for_user(mu_db: sqlite3.Connection) -> None:
    barcode.queue_upsert(mu_db, upc="U-A", user_id=2, platform="PS",
                         igdb_id=42, title="Halo", cover_url="c.jpg", game_id=7)
    row = barcode.pending_for_user(mu_db, "U-A", 2)
    assert row["title"] == "Halo" and row["igdb_id"] == 42 and row["platform"] == "PS"
    created_at = mu_db.execute(
        "SELECT created_at FROM barcode_link_review WHERE user_id=2 AND upc='U-A'"
    ).fetchone()[0]
    # A resubmission upserts the SAME row (UNIQUE(user_id, upc)), not a duplicate.
    barcode.queue_upsert(mu_db, upc="U-A", user_id=2, platform="XBOX", title="Halo 2")
    assert mu_db.execute(
        "SELECT COUNT(*) FROM barcode_link_review WHERE user_id=2 AND upc='U-A'"
    ).fetchone()[0] == 1
    row2 = barcode.pending_for_user(mu_db, "U-A", 2)
    assert row2["title"] == "Halo 2" and row2["platform"] == "XBOX"
    created_at2 = mu_db.execute(
        "SELECT created_at FROM barcode_link_review WHERE user_id=2 AND upc='U-A'"
    ).fetchone()[0]
    assert created_at2 == created_at  # created_at is preserved across resubmission


def test_pending_is_submitter_scoped(mu_db: sqlite3.Connection) -> None:
    barcode.queue_upsert(mu_db, upc="U-B", user_id=2, title="Only Mine")
    assert barcode.pending_for_user(mu_db, "U-B", 2) is not None
    assert barcode.pending_for_user(mu_db, "U-B", 1) is None  # owner doesn't see it


def test_resolve_uses_own_pending_but_not_others(
    mu_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    barcode.queue_upsert(mu_db, upc="U-C", user_id=2, platform="PS",
                         igdb_id=55, title="Provisional Game")
    # Neutralize the fall-through source chain so user 1's lookup (no cache, no
    # pending row) can't fall through to barcode._product_via_sources and make
    # live HTTP calls to UPCitemdb + Wikidata.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (lambda u: None,))
    with app_ctx_as(2):
        res = barcode.resolve(mu_db, "U-C", user_id=2)
    assert res["source"] == "provisional"
    assert res["candidates"][0]["title"] == "Provisional Game"
    assert res["candidates"][0]["igdb_id"] == 55
    # A different user has no registry row and no pending row -> not provisional.
    with app_ctx_as(1):
        other = barcode.resolve(mu_db, "U-C", user_id=1)
    assert other["source"] != "provisional"


def test_resolve_provisional_derives_ownership_for_submitter(mu_db: sqlite3.Connection) -> None:
    seed_game(mu_db, 2, "Owned Provisional", igdb_id=77)
    barcode.queue_upsert(mu_db, upc="U-D", user_id=2, igdb_id=77,
                         title="Owned Provisional", platform="PS")
    with app_ctx_as(2):
        res = barcode.resolve(mu_db, "U-D", user_id=2)
    assert res["candidates"][0]["owned_game_id"] is not None


def test_approve_writes_edited_identity_no_game_id(mu_db: sqlite3.Connection) -> None:
    barcode.queue_upsert(mu_db, upc="U-E", user_id=2, platform="PS",
                         igdb_id=10, title="Wrong Title", game_id=99)
    review_id = mu_db.execute(
        "SELECT id FROM barcode_link_review WHERE upc='U-E'").fetchone()[0]
    barcode.approve(mu_db, review_id, title="Correct Title")
    reg = barcode.registry_get(mu_db, "U-E")
    assert reg["title"] == "Correct Title"       # edited value won
    assert reg["igdb_id"] == 10                   # untouched field preserved
    assert reg["game_id"] is None                 # game_id NOT carried into registry
    row = mu_db.execute(
        "SELECT status, title, resolved_at FROM barcode_link_review WHERE id=?",
        (review_id,)).fetchone()
    assert row["status"] == "approved" and row["title"] == "Correct Title"
    assert row["resolved_at"] is not None


def test_approved_resolves_for_everyone(mu_db: sqlite3.Connection) -> None:
    barcode.queue_upsert(mu_db, upc="U-F", user_id=2, platform="PS",
                         igdb_id=11, title="Shared Now")
    review_id = mu_db.execute(
        "SELECT id FROM barcode_link_review WHERE upc='U-F'").fetchone()[0]
    barcode.approve(mu_db, review_id)
    with app_ctx_as(1):
        res = barcode.resolve(mu_db, "U-F", user_id=1)  # a non-submitter
    assert res["source"] == "cache"
    assert res["candidates"][0]["title"] == "Shared Now"


def test_reject_stops_provisional(mu_db: sqlite3.Connection) -> None:
    barcode.queue_upsert(mu_db, upc="U-G", user_id=2, title="Rejectme")
    review_id = mu_db.execute(
        "SELECT id FROM barcode_link_review WHERE upc='U-G'").fetchone()[0]
    barcode.reject(mu_db, review_id)
    assert mu_db.execute(
        "SELECT status FROM barcode_link_review WHERE id=?", (review_id,)
    ).fetchone()[0] == "rejected"
    assert barcode.pending_for_user(mu_db, "U-G", 2) is None  # no longer pending
    assert barcode.registry_get(mu_db, "U-G") is None          # no registry write


def test_approve_reject_not_found_and_not_pending(mu_db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        barcode.approve(mu_db, 999999)
    barcode.queue_upsert(mu_db, upc="U-H", user_id=2, title="X")
    rid = mu_db.execute("SELECT id FROM barcode_link_review WHERE upc='U-H'").fetchone()[0]
    barcode.reject(mu_db, rid)
    with pytest.raises(ValueError, match="not pending"):
        barcode.approve(mu_db, rid)          # already rejected
    with pytest.raises(ValueError, match="not pending"):
        barcode.reject(mu_db, rid)


def test_list_pending_only_pending(mu_db: sqlite3.Connection) -> None:
    barcode.queue_upsert(mu_db, upc="U-I", user_id=2, title="Pending One")
    barcode.queue_upsert(mu_db, upc="U-J", user_id=1, title="Pending Two")
    rid = mu_db.execute("SELECT id FROM barcode_link_review WHERE upc='U-J'").fetchone()[0]
    barcode.reject(mu_db, rid)
    items = barcode.list_pending(mu_db)
    upcs = {i["upc"] for i in items}
    assert upcs == {"U-I"}  # rejected U-J excluded


def test_tester_link_queues_not_registry(mu_db):
    client = client_as(2)  # non-owner
    r = client.post("/api/barcode/link",
                    json={"upc": "R-A", "platform": "PS", "title": "Queued", "igdb_id": 5})
    assert r.status_code == 200 and r.get_json()["queued"] is True
    assert mu_db.execute(
        "SELECT COUNT(*) FROM barcode_link_review WHERE upc='R-A' AND user_id=2"
    ).fetchone()[0] == 1
    assert barcode.registry_get(mu_db, "R-A") is None  # nothing shared


def test_owner_link_writes_registry_directly(mu_db):
    client = client_as(1)  # owner
    r = client.post("/api/barcode/link",
                    json={"upc": "R-B", "platform": "PS", "title": "Trusted"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("queued") is not True
    assert barcode.registry_get(mu_db, "R-B")["title"] == "Trusted"
    assert mu_db.execute(
        "SELECT COUNT(*) FROM barcode_link_review WHERE upc='R-B'").fetchone()[0] == 0


def test_review_list_owner_only(mu_db):
    seed_barcode_review(mu_db, 2, upc="R-C", title="Pending")
    assert client_as(2).get("/api/barcode/review").status_code == 403
    owner_res = client_as(1).get("/api/barcode/review")
    assert owner_res.status_code == 200
    assert {i["upc"] for i in owner_res.get_json()["items"]} == {"R-C"}


def test_review_approve_reject_owner_only(mu_db):
    rid = seed_barcode_review(mu_db, 2, upc="R-D", title="Wrong", igdb_id=3)
    assert client_as(2).post(f"/api/barcode/review/{rid}/approve", json={}).status_code == 403
    assert client_as(2).post(f"/api/barcode/review/{rid}/reject", json={}).status_code == 403


def test_route_approve_with_edit(mu_db):
    rid = seed_barcode_review(mu_db, 2, upc="R-E", title="Wrong", igdb_id=3, game_id=88)
    r = client_as(1).post(f"/api/barcode/review/{rid}/approve",
                          json={"title": "Right"})
    assert r.status_code == 200
    reg = barcode.registry_get(mu_db, "R-E")
    assert reg["title"] == "Right" and reg["game_id"] is None


def test_route_reject(mu_db):
    rid = seed_barcode_review(mu_db, 2, upc="R-F", title="Nope")
    assert client_as(1).post(f"/api/barcode/review/{rid}/reject", json={}).status_code == 200
    assert mu_db.execute(
        "SELECT status FROM barcode_link_review WHERE id=?", (rid,)
    ).fetchone()[0] == "rejected"


def test_route_approve_404_and_409(mu_db):
    assert client_as(1).post("/api/barcode/review/999999/approve", json={}).status_code == 404
    rid = seed_barcode_review(mu_db, 2, upc="R-G", status="rejected")
    assert client_as(1).post(f"/api/barcode/review/{rid}/approve", json={}).status_code == 409
