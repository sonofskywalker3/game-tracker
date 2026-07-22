# Barcode Approval Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let beta testers submit barcode→game links that resolve immediately for themselves (provisionally) but stay out of the shared registry until the owner approves them via an owner-only review queue with edit-before-approve.

**Architecture:** A new per-user `barcode_link_review` table mirrors the existing dlc/bundle review queues. `POST /api/barcode/link` branches on `identity.is_owner()`: owners write the shared `barcode_registry` directly (unchanged); testers enqueue a `pending` row. `barcode.resolve` gains one precedence step — the caller's own pending row resolves provisionally between the shared-registry hit and the IGDB path. Three owner-only endpoints (list/approve/reject) curate the queue; approve writes the (optionally edited) identity into the shared registry. An owner review section in `settings.html` mirrors the existing bundle-review UI.

**Tech Stack:** Python 3 / Flask / SQLite (`models.py` migrations, `barcode.py` data layer, `app.py` routes), Jinja/vanilla-JS templates, pytest.

**Spec:** `docs/superpowers/specs/2026-07-22-barcode-approval-queue-design.md`

## Global Constraints

- Work on `main` — no branches, no PRs, no `git stash`.
- Run tests with `uv run python -m pytest` (plain `uv run pytest` FAILS in this project).
- Lint with `ruff check` ONLY — NEVER `ruff format`.
- Subagents use temp/in-memory DBs ONLY — NEVER touch the real `games.db` or the running app.
- Type hints on all function signatures (params and return).
- `logging` module (`app.logger` / module logger), never `print()`, for operational output.
- Secrets via `os.environ` only.
- Registry rows carry IDENTITY only (`title`/`igdb_id`/`platform`/`cover_url`). `game_id` is NEVER written into `barcode_registry` on approve; per-user ownership stays derived via `barcode._owned_game_id`.
- Reject is plain (v1): no permanent "don't ask again" memory; a rejected UPC is resubmittable.

---

### Task 1: Migration + `barcode_link_review` table + test seed helper

**Files:**
- Modify: `models.py` (add `migrate_barcode_link_review`; register it in `migrate_db()` right after `migrate_barcode_registry_drop_owned(conn)` at ~models.py:1698)
- Modify: `tests/helpers_multiuser.py` (add `seed_barcode_review`)
- Test: `tests/test_barcode_review.py` (new)

**Interfaces:**
- Produces: `models.migrate_barcode_link_review(conn: sqlite3.Connection) -> None`; a `barcode_link_review` table with columns `id, user_id, upc, platform, igdb_id, title, cover_url, game_id, status, created_at, resolved_at`, `UNIQUE(user_id, upc)`, index `idx_barcode_link_review_status`.
- Produces: `tests.helpers_multiuser.seed_barcode_review(conn, user_id, *, upc, platform, igdb_id, title, cover_url, game_id, status) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_barcode_review.py`:

```python
"""Barcode approval-queue tests (Spec: barcode-approval-queue-design)."""
from __future__ import annotations

import sqlite3

from tests.helpers_multiuser import (  # noqa: F401  (fixtures via conftest re-export)
    mu_db, seed_barcode_review,
)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode_review.py -v`
Expected: FAIL — `no such table: barcode_link_review` (and `seed_barcode_review` import error).

- [ ] **Step 3: Add the migration to `models.py`**

Add this function near the other barcode migrations (e.g. after `migrate_barcode_registry_drop_owned`, ~models.py:1420):

```python
def migrate_barcode_link_review(conn: sqlite3.Connection) -> None:
    """Per-user queue of tester barcode links awaiting owner approval.

    A non-owner POST /api/barcode/link enqueues a pending row here (provisional
    for that submitter only) instead of writing the shared barcode_registry; the
    owner approves it into the registry or rejects it. Fresh CREATE with user_id
    inline (no ADD COLUMN, so no FK-off/on caution needed). Idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barcode_link_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            upc TEXT NOT NULL,
            platform TEXT,
            igdb_id INTEGER,
            title TEXT,
            cover_url TEXT,
            game_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT,
            UNIQUE(user_id, upc)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_barcode_link_review_status "
        "ON barcode_link_review(status)")
    conn.commit()
```

Register it in `migrate_db()` immediately after `migrate_barcode_registry_drop_owned(conn)` (~models.py:1698). `migrate_users` (which the FK references) runs first at the top of `migrate_db()`, so the FK target exists:

```python
    migrate_barcode_registry_drop_owned(conn)
    # Per-user tester barcode-link approval queue (FK -> users, created first).
    migrate_barcode_link_review(conn)
```

- [ ] **Step 4: Add the `seed_barcode_review` helper to `tests/helpers_multiuser.py`**

Add after `seed_registry` (~helpers_multiuser.py:207):

```python
def seed_barcode_review(conn: sqlite3.Connection, user_id: int, *,
                        upc: str = "0123456789012", platform: str | None = "PS",
                        igdb_id: int | None = None, title: str | None = "Some Game",
                        cover_url: str | None = None, game_id: int | None = None,
                        status: str = "pending") -> int:
    """Insert a barcode_link_review row for ``user_id`` and return its id.

    Mirrors the tester-submitted queue row. ``game_id`` is the submitter's own
    proposed library row (informational; never written to the shared registry)."""
    cur = conn.execute(
        "INSERT INTO barcode_link_review "
        "(user_id, upc, platform, igdb_id, title, cover_url, game_id, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, upc, platform, igdb_id, title, cover_url, game_id, status),
    )
    conn.commit()
    return cur.lastrowid
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode_review.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Lint**

Run: `ruff check models.py tests/helpers_multiuser.py tests/test_barcode_review.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add models.py tests/helpers_multiuser.py tests/test_barcode_review.py
git commit -m "feat(barcode): add barcode_link_review table + migration"
```

---

### Task 2: `barcode.py` queue helpers + provisional resolve precedence

**Files:**
- Modify: `barcode.py` (add `queue_upsert`, `pending_for_user`, `list_pending`, `approve`, `reject`; insert a provisional step in `resolve` at ~barcode.py:482)
- Test: `tests/test_barcode_review.py` (extend)

**Interfaces:**
- Consumes: `barcode.registry_put` (barcode.py:334), `barcode.registry_get` (barcode.py:324), `barcode._owned_game_id`, `barcode.owned_platforms_for`, `identity.OWNER_USER_ID`.
- Produces:
  - `barcode.queue_upsert(conn, *, upc: str, user_id: int, platform: str | None = None, igdb_id: int | None = None, title: str | None = None, cover_url: str | None = None, game_id: int | None = None) -> None`
  - `barcode.pending_for_user(conn, upc: str, user_id: int) -> dict | None`
  - `barcode.list_pending(conn) -> list[dict]`
  - `barcode.approve(conn, review_id: int, *, title: str | None = None, igdb_id: int | None = None, platform: str | None = None, cover_url: str | None = None) -> None` (raises `ValueError("review not found")` / `ValueError("review not pending")`)
  - `barcode.reject(conn, review_id: int) -> None` (same `ValueError`s)
  - `resolve(...)` returns a candidate with `source == "provisional"` when the caller's own pending row supplies identity.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_barcode_review.py`:

```python
import barcode
from tests.helpers_multiuser import app_ctx_as, seed_game


def test_queue_upsert_then_pending_for_user(mu_db):
    barcode.queue_upsert(mu_db, upc="U-A", user_id=2, platform="PS",
                         igdb_id=42, title="Halo", cover_url="c.jpg", game_id=7)
    row = barcode.pending_for_user(mu_db, "U-A", 2)
    assert row["title"] == "Halo" and row["igdb_id"] == 42 and row["platform"] == "PS"
    # A resubmission upserts the SAME row (UNIQUE(user_id, upc)), not a duplicate.
    barcode.queue_upsert(mu_db, upc="U-A", user_id=2, platform="XBOX", title="Halo 2")
    assert mu_db.execute(
        "SELECT COUNT(*) FROM barcode_link_review WHERE user_id=2 AND upc='U-A'"
    ).fetchone()[0] == 1
    row2 = barcode.pending_for_user(mu_db, "U-A", 2)
    assert row2["title"] == "Halo 2" and row2["platform"] == "XBOX"


def test_pending_is_submitter_scoped(mu_db):
    barcode.queue_upsert(mu_db, upc="U-B", user_id=2, title="Only Mine")
    assert barcode.pending_for_user(mu_db, "U-B", 2) is not None
    assert barcode.pending_for_user(mu_db, "U-B", 1) is None  # owner doesn't see it


def test_resolve_uses_own_pending_but_not_others(mu_db):
    barcode.queue_upsert(mu_db, upc="U-C", user_id=2, platform="PS",
                         igdb_id=55, title="Provisional Game")
    with app_ctx_as(2):
        res = barcode.resolve(mu_db, "U-C", user_id=2)
    assert res["source"] == "provisional"
    assert res["candidates"][0]["title"] == "Provisional Game"
    assert res["candidates"][0]["igdb_id"] == 55
    # A different user has no registry row and no pending row -> not provisional.
    with app_ctx_as(1):
        other = barcode.resolve(mu_db, "U-C", user_id=1)
    assert other["source"] != "provisional"


def test_resolve_provisional_derives_ownership_for_submitter(mu_db):
    seed_game(mu_db, 2, "Owned Provisional", igdb_id=77)
    barcode.queue_upsert(mu_db, upc="U-D", user_id=2, igdb_id=77,
                         title="Owned Provisional", platform="PS")
    with app_ctx_as(2):
        res = barcode.resolve(mu_db, "U-D", user_id=2)
    assert res["candidates"][0]["owned_game_id"] is not None


def test_approve_writes_edited_identity_no_game_id(mu_db):
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


def test_approved_resolves_for_everyone(mu_db):
    barcode.queue_upsert(mu_db, upc="U-F", user_id=2, platform="PS",
                         igdb_id=11, title="Shared Now")
    review_id = mu_db.execute(
        "SELECT id FROM barcode_link_review WHERE upc='U-F'").fetchone()[0]
    barcode.approve(mu_db, review_id)
    with app_ctx_as(1):
        res = barcode.resolve(mu_db, "U-F", user_id=1)  # a non-submitter
    assert res["source"] == "cache"
    assert res["candidates"][0]["title"] == "Shared Now"


def test_reject_stops_provisional(mu_db):
    barcode.queue_upsert(mu_db, upc="U-G", user_id=2, title="Rejectme")
    review_id = mu_db.execute(
        "SELECT id FROM barcode_link_review WHERE upc='U-G'").fetchone()[0]
    barcode.reject(mu_db, review_id)
    assert mu_db.execute(
        "SELECT status FROM barcode_link_review WHERE id=?", (review_id,)
    ).fetchone()[0] == "rejected"
    assert barcode.pending_for_user(mu_db, "U-G", 2) is None  # no longer pending
    assert barcode.registry_get(mu_db, "U-G") is None          # no registry write


def test_approve_reject_not_found_and_not_pending(mu_db):
    import pytest
    with pytest.raises(ValueError, match="not found"):
        barcode.approve(mu_db, 999999)
    barcode.queue_upsert(mu_db, upc="U-H", user_id=2, title="X")
    rid = mu_db.execute("SELECT id FROM barcode_link_review WHERE upc='U-H'").fetchone()[0]
    barcode.reject(mu_db, rid)
    with pytest.raises(ValueError, match="not pending"):
        barcode.approve(mu_db, rid)          # already rejected
    with pytest.raises(ValueError, match="not pending"):
        barcode.reject(mu_db, rid)


def test_list_pending_only_pending(mu_db):
    barcode.queue_upsert(mu_db, upc="U-I", user_id=2, title="Pending One")
    barcode.queue_upsert(mu_db, upc="U-J", user_id=1, title="Pending Two")
    rid = mu_db.execute("SELECT id FROM barcode_link_review WHERE upc='U-J'").fetchone()[0]
    barcode.reject(mu_db, rid)
    items = barcode.list_pending(mu_db)
    upcs = {i["upc"] for i in items}
    assert upcs == {"U-I"}  # rejected U-J excluded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_barcode_review.py -v`
Expected: FAIL — `module 'barcode' has no attribute 'queue_upsert'` etc.

- [ ] **Step 3: Add the queue helpers to `barcode.py`**

Add near `registry_put` (after ~barcode.py:357). `registry_put` and `registry_get` are already defined above; `_owned_game_id` and `owned_platforms_for` exist in this module:

```python
def queue_upsert(conn: sqlite3.Connection, *, upc: str, user_id: int,
                 platform: str | None = None, igdb_id: int | None = None,
                 title: str | None = None, cover_url: str | None = None,
                 game_id: int | None = None) -> None:
    """Enqueue (or refresh) a tester's pending barcode link.

    ON CONFLICT(user_id, upc) a resubmission refreshes the proposed identity,
    re-sets status to 'pending', clears resolved_at, and PRESERVES created_at."""
    conn.execute(
        "INSERT INTO barcode_link_review "
        "(user_id, upc, platform, igdb_id, title, cover_url, game_id, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now')) "
        "ON CONFLICT(user_id, upc) DO UPDATE SET "
        "platform=excluded.platform, igdb_id=excluded.igdb_id, "
        "title=excluded.title, cover_url=excluded.cover_url, "
        "game_id=excluded.game_id, status='pending', resolved_at=NULL",
        (user_id, upc, platform, igdb_id, title, cover_url, game_id),
    )


def pending_for_user(conn: sqlite3.Connection, upc: str,
                     user_id: int) -> dict | None:
    """The caller's OWN pending review row for this UPC, or None. Submitter-scoped."""
    row = conn.execute(
        "SELECT id, upc, igdb_id, title, platform, cover_url, game_id "
        "FROM barcode_link_review "
        "WHERE user_id = ? AND upc = ? AND status = 'pending'",
        (user_id, upc),
    ).fetchone()
    return dict(row) if row else None


def list_pending(conn: sqlite3.Connection) -> list[dict]:
    """All pending review rows across submitters (owner review list)."""
    rows = conn.execute(
        "SELECT id, user_id, upc, platform, igdb_id, title, cover_url, game_id, "
        "created_at FROM barcode_link_review "
        "WHERE status = 'pending' ORDER BY created_at, id"
    ).fetchall()
    return [dict(r) for r in rows]


def approve(conn: sqlite3.Connection, review_id: int, *,
            title: str | None = None, igdb_id: int | None = None,
            platform: str | None = None, cover_url: str | None = None) -> None:
    """Approve a pending row into the shared registry (edit-before-approve).

    Supplied overrides win over the submitted values; the merged IDENTITY (never
    game_id) is written to barcode_registry and mirrored back onto the row, which
    is marked approved. Raises ValueError if missing / not pending."""
    row = conn.execute(
        "SELECT upc, title, igdb_id, platform, cover_url, status "
        "FROM barcode_link_review WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ValueError("review not found")
    if row["status"] != "pending":
        raise ValueError("review not pending")
    final_title = title if title is not None else row["title"]
    final_igdb = igdb_id if igdb_id is not None else row["igdb_id"]
    final_platform = platform if platform is not None else row["platform"]
    final_cover = cover_url if cover_url is not None else row["cover_url"]
    registry_put(conn, row["upc"], igdb_id=final_igdb, title=final_title,
                 platform=final_platform, cover_url=final_cover)
    conn.execute(
        "UPDATE barcode_link_review SET title = ?, igdb_id = ?, platform = ?, "
        "cover_url = ?, status = 'approved', resolved_at = datetime('now') "
        "WHERE id = ?",
        (final_title, final_igdb, final_platform, final_cover, review_id))


def reject(conn: sqlite3.Connection, review_id: int) -> None:
    """Reject a pending row (plain reject: provisional stops, UPC resubmittable).

    Raises ValueError if missing / not pending. No registry write."""
    row = conn.execute(
        "SELECT status FROM barcode_link_review WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ValueError("review not found")
    if row["status"] != "pending":
        raise ValueError("review not pending")
    conn.execute(
        "UPDATE barcode_link_review SET status = 'rejected', "
        "resolved_at = datetime('now') WHERE id = ?", (review_id,))
```

- [ ] **Step 4: Insert the provisional precedence step in `resolve`**

In `barcode.py`, `resolve` currently returns the cache candidate inside `if cached:` (~barcode.py:471-482), then falls through to `product = _product_via_sources(upc)`. Insert the provisional block BETWEEN the `if cached:` return and the product lookup (i.e. right before `product = _product_via_sources(upc)` at ~barcode.py:484):

```python
    # A tester's own pending (unapproved) link resolves provisionally for THEM
    # only -- never for other users, and never from the shared registry until the
    # owner approves it. Identity only; ownership derived per-user as in the cache
    # path above.
    pending = pending_for_user(conn, upc, user_id)
    if pending:
        owned_id = _owned_game_id(conn, pending["title"] or "", user_id)
        return {"upc": upc, "source": "provisional",
                "scanned_platform": pending["platform"], "candidates": [{
                    "igdb_id": pending["igdb_id"],
                    "title": pending["title"],
                    "platform": pending["platform"],
                    "cover_url": pending["cover_url"],
                    "game_type": None,
                    "owned_game_id": owned_id,
                    "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
                }]}

    product = _product_via_sources(upc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode_review.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 6: Run the full barcode + isolation suites (no regressions)**

Run: `uv run python -m pytest tests/test_barcode_review.py tests/test_multiuser_isolation.py -q`
Expected: PASS.

- [ ] **Step 7: Lint**

Run: `ruff check barcode.py tests/test_barcode_review.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add barcode.py tests/test_barcode_review.py
git commit -m "feat(barcode): queue helpers + submitter-only provisional resolve"
```

---

### Task 3: Routes — link branch + owner-only review endpoints + completeness gate

**Files:**
- Modify: `app.py` (rewrite `api_barcode_link` at ~app.py:2490; add `api_barcode_review_list` / `api_barcode_review_approve` / `api_barcode_review_reject` after it)
- Modify: `tests/test_multiuser_isolation.py` (add `/api/barcode/review` to `_OWNER_ONLY` at ~line 715)
- Test: `tests/test_barcode_review.py` (extend with route-level tests)

**Interfaces:**
- Consumes: `barcode.registry_put`, `barcode.queue_upsert`, `barcode.list_pending`, `barcode.approve`, `barcode.reject`; `identity.is_owner()`, `identity.current_user_id()`; `get_db()`. (`import barcode`, `import identity` already present in app.py.)
- Produces: routes `POST /api/barcode/link` (now branches owner/tester), `GET /api/barcode/review`, `POST /api/barcode/review/<int:review_id>/approve`, `POST /api/barcode/review/<int:review_id>/reject`.

- [ ] **Step 1: Write the failing route tests**

Append to `tests/test_barcode_review.py`:

```python
from tests.helpers_multiuser import client_as


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_barcode_review.py -k "route or link or review" -v`
Expected: FAIL — tester link currently writes the registry (no `queued`), review routes 404 (not yet defined).

- [ ] **Step 3: Rewrite `api_barcode_link` in `app.py`**

Replace the body of `api_barcode_link` (~app.py:2490-2507) with the owner/tester branch:

```python
@app.route('/api/barcode/link', methods=['POST'])
def api_barcode_link():
    """Record a confirmed UPC -> game mapping.

    Owner links write the shared barcode_registry directly (trusted). A non-owner
    (tester) link is queued into barcode_link_review as a pending row -- provisional
    for the submitter only -- until the owner approves it into the registry."""
    data = request.get_json(silent=True) or {}
    upc = (data.get('upc') or '').strip()
    platform = (data.get('platform') or '').strip() or None
    if not upc or not platform:
        return jsonify({'error': 'upc and platform required'}), 400
    conn = get_db()
    if identity.is_owner():
        barcode.registry_put(conn, upc, igdb_id=data.get('igdb_id'),
                             title=data.get('title'), platform=platform,
                             cover_url=data.get('cover_url'), game_id=data.get('game_id'))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    barcode.queue_upsert(conn, upc=upc, user_id=identity.current_user_id(),
                         platform=platform, igdb_id=data.get('igdb_id'),
                         title=data.get('title'), cover_url=data.get('cover_url'),
                         game_id=data.get('game_id'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'queued': True})
```

- [ ] **Step 4: Add the three review routes in `app.py`**

Add immediately after `api_barcode_link` (before `@app.route('/api/igdb/search')` at ~app.py:2510):

```python
@app.route('/api/barcode/review')
def api_barcode_review_list():
    """Pending tester barcode links awaiting owner approval (owner-only)."""
    if not identity.is_owner():
        return jsonify({'error': 'owner only'}), 403
    conn = get_db()
    items = barcode.list_pending(conn)
    conn.close()
    return jsonify({'items': items, 'count': len(items)})


@app.route('/api/barcode/review/<int:review_id>/approve', methods=['POST'])
def api_barcode_review_approve(review_id):
    """Approve a queued barcode link into the shared registry (owner-only).

    Optional JSON body overrides the queued identity (title/igdb_id/platform/
    cover_url); the edited values are what get written to barcode_registry."""
    if not identity.is_owner():
        return jsonify({'error': 'owner only'}), 403
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        barcode.approve(conn, review_id, title=data.get('title'),
                        igdb_id=data.get('igdb_id'), platform=data.get('platform'),
                        cover_url=data.get('cover_url'))
    except ValueError as exc:
        conn.rollback()
        conn.close()
        msg = str(exc)
        return jsonify({'error': msg}), 404 if 'not found' in msg else 409
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/barcode/review/<int:review_id>/reject', methods=['POST'])
def api_barcode_review_reject(review_id):
    """Reject a queued barcode link (owner-only). The provisional stops applying;
    the UPC is resubmittable (no permanent-reject memory in v1)."""
    if not identity.is_owner():
        return jsonify({'error': 'owner only'}), 403
    conn = get_db()
    try:
        barcode.reject(conn, review_id)
    except ValueError as exc:
        conn.rollback()
        conn.close()
        msg = str(exc)
        return jsonify({'error': msg}), 404 if 'not found' in msg else 409
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
```

- [ ] **Step 5: Add `/api/barcode/review` to the isolation completeness gate**

In `tests/test_multiuser_isolation.py`, add to the `_OWNER_ONLY` dict (~line 715, before the closing `}` at ~734):

```python
    "/api/barcode/review":
        "owner-only queue of tester barcode links awaiting approval; a non-owner "
        "gets 403 and never reads other users' pending submissions. Covered by "
        "test_review_list_owner_only",
```

(The `<int:review_id>/approve` and `/reject` routes are POST-only, so the GET-only completeness gate does not enumerate them — no entry needed for those.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode_review.py tests/test_multiuser_isolation.py -q`
Expected: PASS (including `test_every_api_get_route_is_isolation_covered`).

- [ ] **Step 7: Lint**

Run: `ruff check app.py tests/test_barcode_review.py tests/test_multiuser_isolation.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_barcode_review.py tests/test_multiuser_isolation.py
git commit -m "feat(barcode): owner-gated review API + tester link queues"
```

---

### Task 4: Owner review UI in `settings.html`

**Files:**
- Modify: `templates/settings.html` (add a review section after `#bundle-review-section` ~line 188; add `loadBarcodeReviews`/`approveBarcode`/`rejectBarcode` JS near the bundle-review JS ~line 858; call `loadBarcodeReviews()` inside `loadSettings` ~line 372)
- Test: `tests/test_barcode_review.py` (extend with a template-render smoke test)

**Interfaces:**
- Consumes: `GET /api/barcode/review`, `POST /api/barcode/review/<id>/approve`, `POST /api/barcode/review/<id>/reject` (Task 3); the page's existing `api` helper (`api.get` returns the parsed body; `api.post` returns `{ok, status, data}`) and `escapeHtml`.

- [ ] **Step 1: Write the failing smoke test**

The review UI is owner-only and JS-driven; a lightweight guard is that the settings page renders the section container and its loader wiring. Append to `tests/test_barcode_review.py`:

```python
def test_settings_page_has_barcode_review_section(mu_db):
    html = client_as(1).get("/settings").get_data(as_text=True)
    assert 'id="barcode-review-section"' in html
    assert 'loadBarcodeReviews' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode_review.py::test_settings_page_has_barcode_review_section -v`
Expected: FAIL — the section id is not in the template yet.

- [ ] **Step 3: Add the review section HTML**

In `templates/settings.html`, add after the `#bundle-review-section` closing `</div>` (~line 188):

```html
    <!-- Tester barcode links needing owner approval -->
    <div id="barcode-review-section" class="bg-surface-light rounded-xl p-6 hidden">
        <h2 class="text-lg font-medium text-white mb-4 flex items-center">
            <span class="mr-2">🏷️</span>
            Barcode Links — Needs Review
        </h2>
        <p class="text-sm text-gray-400 mb-4">
            Testers scanned these barcodes and proposed a game. Approving adds the
            link to the shared registry for everyone (edit the fields first if the
            match is wrong); rejecting removes it (the tester can resubmit).
        </p>
        <div id="barcode-review-list" class="grid grid-cols-1 sm:grid-cols-2 gap-3"></div>
    </div>
```

- [ ] **Step 4: Add the loader + approve/reject JS**

In `templates/settings.html`, add after `dismissBundle` (~line 858), mirroring the bundle-review JS:

```javascript
    async function loadBarcodeReviews() {
        // Owner-only: a non-owner GET returns 403 -> body has no .items -> hidden.
        const res = await api.get('/api/barcode/review');
        const items = res.items || [];
        const section = document.getElementById('barcode-review-section');
        section.classList.toggle('hidden', items.length === 0);
        document.getElementById('barcode-review-list').innerHTML = items.map(i => `
            <div class="bg-surface rounded-lg border border-gray-700 p-3">
                <div class="text-[11px] text-gray-500 truncate">UPC ${escapeHtml(i.upc)} · submitter #${i.user_id}</div>
                <input id="bc-title-${i.id}" value="${escapeHtml(i.title || '')}" placeholder="Title"
                       class="mt-2 w-full bg-black/20 rounded border border-gray-600 px-2 py-1 text-xs text-white focus:border-accent focus:outline-none">
                <div class="mt-2 flex gap-2">
                    <input id="bc-platform-${i.id}" value="${escapeHtml(i.platform || '')}" placeholder="Platform"
                           class="w-1/2 bg-black/20 rounded border border-gray-600 px-2 py-1 text-xs text-white focus:border-accent focus:outline-none">
                    <input id="bc-igdb-${i.id}" value="${i.igdb_id ?? ''}" placeholder="IGDB id" inputmode="numeric"
                           class="w-1/2 bg-black/20 rounded border border-gray-600 px-2 py-1 text-xs text-white focus:border-accent focus:outline-none">
                </div>
                <input id="bc-cover-${i.id}" value="${escapeHtml(i.cover_url || '')}" placeholder="Cover URL"
                       class="mt-2 w-full bg-black/20 rounded border border-gray-600 px-2 py-1 text-xs text-white focus:border-accent focus:outline-none">
                <div class="mt-2 flex gap-2">
                    <button onclick="approveBarcode(${i.id})" class="text-xs bg-accent/80 hover:bg-accent text-white rounded px-2 py-1">Approve</button>
                    <button onclick="rejectBarcode(${i.id})" class="text-xs bg-surface-light hover:bg-gray-600 rounded px-2 py-1">Reject</button>
                </div>
            </div>`).join('');
    }

    async function approveBarcode(id) {
        const igdbRaw = (document.getElementById(`bc-igdb-${id}`).value || '').trim();
        const body = {
            title: (document.getElementById(`bc-title-${id}`).value || '').trim() || null,
            platform: (document.getElementById(`bc-platform-${id}`).value || '').trim() || null,
            igdb_id: igdbRaw ? parseInt(igdbRaw, 10) : null,
            cover_url: (document.getElementById(`bc-cover-${id}`).value || '').trim() || null,
        };
        const r = await api.post(`/api/barcode/review/${id}/approve`, body);
        if (!r.ok) { alert(r.data?.error || 'Could not approve'); return; }
        await loadBarcodeReviews();
    }

    async function rejectBarcode(id) {
        const r = await api.post(`/api/barcode/review/${id}/reject`, {});
        if (!r.ok) { alert(r.data?.error || 'Could not reject'); return; }
        await loadBarcodeReviews();
    }
```

- [ ] **Step 5: Wire the loader into `loadSettings`**

In `templates/settings.html`, `loadSettings` already calls `loadBundleReviews();` (~line 372). Add the barcode loader right after it:

```javascript
        loadBundleReviews();
        loadBarcodeReviews();
```

- [ ] **Step 6: Run the smoke test + full barcode suite**

Run: `uv run python -m pytest tests/test_barcode_review.py -v`
Expected: PASS (all tasks' tests, including the template smoke test).

- [ ] **Step 7: Manual verification note (controller/owner)**

Automated coverage cannot exercise the JS. During review, the controller confirms by reading the diff that: the section id/loader are present, `escapeHtml` is used on all interpolated strings, the approve body maps the four editable fields, and non-owner viewers keep the section hidden (403 → no `.items`). A live spot-check (owner sees a queued tester link, edits the title, approves, and it appears in the registry) is done at deploy.

- [ ] **Step 8: Lint (Python only — ruff does not touch templates)**

Run: `ruff check tests/test_barcode_review.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add templates/settings.html tests/test_barcode_review.py
git commit -m "feat(barcode): owner review-queue UI in settings"
```

---

## Full-suite gate (after Task 4)

Run the whole suite + lint to confirm no regressions across the ~1153 existing tests:

Run: `uv run python -m pytest -q && ruff check .`
Expected: all green, ruff clean.

---

## Self-Review (plan author)

**Spec coverage:**
- New `barcode_link_review` table + `UNIQUE(user_id, upc)` + migration → Task 1. ✅
- Submission branch (owner→registry, tester→queue) → Task 3 (`api_barcode_link`). ✅
- Submitter-only provisional resolve precedence (registry → own pending → IGDB), `source='provisional'`, identity-only, ownership via `_owned_game_id` → Task 2. ✅
- Owner-only review endpoints list/approve(edit)/reject, 403 non-owner, 404/409 → Task 3 (+ helpers Task 2). ✅
- `game_id` never carried into registry on approve → Task 2 `approve` (asserted in `test_approve_writes_edited_identity_no_game_id`, `test_route_approve_with_edit`). ✅
- Plain reject, resubmittable → Task 2 `reject` + `queue_upsert` re-pending (asserted `test_reject_stops_provisional`, `test_queue_upsert_then_pending_for_user`). ✅
- UI review section mirroring bundle-review → Task 4. ✅
- `/api/barcode/review` added to `_OWNER_ONLY` completeness gate → Task 3 Step 5. ✅
- All 9 spec test scenarios map to tests in Tasks 1-3; UI smoke test in Task 4. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code.

**Type consistency:** `queue_upsert`/`pending_for_user`/`list_pending`/`approve`/`reject` signatures are identical between the Task 2 Interfaces block, the Task 2 implementation, and the Task 3 route call sites. `approve` override kwargs (`title/igdb_id/platform/cover_url`) match the route's `data.get(...)` keys and the JS approve body keys. `resolve` `source='provisional'` string matches the tests. ✅
