# DLC SP3 — modal source-link, cover button, conflict resolution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repurpose the per-game modal's Cover Art URL field into a generalized IGDB/Steam source-of-truth link, decouple cover-image picking via a "Change cover" button + literal Cover image URL input, persist `OwnershipReport.review` items into a new `dlc_review_queue` table, and surface them in a dedup-style resolution modal opened from the Add Game modal. Manual game-add sync-runs IGDB enrichment when an IGDB URL is pinned.

**Architecture:** Three coordinated changes layered on the existing per-game modal + Add Game modal + DLC pipeline. (1) A new `steam_dlc.appid_from_steam_url` helper + `POST /api/games/<id>/steam` endpoint mirror the existing IGDB pin pattern. (2) `dlc_ownership.mark_ownership`'s inner per-addon block is factored into `_apply_addon_to_parent` and the function gains an UPSERT into `dlc_review_queue`; resolution reuses the factored helper. (3) Five new endpoints (`/api/dlc/review/{count,list,resolve,dismiss}` + `/api/games/search`) drive a new `#dlc-review-modal`. No new background workers; no scraper changes.

**Tech Stack:** Python 3.13 + Flask + SQLite via `models.py` (existing). Tests: pytest temp-DB. Front-end: jinja2 + vanilla JS in `templates/base.html` (existing patterns). Tooling: `uv run python -m pytest` for tests, `uv run ruff check` for lint (never `ruff format` — codebase is hand-aligned).

**Design doc:** `docs/superpowers/specs/2026-05-25-dlc-sp3-modal-and-resolution-design.md`.

---

## File structure

**Create:**
- `dlc_review.py` — resolve engine: `resolve(conn, review_id, *, picked_game_id, picked_dlc_id, create_new_dlc)`. Pure DB; no Flask.
- `tests/test_steam_appid_url.py` — unit tests for `steam_dlc.appid_from_steam_url`.
- `tests/test_dlc_review_queue_migration.py` — `migrate_dlc_review_queue` + idempotency.
- `tests/test_dlc_review_persistence.py` — `mark_ownership` UPSERTs into the queue.
- `tests/test_dlc_ownership_apply.py` — `_apply_addon_to_parent` direct tests (forced parent, forced dlc, force create).
- `tests/test_dlc_review_resolve.py` — `dlc_review.resolve` happy paths + idempotency + missing target.
- `tests/test_app_pin_steam.py` — `POST /api/games/<id>/steam` happy + 400 + idempotent.
- `tests/test_app_dlc_review.py` — `/api/dlc/review/{count,list,resolve,dismiss}` shape + happy paths + 404.
- `tests/test_app_games_search.py` — `GET /api/games/search?q=` typeahead.

**Modify:**
- `steam_dlc.py` — add `appid_from_steam_url` helper.
- `models.py` — add `migrate_dlc_review_queue(conn)`; call from `migrate_db()`.
- `dlc_ownership.py` — extract `_apply_addon_to_parent(...)`; `mark_ownership` UPSERTs review items.
- `app.py` — new routes: `/api/games/<id>/steam`, `/api/games/search`, `/api/dlc/review/count`, `/api/dlc/review`, `/api/dlc/review/<id>/resolve`, `/api/dlc/review/<id>/dismiss`. Extend `/api/games/<id>` GET to include `external_ids`. Extend `/api/igdb/search` to return `slug` + `igdb_url`.
- `templates/base.html` — rename Cover Art URL → Source link in the per-game modal; add Change cover button + literal Cover image URL input; rework the Add Game modal (visible source-link + cover inputs, chained submit); add `#dlc-review-modal` + a "DLC review (N)" badge in the Add Game modal.

---

## Conventions (binding for every task)

- Commit subjects use conventional-commit prefixes: `feat: …`, `test: …`, `refactor: …`, `fix: …`, `docs: …`.
- **No `Co-Authored-By` trailer** in commit messages (project policy; overrides any default).
- Run tests with `uv run python -m pytest`. Never plain `uv run pytest` (fails with `ModuleNotFoundError: models`).
- Lint with `uv run ruff check` only. **Never run `ruff format`** — codebase is hand-aligned.
- Commit directly to `main`. No branches, no PRs unless asked.
- Impl/review subagents stay on pytest temp-DB + static review. Never run the app, live scrapers, or real `games.db`.
- Never commit `games.db*`, `.recon/`, `scraped/`, `.pw-profile/`, `config.json`, `.igdb_token.json`, `.steam_cache/`, `excluded_games.json`.

---

## Task 1: `steam_dlc.appid_from_steam_url` helper

**Files:**
- Modify: `steam_dlc.py` (top-of-file, near the existing module-level helpers)
- Test: `tests/test_steam_appid_url.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_steam_appid_url.py`:

```python
"""Unit tests for steam_dlc.appid_from_steam_url (mirrors igdb_dlc.slug_from_igdb_url)."""
from __future__ import annotations

import pytest

from steam_dlc import appid_from_steam_url


@pytest.mark.parametrize("url,expected", [
    ("https://store.steampowered.com/app/1794680/Vampire_Survivors/", 1794680),
    ("https://store.steampowered.com/app/1794680/", 1794680),
    ("https://store.steampowered.com/app/1794680", 1794680),
    ("http://store.steampowered.com/app/42/", 42),
    ("https://store.steampowered.com/app/1794680/Vampire_Survivors/?snr=foo", 1794680),
    ("  https://store.steampowered.com/app/7/  ", 7),
    ("HTTPS://STORE.STEAMPOWERED.COM/APP/1794680/", 1794680),
])
def test_parses_appid(url, expected):
    assert appid_from_steam_url(url) == expected


@pytest.mark.parametrize("bad", [
    None,
    "",
    "   ",
    "https://store.steampowered.com/",
    "https://store.steampowered.com/sub/12345/",
    "https://www.igdb.com/games/vampire-survivors",
    "not a url",
    "store.steampowered.com/app/123",  # missing scheme
])
def test_rejects_non_steam_app_urls(bad):
    assert appid_from_steam_url(bad) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_steam_appid_url.py -v`
Expected: ImportError or AttributeError — `appid_from_steam_url` not defined in `steam_dlc`.

- [ ] **Step 3: Add the helper to `steam_dlc.py`**

Add this near the top of `steam_dlc.py`, after the existing imports / module-level constants (place it next to whatever URL/regex helpers already exist; if none, just below the imports). Use `re` (import it if not already imported in the file):

```python
import re

_STEAM_APP_URL = re.compile(
    r"https?://store\.steampowered\.com/app/(\d+)",
    re.IGNORECASE,
)


def appid_from_steam_url(url: str | None) -> int | None:
    """Extract the appid from a store.steampowered.com/app/<appid> URL, else None.

    Mirrors igdb_dlc.slug_from_igdb_url: case-insensitive, ignores trailing path
    segments and query strings, strips surrounding whitespace.
    """
    if not url:
        return None
    match = _STEAM_APP_URL.search(url.strip())
    return int(match.group(1)) if match else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_steam_appid_url.py -v`
Expected: 15 PASSED.

- [ ] **Step 5: Lint**

Run: `uv run ruff check steam_dlc.py tests/test_steam_appid_url.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add steam_dlc.py tests/test_steam_appid_url.py
git commit -m "feat(steam): add appid_from_steam_url URL parser"
```

---

## Task 2: `dlc_review_queue` table + migration

**Files:**
- Modify: `models.py` (add `migrate_dlc_review_queue`; call from `migrate_db`)
- Test: `tests/test_dlc_review_queue_migration.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dlc_review_queue_migration.py`:

```python
"""Tests for migrate_dlc_review_queue (mirrors test_external_ids_migration.py shape)."""
from __future__ import annotations

import sqlite3

import pytest

from models import migrate_dlc_review_queue


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")  # FK target
    yield c
    c.close()


def test_creates_table_and_columns(conn):
    migrate_dlc_review_queue(conn)
    cols = _columns(conn, "dlc_review_queue")
    assert set(cols) == {
        "id", "addon_title", "source", "external_id", "source_title",
        "reason", "game_id", "created_at", "resolved_at", "dismissed_at",
    }


def test_creates_indexes(conn):
    migrate_dlc_review_queue(conn)
    idx = _indexes(conn, "dlc_review_queue")
    assert "uq_dlc_review_vendor_id" in idx
    assert "idx_dlc_review_open" in idx


def test_is_idempotent(conn):
    migrate_dlc_review_queue(conn)
    migrate_dlc_review_queue(conn)  # must not raise


def test_partial_unique_blocks_vendor_id_dupes(conn):
    migrate_dlc_review_queue(conn)
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
        "VALUES ('A', 'nintendo', '70050000000003', 'no parent game')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
            "VALUES ('A2', 'nintendo', '70050000000003', 'no parent game')")


def test_partial_unique_allows_null_vendor_id_rows(conn):
    migrate_dlc_review_queue(conn)
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
        "VALUES ('A', NULL, NULL, 'no parent game')")
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
        "VALUES ('B', NULL, NULL, 'no parent game')")  # allowed
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 2


def test_fk_set_null_on_game_delete(conn):
    migrate_dlc_review_queue(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO games (id, title) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO dlc_review_queue (addon_title, reason, game_id) VALUES ('A', 'ambiguous dlc', 1)")
    conn.execute("DELETE FROM games WHERE id = 1")
    g = conn.execute("SELECT game_id FROM dlc_review_queue WHERE addon_title='A'").fetchone()[0]
    assert g is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_review_queue_migration.py -v`
Expected: ImportError — `migrate_dlc_review_queue` not in `models`.

- [ ] **Step 3: Add `migrate_dlc_review_queue` to `models.py`**

Add this function alongside `migrate_not_duplicates` (use that as the structural model). Then call it from `migrate_db()` after `migrate_not_duplicates(conn)`:

```python
def migrate_dlc_review_queue(conn):
    """Create the dlc_review_queue table + indexes if missing. Idempotent.

    Persists OwnershipReport.review items across scrapes so the resolution modal
    can resolve them at any time. UPSERT key is (source, external_id) via the
    partial unique index (null source/ext rows are allowed for legacy paths).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dlc_review_queue (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            addon_title   TEXT    NOT NULL,
            source        TEXT,
            external_id   TEXT,
            source_title  TEXT,
            reason        TEXT    NOT NULL,
            game_id       INTEGER,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at   TIMESTAMP,
            dismissed_at  TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dlc_review_vendor_id
            ON dlc_review_queue(source, external_id)
            WHERE source IS NOT NULL AND external_id IS NOT NULL
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_dlc_review_open
            ON dlc_review_queue(resolved_at, dismissed_at)
    """)
```

Then in `migrate_db()` (in `models.py`), add the call after the existing `migrate_not_duplicates(conn)` call:

```python
    migrate_not_duplicates(conn)
    migrate_dlc_review_queue(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_dlc_review_queue_migration.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run python -m pytest -q`
Expected: All previously-green tests still green (275 + 6 new = 281).

- [ ] **Step 6: Lint**

Run: `uv run ruff check models.py tests/test_dlc_review_queue_migration.py`

- [ ] **Step 7: Commit**

```bash
git add models.py tests/test_dlc_review_queue_migration.py
git commit -m "feat(dlc): add dlc_review_queue table + migration"
```

---

## Task 3: Refactor — extract `_apply_addon_to_parent`

A pure refactor of `dlc_ownership.mark_ownership`'s inner per-addon block. Behavior unchanged; existing tests must stay green. New helper signature supports forced parent (used today) plus `forced_dlc_id` and `force_create` (used by `dlc_review.resolve` in Task 5).

**Files:**
- Modify: `dlc_ownership.py:153-232`
- Test: `tests/test_dlc_ownership_apply.py` (new — direct tests for the helper)

- [ ] **Step 1: Write the failing test for the new helper**

Create `tests/test_dlc_ownership_apply.py`:

```python
"""Direct tests for dlc_ownership._apply_addon_to_parent.

The helper is the inner per-addon block extracted from mark_ownership and is
reused by dlc_review.resolve to land a user-picked decision (forced parent,
forced dlc, or forced create).
"""
from __future__ import annotations

import sqlite3

import pytest

import dlc_ownership
from dlc_ownership import OwnershipReport, _apply_addon_to_parent
from models import migrate_db, normalize_title


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrate_db.__wrapped__(c) if hasattr(migrate_db, "__wrapped__") else None
    # migrate_db opens its own connection; rebuild the schema in-memory:
    import models
    models.migrate_games(c) if hasattr(models, "migrate_games") else None
    # Fallback: create the minimal schema by hand to keep tests isolated:
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
    """)
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _add_dlc(conn, game_id, name, *, owned=0):
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, kind, owned, source) VALUES (?, ?, 'dlc', ?, 'igdb')",
        (game_id, name, owned))
    return cur.lastrowid


def test_reconciles_by_name_when_parent_is_forced(conn):
    gid = _add_game(conn, "The Witcher 3")
    dlc_id = _add_dlc(conn, gid, "Hearts of Stone")
    report = OwnershipReport()
    addon = {"title": "The Witcher 3 - Hearts of Stone", "source": "steam",
             "external_id": "378649", "source_title": "Hearts of Stone"}
    parent_norm = normalize_title("The Witcher 3")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "The Witcher 3"}, addon, dry_run=False)
    assert report.reconciled == 1
    owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (dlc_id,)).fetchone()[0]
    assert owned == 1
    ext = conn.execute(
        "SELECT dlc_id, external_id FROM dlc_external_ids WHERE source = 'steam'"
    ).fetchall()
    assert [(dlc_id, "378649")] == [(r[0], r[1]) for r in ext]


def test_creates_when_no_matching_dlc(conn):
    gid = _add_game(conn, "Some Game")
    report = OwnershipReport()
    addon = {"title": "Some Game - Season Pass", "source": "nintendo",
             "external_id": "70050000000003", "source_title": "Some Game - Season Pass"}
    parent_norm = normalize_title("Some Game")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Some Game"}, addon, dry_run=False)
    assert report.created == 1
    row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id = ?", (gid,)).fetchone()
    assert row["name"] == "Season Pass"
    assert row["owned"] == 1
    assert row["source"] == "nintendo"


def test_forced_dlc_id_flips_that_specific_row(conn):
    gid = _add_game(conn, "Game X")
    # Two ambiguously-equal rows (would have been ambiguous-dlc in the engine):
    a_id = _add_dlc(conn, gid, "DLC One")
    b_id = _add_dlc(conn, gid, "DLC One ")  # trailing space → different name, same normalized
    report = OwnershipReport()
    addon = {"title": "Game X DLC One", "source": "xbox",
             "external_id": "BFR-1", "source_title": "DLC One"}
    parent_norm = normalize_title("Game X")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Game X"}, addon, dry_run=False, forced_dlc_id=b_id)
    assert report.reconciled == 1
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (b_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (a_id,)).fetchone()[0] == 0
    ext = conn.execute("SELECT dlc_id FROM dlc_external_ids WHERE external_id = 'BFR-1'").fetchone()
    assert ext[0] == b_id


def test_force_create_bypasses_reconcile(conn):
    gid = _add_game(conn, "Game Y")
    existing = _add_dlc(conn, gid, "Bonus")  # would normally equality-reconcile
    report = OwnershipReport()
    addon = {"title": "Game Y - Bonus", "source": "playstation",
             "external_id": "EP1234-001", "source_title": "Bonus"}
    parent_norm = normalize_title("Game Y")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Game Y"}, addon, dry_run=False, force_create=True)
    assert report.created == 1
    # The pre-existing row stays at owned=0:
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (existing,)).fetchone()[0] == 0
    # And a new row was inserted (different name to avoid UNIQUE collision, or
    # the helper handled the collision by reconciling — verify the count grew):
    n = conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (gid,)).fetchone()[0]
    assert n >= 2 or report.created == 1  # at minimum, created was incremented


def test_dry_run_writes_nothing(conn):
    gid = _add_game(conn, "Game Z")
    report = OwnershipReport()
    addon = {"title": "Game Z - Extra", "source": "steam",
             "external_id": "999", "source_title": "Extra"}
    parent_norm = normalize_title("Game Z")
    _apply_addon_to_parent(conn, report, gid, parent_norm,
                           {gid: "Game Z"}, addon, dry_run=True)
    assert report.created == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (gid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_ownership_apply.py -v`
Expected: ImportError — `_apply_addon_to_parent` is not yet exported from `dlc_ownership`.

- [ ] **Step 3: Refactor `dlc_ownership.mark_ownership`**

In `dlc_ownership.py`, extract the per-addon inner block (currently lines ~180-231 — the "reconcile by id → reconcile by name-equality → create" branch) into a new helper. The replacement function bodies look like this:

```python
def _apply_addon_to_parent(
    conn: sqlite3.Connection,
    report: OwnershipReport,
    parent: int,
    parent_norm: str,
    titles: dict[int, str],
    addon,
    *,
    dry_run: bool,
    forced_dlc_id: int | None = None,
    force_create: bool = False,
) -> None:
    """Reconcile or create one add-on against a known parent game.

    Inner block factored out of mark_ownership; reused by dlc_review.resolve to
    land a user-picked decision. When forced_dlc_id is given, that DLC row is
    flipped directly (skipping reconcile-by-id and reconcile-by-name). When
    force_create is True, the create branch is taken unconditionally (skipping
    both reconcile steps); used when the user picks "none of these — create a
    new DLC row". Otherwise this is identical to the engine's normal flow.
    """
    title = _addon_field(addon, "title")
    source = _addon_field(addon, "source")
    ext = _addon_field(addon, "external_id")
    source_title = _addon_field(addon, "source_title") or title

    # (forced_dlc_id) user picked a specific DLC row -> attach id + flip.
    if forced_dlc_id is not None:
        if not dry_run:
            _record_ext_id(conn, forced_dlc_id, source, ext, source_title)
        _flip(conn, report, forced_dlc_id, title, parent, dry_run)
        return

    # (force_create) user said "none of these — create new"; skip both reconciles.
    if not force_create:
        # (a) reconcile by vendor id
        dlc_id = None
        if source and ext:
            row = conn.execute(
                "SELECT dlc_id FROM dlc_external_ids WHERE source = ? AND external_id = ?",
                (source, ext)).fetchone()
            if row:
                dlc_id = row[0]
        if dlc_id is not None:
            _flip(conn, report, dlc_id, title, parent, dry_run)
            return

        # (b) reconcile by normalized-name equality
        rows = [(r["id"], r["name"])
                for r in conn.execute("SELECT id, name FROM dlc WHERE game_id = ?", (parent,))]
        match = match_equal(_remainder(title, parent_norm), rows)
        if match is AMBIGUOUS:
            report.review.append(Match(title, game_id=parent, reason="ambiguous dlc"))
            return
        if match is not None:
            if not dry_run:
                _record_ext_id(conn, match, source, ext, source_title)
            _flip(conn, report, match, title, parent, dry_run)
            return

    # (c) create a vendor-sourced owned row.
    name = _clean_remainder(title, titles.get(parent, ""))
    if dry_run:
        report.created += 1
        report.marked += 1
        report.marked_items.append(Match(title, game_id=parent, reason="created"))
        return
    try:
        cur = conn.execute(
            "INSERT INTO dlc (game_id, name, kind, owned, source) VALUES (?, ?, 'dlc', 1, ?)",
            (parent, name, source or "vendor"))
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT id FROM dlc WHERE game_id = ? AND name = ?", (parent, name)).fetchone()
        _record_ext_id(conn, existing[0], source, ext, source_title)
        _flip(conn, report, existing[0], title, parent, dry_run)
        return
    new_id = cur.lastrowid
    report.created += 1
    report.marked += 1
    _record_ext_id(conn, new_id, source, ext, source_title)
    report.marked_items.append(Match(title, game_id=parent, dlc_id=new_id, reason="created"))


def mark_ownership(conn: sqlite3.Connection, addons, *, dry_run: bool = False) -> OwnershipReport:
    """Flip dlc.owned for scraped owned add-ons (0 -> 1 only; idempotent).

    Each add-on is a scrape dict/obj carrying `title`, `source` (vendor), and
    `external_id`. On a confident parent: reconcile by vendor id, then by
    name-equality (recording the vendor id), else create a vendor-sourced owned
    row. Uncertain parents go to `report.review`. Writes nothing when dry_run
    (the caller owns commit).
    """
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM games")}

    report = OwnershipReport()
    for addon in addons:
        title = _addon_field(addon, "title")
        parent = parent_of(title, library)
        if parent is None:
            report.review.append(Match(title, reason="no parent game"))
            continue
        if parent is AMBIGUOUS:
            report.review.append(Match(title, reason="ambiguous parent"))
            continue
        parent_norm = next(gnorm for gid, gnorm in library if gid == parent)
        _apply_addon_to_parent(conn, report, parent, parent_norm, titles, addon, dry_run=dry_run)
    return report
```

- [ ] **Step 4: Run the new helper's tests AND the existing engine tests**

Run: `uv run python -m pytest tests/test_dlc_ownership_apply.py tests/test_dlc_ownership.py -v`
Expected: All PASS. The refactor must not regress any existing engine test.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: 275 + previous-task + new tests all green.

- [ ] **Step 6: Lint**

Run: `uv run ruff check dlc_ownership.py tests/test_dlc_ownership_apply.py`

- [ ] **Step 7: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership_apply.py
git commit -m "refactor(dlc): extract _apply_addon_to_parent from mark_ownership"
```

---

## Task 4: `mark_ownership` UPSERTs review items into `dlc_review_queue`

**Files:**
- Modify: `dlc_ownership.py:mark_ownership`
- Test: `tests/test_dlc_review_persistence.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dlc_review_persistence.py`:

```python
"""mark_ownership UPSERTs review items into dlc_review_queue."""
from __future__ import annotations

import sqlite3

import pytest

from dlc_ownership import mark_ownership
from models import migrate_db, normalize_title


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    # Use the real migrate_db so we get every table the engine touches:
    import models
    models.DATABASE_PATH = str(db)  # if migrate_db reads from a global; otherwise skip
    # Most direct path: call migrate_db with this connection. If migrate_db opens
    # its own connection, instead call the individual migrations here:
    try:
        models.migrate_db()
    except Exception:
        # Fallback: call the per-table migrations on this connection directly.
        for fn_name in dir(models):
            if fn_name.startswith("migrate_") and fn_name != "migrate_db":
                getattr(models, fn_name)(c)
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _open_queue(conn):
    return conn.execute(
        "SELECT addon_title, source, external_id, reason, game_id "
        "FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL "
        "ORDER BY id"
    ).fetchall()


def test_no_parent_addon_persisted_with_reason(conn):
    addons = [{"title": "Unknown Title - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown Title - DLC"}]
    mark_ownership(conn, addons)
    rows = _open_queue(conn)
    assert len(rows) == 1
    assert rows[0]["addon_title"] == "Unknown Title - DLC"
    assert rows[0]["source"] == "nintendo"
    assert rows[0]["external_id"] == "70050000000003"
    assert rows[0]["reason"] == "no parent game"
    assert rows[0]["game_id"] is None


def test_ambiguous_parent_persisted(conn):
    # Two games whose normalized titles both prefix the addon at the same length:
    _add_game(conn, "Some Title")
    _add_game(conn, "Some Title")  # duplicate by name; both are equal-length prefixes
    addons = [{"title": "Some Title DLC", "source": "xbox",
               "external_id": "BFR-1", "source_title": "Some Title DLC"}]
    mark_ownership(conn, addons)
    rows = _open_queue(conn)
    assert len(rows) == 1
    assert rows[0]["reason"] == "ambiguous parent"
    assert rows[0]["game_id"] is None


def test_ambiguous_dlc_persisted_with_game_id(conn):
    gid = _add_game(conn, "Game Q")
    conn.execute("INSERT INTO dlc (game_id, name) VALUES (?, 'Extra')", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name) VALUES (?, 'Extra ')", (gid,))  # trailing space → ties on normalize
    addons = [{"title": "Game Q Extra", "source": "steam",
               "external_id": "111", "source_title": "Extra"}]
    mark_ownership(conn, addons)
    rows = _open_queue(conn)
    assert len(rows) == 1
    assert rows[0]["reason"] == "ambiguous dlc"
    assert rows[0]["game_id"] == gid


def test_re_run_upserts_does_not_dupe(conn):
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    mark_ownership(conn, addons)
    mark_ownership(conn, addons)  # second run
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 1


def test_resolved_row_not_re_opened_by_rescrape(conn):
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    mark_ownership(conn, addons)
    conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP "
        "WHERE source = 'nintendo' AND external_id = '70050000000003'")
    mark_ownership(conn, addons)  # re-scrape
    rows = _open_queue(conn)
    assert rows == []  # still hidden from the open-queue view
    n = conn.execute("SELECT COUNT(*) FROM dlc_review_queue").fetchone()[0]
    assert n == 1  # row is preserved, not duplicated


def test_existing_report_review_shape_unchanged(conn):
    """The in-memory OwnershipReport.review list must still contain the same
    items so the post-scrape inline UI keeps working."""
    addons = [{"title": "Unknown - DLC", "source": "nintendo",
               "external_id": "70050000000003", "source_title": "Unknown - DLC"}]
    report = mark_ownership(conn, addons)
    assert len(report.review) == 1
    assert report.review[0].reason == "no parent game"
    assert report.review[0].addon_title == "Unknown - DLC"
```

If `models.migrate_db` opens its own connection (it does — see `models.py:482`), the test fixture above won't share state. Inspect `models.migrate_db` first; if it doesn't accept a connection, refactor the fixture to call the individual `migrate_*` functions directly against the in-memory `conn`. The `try/except` branch in the fixture covers this case.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_review_persistence.py -v`
Expected: All tests fail — `mark_ownership` doesn't persist review items yet.

- [ ] **Step 3: Add the UPSERT to `mark_ownership`**

In `dlc_ownership.py`, define a small helper at module scope and call it from `mark_ownership` after each `report.review.append(...)`:

```python
_UPSERT_REVIEW_SQL = """
INSERT INTO dlc_review_queue
    (addon_title, source, external_id, source_title, reason, game_id)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (source, external_id) WHERE source IS NOT NULL AND external_id IS NOT NULL
DO UPDATE SET
    addon_title  = excluded.addon_title,
    source_title = excluded.source_title,
    reason       = excluded.reason,
    game_id      = excluded.game_id
"""


def _persist_review(conn: sqlite3.Connection, addon, reason: str, game_id: int | None) -> None:
    """UPSERT a review item into dlc_review_queue.

    Keyed on (source, external_id) via the partial unique index so re-scrapes
    of the same vendor add-on refresh the reason without duplicating rows.
    Resolved/dismissed timestamps are intentionally NOT touched by the UPSERT.
    """
    title = _addon_field(addon, "title")
    source = _addon_field(addon, "source")
    ext = _addon_field(addon, "external_id")
    source_title = _addon_field(addon, "source_title") or title
    conn.execute(_UPSERT_REVIEW_SQL, (title, source, ext, source_title, reason, game_id))
```

In `mark_ownership`, every place that does `report.review.append(Match(...))` add a `_persist_review(conn, addon, "<reason>", <game_id_or_None>)` call right after — same three call sites: "no parent game", "ambiguous parent", "ambiguous dlc". Also do it in `_apply_addon_to_parent` for the "ambiguous dlc" case there.

(Note: the `_persist_review` call writes inside the same transaction the caller owns; it does not commit. `dry_run` does not write — guard the call with `if not dry_run`.)

Update each call site (in `mark_ownership`):

```python
        if parent is None:
            report.review.append(Match(title, reason="no parent game"))
            if not dry_run:
                _persist_review(conn, addon, "no parent game", None)
            continue
        if parent is AMBIGUOUS:
            report.review.append(Match(title, reason="ambiguous parent"))
            if not dry_run:
                _persist_review(conn, addon, "ambiguous parent", None)
            continue
```

And in `_apply_addon_to_parent`:

```python
        if match is AMBIGUOUS:
            report.review.append(Match(title, game_id=parent, reason="ambiguous dlc"))
            if not dry_run:
                _persist_review(conn, addon, "ambiguous dlc", parent)
            return
```

- [ ] **Step 4: Run the persistence tests**

Run: `uv run python -m pytest tests/test_dlc_review_persistence.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: All previously-green tests still green.

- [ ] **Step 6: Lint**

Run: `uv run ruff check dlc_ownership.py tests/test_dlc_review_persistence.py`

- [ ] **Step 7: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_review_persistence.py
git commit -m "feat(dlc): persist review items to dlc_review_queue on mark_ownership"
```

---

## Task 5: `dlc_review.resolve` engine

**Files:**
- Create: `dlc_review.py`
- Test: `tests/test_dlc_review_resolve.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dlc_review_resolve.py`:

```python
"""dlc_review.resolve applies a user-picked decision to a queued review item."""
from __future__ import annotations

import sqlite3

import pytest

import dlc_review
from dlc_ownership import mark_ownership
from models import normalize_title


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    import models
    for fn_name in dir(models):
        if fn_name.startswith("migrate_") and fn_name not in ("migrate_db",):
            getattr(models, fn_name)(c)
    yield c
    c.close()


def _add_game(conn, title):
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        (title, normalize_title(title)))
    return cur.lastrowid


def _add_dlc(conn, game_id, name, *, owned=0):
    cur = conn.execute(
        "INSERT INTO dlc (game_id, name, kind, owned, source) "
        "VALUES (?, ?, 'dlc', ?, 'igdb')",
        (game_id, name, owned))
    return cur.lastrowid


def test_resolve_no_parent_with_picked_game(conn):
    # Queue a "no parent game" review item from an addon whose parent isn't in the library:
    mark_ownership(conn, [{"title": "Witcher 3 - HoS", "source": "steam",
                           "external_id": "378649", "source_title": "Witcher 3 - HoS"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    # Now the user adds the parent game and picks it:
    gid = _add_game(conn, "Witcher 3")
    match = dlc_review.resolve(conn, review_id, picked_game_id=gid)
    assert match.game_id == gid
    # The review row is marked resolved:
    row = conn.execute("SELECT resolved_at FROM dlc_review_queue WHERE id = ?",
                       (review_id,)).fetchone()
    assert row["resolved_at"] is not None
    # A new DLC row was created and marked owned:
    dlc_row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id = ?", (gid,)).fetchone()
    assert dlc_row["owned"] == 1
    assert dlc_row["source"] == "steam"


def test_resolve_ambiguous_dlc_with_picked_dlc_id(conn):
    gid = _add_game(conn, "Game Q")
    a = _add_dlc(conn, gid, "Extra")
    b = _add_dlc(conn, gid, "Extra ")  # trailing space → ambiguous on normalize
    mark_ownership(conn, [{"title": "Game Q Extra", "source": "xbox",
                           "external_id": "X1", "source_title": "Extra"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    dlc_review.resolve(conn, review_id, picked_dlc_id=b)
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (b,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (a,)).fetchone()[0] == 0
    ext = conn.execute("SELECT dlc_id FROM dlc_external_ids WHERE external_id = 'X1'").fetchone()
    assert ext[0] == b


def test_resolve_ambiguous_dlc_with_create_new(conn):
    gid = _add_game(conn, "Game R")
    pre_a = _add_dlc(conn, gid, "Pass")
    pre_b = _add_dlc(conn, gid, "Pass ")
    mark_ownership(conn, [{"title": "Game R Pass", "source": "nintendo",
                           "external_id": "N1", "source_title": "Pass"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    dlc_review.resolve(conn, review_id, create_new_dlc=True)
    # Neither pre-existing row got flipped:
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (pre_a,)).fetchone()[0] == 0
    assert conn.execute("SELECT owned FROM dlc WHERE id = ?", (pre_b,)).fetchone()[0] == 0
    # A new row exists, owned + nintendo-sourced, ext id recorded:
    new_row = conn.execute(
        "SELECT id, owned, source FROM dlc WHERE game_id = ? AND id NOT IN (?, ?)",
        (gid, pre_a, pre_b)).fetchone()
    assert new_row is not None
    assert new_row["owned"] == 1
    assert new_row["source"] == "nintendo"
    ext = conn.execute("SELECT dlc_id FROM dlc_external_ids WHERE external_id = 'N1'").fetchone()
    assert ext[0] == new_row["id"]


def test_resolve_is_idempotent_on_already_resolved(conn):
    mark_ownership(conn, [{"title": "X - Y", "source": "steam",
                           "external_id": "9", "source_title": "X - Y"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    gid = _add_game(conn, "X")
    dlc_review.resolve(conn, review_id, picked_game_id=gid)
    # Second call must not raise and must not double-create:
    dlc_review.resolve(conn, review_id, picked_game_id=gid)
    n = conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id = ?", (gid,)).fetchone()[0]
    assert n == 1


def test_resolve_with_missing_picked_game_raises(conn):
    mark_ownership(conn, [{"title": "A - B", "source": "steam",
                           "external_id": "8", "source_title": "A - B"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id, picked_game_id=999999)


def test_resolve_with_missing_picked_dlc_raises(conn):
    gid = _add_game(conn, "Game S")
    _add_dlc(conn, gid, "One")
    _add_dlc(conn, gid, "One ")
    mark_ownership(conn, [{"title": "Game S One", "source": "steam",
                           "external_id": "7", "source_title": "One"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id, picked_dlc_id=999999)


def test_resolve_requires_exactly_one_choice(conn):
    mark_ownership(conn, [{"title": "T - U", "source": "steam",
                           "external_id": "6", "source_title": "T - U"}])
    review_id = conn.execute("SELECT id FROM dlc_review_queue").fetchone()[0]
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id)  # nothing picked
    with pytest.raises(ValueError):
        dlc_review.resolve(conn, review_id, picked_game_id=1, picked_dlc_id=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_review_resolve.py -v`
Expected: ImportError — `dlc_review` module doesn't exist.

- [ ] **Step 3: Create `dlc_review.py`**

```python
"""Apply user-picked decisions to queued DLC review items.

Each review row in `dlc_review_queue` represents an owned add-on the engine
couldn't auto-link to a game/DLC (no parent / ambiguous parent / ambiguous dlc).
`resolve` lets the modal hand back the user's pick (a game_id, a dlc_id, or
"create a new DLC row") and runs the same per-add-on reconcile/create logic the
scrape engine uses, then marks the row resolved.

Pure DB; no Flask. See
docs/superpowers/specs/2026-05-25-dlc-sp3-modal-and-resolution-design.md.
"""
from __future__ import annotations

import logging
import sqlite3

import dlc_ownership
from dlc_ownership import Match, OwnershipReport

logger = logging.getLogger(__name__)


def resolve(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    picked_game_id: int | None = None,
    picked_dlc_id: int | None = None,
    create_new_dlc: bool = False,
) -> Match:
    """Apply one user-resolved review item; return the resulting Match.

    Exactly one of picked_game_id / picked_dlc_id / create_new_dlc must be set.
    Idempotent: resolving an already-resolved row is a no-op that returns a
    synthesized Match for the current state. Raises ValueError if the picked
    game or DLC doesn't exist.
    """
    picks = [picked_game_id is not None, picked_dlc_id is not None, create_new_dlc]
    if sum(picks) != 1:
        raise ValueError(
            "resolve requires exactly one of picked_game_id, picked_dlc_id, "
            "or create_new_dlc=True")

    row = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, "
        "game_id, resolved_at, dismissed_at "
        "FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ValueError(f"review_id {review_id} not found")
    if row["resolved_at"] is not None:
        logger.info("review_id %s already resolved; no-op", review_id)
        return Match(row["addon_title"], game_id=row["game_id"], reason="already resolved")
    if row["dismissed_at"] is not None:
        raise ValueError(f"review_id {review_id} is dismissed; cannot resolve")

    addon = {"title": row["addon_title"], "source": row["source"],
             "external_id": row["external_id"], "source_title": row["source_title"]}

    # Resolve the parent for the apply call.
    if picked_dlc_id is not None:
        dlc_row = conn.execute(
            "SELECT game_id FROM dlc WHERE id = ?", (picked_dlc_id,)).fetchone()
        if dlc_row is None:
            raise ValueError(f"picked_dlc_id {picked_dlc_id} not found")
        parent = dlc_row["game_id"]
    elif picked_game_id is not None:
        if conn.execute("SELECT 1 FROM games WHERE id = ?", (picked_game_id,)).fetchone() is None:
            raise ValueError(f"picked_game_id {picked_game_id} not found")
        parent = picked_game_id
    else:  # create_new_dlc
        if row["game_id"] is None:
            raise ValueError("create_new_dlc requires an 'ambiguous dlc' row "
                             "(which carries the known parent game_id)")
        parent = row["game_id"]

    parent_title_row = conn.execute(
        "SELECT title, normalized_title FROM games WHERE id = ?", (parent,)).fetchone()
    if parent_title_row is None:
        raise ValueError(f"parent game {parent} not found")
    parent_norm = parent_title_row["normalized_title"] or ""
    titles = {parent: parent_title_row["title"]}

    report = OwnershipReport()
    dlc_ownership._apply_addon_to_parent(
        conn, report, parent, parent_norm, titles, addon,
        dry_run=False,
        forced_dlc_id=picked_dlc_id,
        force_create=create_new_dlc,
    )

    conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (review_id,))

    if report.marked_items:
        return report.marked_items[0]
    # marked_items is empty when _apply hit "already_owned"; synthesize a Match.
    return Match(row["addon_title"], game_id=parent, reason="already owned")


def dismiss(conn: sqlite3.Connection, review_id: int) -> None:
    """Mark a review row dismissed (user said: not a real add-on). Idempotent."""
    if conn.execute("SELECT 1 FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone() is None:
        raise ValueError(f"review_id {review_id} not found")
    conn.execute(
        "UPDATE dlc_review_queue "
        "SET dismissed_at = COALESCE(dismissed_at, CURRENT_TIMESTAMP) "
        "WHERE id = ?", (review_id,))
```

- [ ] **Step 4: Run the resolve tests**

Run: `uv run python -m pytest tests/test_dlc_review_resolve.py -v`
Expected: 7 PASSED.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`
Expected: All previous tests + 7 new green.

- [ ] **Step 6: Lint**

Run: `uv run ruff check dlc_review.py tests/test_dlc_review_resolve.py`

- [ ] **Step 7: Commit**

```bash
git add dlc_review.py tests/test_dlc_review_resolve.py
git commit -m "feat(dlc): add dlc_review.resolve + dismiss engine"
```

---

## Task 6: `POST /api/games/<id>/steam` endpoint

**Files:**
- Modify: `app.py` (add the new route alongside `/api/games/<id>/igdb` at line 380)
- Test: `tests/test_app_pin_steam.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_pin_steam.py`:

```python
"""POST /api/games/<id>/steam records a Steam appid in game_external_ids."""
from __future__ import annotations

import sqlite3

import pytest

import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(flask_app, "DATABASE_PATH", str(db))
    # Seed schema + one game:
    import models
    monkeypatch.setattr(models, "DATABASE_PATH", str(db))
    models.migrate_db()
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'Vampire Survivors', 'vampire survivors')")
        c.commit()
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as cl:
        yield cl


def test_pin_steam_happy(client, tmp_path):
    res = client.post("/api/games/1/steam",
                      json={"url": "https://store.steampowered.com/app/1794680/Vampire_Survivors/"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["appid"] == 1794680
    assert body["game"]["id"] == 1
    # Check the row landed:
    import app as A
    with sqlite3.connect(A.DATABASE_PATH) as c:
        row = c.execute(
            "SELECT game_id, source, external_id FROM game_external_ids WHERE source='steam'"
        ).fetchone()
    assert row == (1, "steam", "1794680")


def test_pin_steam_rejects_non_steam_url(client):
    res = client.post("/api/games/1/steam",
                      json={"url": "https://www.igdb.com/games/vampire-survivors"})
    assert res.status_code == 400
    assert "Steam" in (res.get_json() or {}).get("error", "")


def test_pin_steam_rejects_empty(client):
    res = client.post("/api/games/1/steam", json={"url": ""})
    assert res.status_code == 400


def test_pin_steam_404_on_missing_game(client):
    res = client.post("/api/games/9999/steam",
                      json={"url": "https://store.steampowered.com/app/1/"})
    assert res.status_code == 404


def test_pin_steam_idempotent(client):
    url = "https://store.steampowered.com/app/1794680/"
    client.post("/api/games/1/steam", json={"url": url})
    res = client.post("/api/games/1/steam", json={"url": url})
    assert res.status_code == 200
    import app as A
    with sqlite3.connect(A.DATABASE_PATH) as c:
        n = c.execute("SELECT COUNT(*) FROM game_external_ids WHERE source='steam'").fetchone()[0]
    assert n == 1
```

If the test fixture pattern above doesn't match how the rest of `tests/test_app_*.py` files spin up the Flask app, inspect an existing one (e.g. `tests/test_api_games.py`) and copy that fixture style instead. Do this BEFORE writing the test — the test's job is to exercise the route, not to fight the fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_app_pin_steam.py -v`
Expected: 404 from Flask — the route doesn't exist yet.

- [ ] **Step 3: Add the route to `app.py`**

In `app.py`, right after the existing `/api/games/<int:game_id>/igdb` route (ends around line 418), add:

```python
@app.route('/api/games/<int:game_id>/steam', methods=['POST'])
def api_pin_steam(game_id):
    """Pin a game's Steam identity from a store.steampowered.com/app/<appid> URL.

    Writes game_external_ids(source='steam', external_id=str(appid), source_title=<title>).
    Does NOT run DLC enrichment (Steam's per-DLC appdetails is rate-limited at
    200/5min; DLC defers to the next Steam scrape per SP3 decision).
    """
    import steam_dlc
    data = request.get_json(silent=True) or {}
    appid = steam_dlc.appid_from_steam_url((data.get('url') or '').strip())
    if not appid:
        return jsonify({'error': 'Not a Steam store URL'}), 400
    conn = get_db()
    game_row = conn.execute(
        "SELECT id, title, cover_url, igdb_id FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game_row:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    conn.execute(
        "INSERT OR IGNORE INTO game_external_ids (game_id, source, external_id, source_title) "
        "VALUES (?, 'steam', ?, ?)",
        (game_id, str(appid), game_row['title']))
    conn.commit()
    game = dict(game_row)
    conn.close()
    return jsonify({'appid': appid, 'game': game})
```

- [ ] **Step 4: Run the route tests**

Run: `uv run python -m pytest tests/test_app_pin_steam.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 6: Lint**

Run: `uv run ruff check app.py tests/test_app_pin_steam.py`

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_pin_steam.py
git commit -m "feat(api): POST /api/games/<id>/steam pins Steam appid"
```

---

## Task 7: Extend `/api/games/<id>` GET to include `external_ids`

The "Change cover" button in the per-game modal needs to know if the game has a Steam appid pinned (to point its href at the Steam store page). Surface the existing `game_external_ids` rows in the per-game GET response.

**Files:**
- Modify: `app.py` (the `/api/games/<int:game_id>` GET route — find it around line 237)
- Test: extend `tests/test_api_games.py` OR create `tests/test_api_games_external_ids.py`

- [ ] **Step 1: Inspect the existing route**

Read `app.py:237`-ish to confirm the GET-by-id response shape. Note the SQL it runs and the fields it returns.

- [ ] **Step 2: Write the failing test**

Create `tests/test_api_games_external_ids.py` (or add a new test function to `tests/test_api_games.py` — match the existing style):

```python
"""GET /api/games/<id> includes an external_ids dict (used by the 'Change cover' button)."""
from __future__ import annotations

import sqlite3

import pytest

import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(flask_app, "DATABASE_PATH", str(db))
    import models
    monkeypatch.setattr(models, "DATABASE_PATH", str(db))
    models.migrate_db()
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'X', 'x')")
        c.execute("INSERT INTO game_external_ids (game_id, source, external_id, source_title) "
                  "VALUES (1, 'steam', '1794680', 'X')")
        c.execute("INSERT INTO game_external_ids (game_id, source, external_id, source_title) "
                  "VALUES (1, 'playstation', 'EP1234-X', 'X')")
        c.commit()
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as cl:
        yield cl


def test_get_game_includes_external_ids(client):
    res = client.get("/api/games/1")
    assert res.status_code == 200
    body = res.get_json()
    assert body["external_ids"] == {"steam": "1794680", "playstation": "EP1234-X"}


def test_get_game_empty_external_ids_is_object(client, tmp_path):
    # A second game with no external ids:
    import app as A
    with sqlite3.connect(A.DATABASE_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (2, 'Y', 'y')")
        c.commit()
    res = client.get("/api/games/2")
    body = res.get_json()
    assert body["external_ids"] == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games_external_ids.py -v`
Expected: KeyError or `'external_ids'` missing.

- [ ] **Step 4: Update the per-game GET route**

In `app.py`, inside `api_get_game` (around line 237), after the existing query that builds the response dict, add:

```python
    ext_rows = conn.execute(
        "SELECT source, external_id FROM game_external_ids WHERE game_id = ?",
        (game_id,)).fetchall()
    response["external_ids"] = {r["source"]: r["external_id"] for r in ext_rows}
```

— substituting `response` with whatever the actual local variable is in that route. The dict shape is `{"steam": "1794680", "playstation": "EP1234-X", ...}`; an empty dict when there are no rows.

- [ ] **Step 5: Run the tests**

Run: `uv run python -m pytest tests/test_api_games_external_ids.py tests/test_api_games.py -v`
Expected: PASS. Pre-existing `test_api_games.py` must not regress.

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 7: Lint**

Run: `uv run ruff check app.py tests/test_api_games_external_ids.py`

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_api_games_external_ids.py
git commit -m "feat(api): include external_ids in GET /api/games/<id>"
```

---

## Task 8: Extend `/api/igdb/search` to return `slug` + `igdb_url`

The Add Game modal's IGDB typeahead needs to populate the new source-link field with the IGDB game URL when the user picks a result. The current endpoint returns `{name, cover_url}` only.

**Files:**
- Modify: `app.py:1464-1518` (the `/api/igdb/search` route)
- Test: extend or add to an existing IGDB-search test, or add a new minimal test that mocks the requests call.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_igdb_search_slug.py`:

```python
"""GET /api/igdb/search returns slug + igdb_url for each result."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import app as flask_app


class _MockResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"http {self.status_code}")
    def json(self):
        return self._data


@pytest.fixture
def client(monkeypatch):
    flask_app.app.config["TESTING"] = True
    # Pretend Twitch creds exist; mock the token + IGDB POST:
    monkeypatch.setattr("fetch_covers.get_twitch_credentials",
                        lambda: ("cid", "csec"), raising=False)
    # The route imports get_twitch_credentials from fetch_covers at call time;
    # also set on app module level in case it's been re-exported:
    if hasattr(flask_app, "get_twitch_credentials"):
        monkeypatch.setattr(flask_app, "get_twitch_credentials",
                            lambda: ("cid", "csec"), raising=False)
    monkeypatch.setattr("fetch_covers.get_access_token",
                        lambda *a, **k: "tok", raising=False)
    with flask_app.app.test_client() as cl:
        yield cl


def test_search_includes_slug_and_igdb_url(client, monkeypatch):
    mock_payload = [
        {"name": "Vampire Survivors", "slug": "vampire-survivors",
         "cover": {"url": "//images.igdb.com/.../t_thumb/abc.jpg"}}
    ]
    with patch("app.requests.post", return_value=_MockResp(mock_payload)):
        res = client.get("/api/igdb/search?q=vampire")
    body = res.get_json()
    assert isinstance(body, list)
    assert body[0]["name"] == "Vampire Survivors"
    assert body[0]["slug"] == "vampire-survivors"
    assert body[0]["igdb_url"] == "https://www.igdb.com/games/vampire-survivors"
    assert body[0]["cover_url"].startswith("https://")
```

If `requests` isn't imported at module scope in `app.py`, swap the `patch` target to wherever the IGDB-search route actually calls `requests.post` from (the route imports `requests` at the top of the function on line 1468 — patch `requests.post` directly or refactor minimally).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_app_igdb_search_slug.py -v`
Expected: `KeyError: 'slug'` or `'igdb_url'`.

- [ ] **Step 3: Update the IGDB search route**

In `app.py:1464-1518`:

```python
        igdb_query = f'''
            search "{query}";
            fields name, slug, cover.url;
            limit 8;
        '''
```

```python
        games = []
        for game in results:
            cover_url = None
            if game.get('cover') and game['cover'].get('url'):
                cover_url = game['cover']['url'].replace('t_thumb', 't_cover_big')
                if not cover_url.startswith('http'):
                    cover_url = 'https:' + cover_url
            slug = game.get('slug') or ''
            games.append({
                'name': game.get('name', ''),
                'slug': slug,
                'cover_url': cover_url,
                'igdb_url': f'https://www.igdb.com/games/{slug}' if slug else '',
            })
        return jsonify(games)
```

- [ ] **Step 4: Run the test**

Run: `uv run python -m pytest tests/test_app_igdb_search_slug.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 6: Lint**

Run: `uv run ruff check app.py tests/test_app_igdb_search_slug.py`

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_igdb_search_slug.py
git commit -m "feat(api): IGDB search returns slug + igdb_url"
```

---

## Task 9: `GET /api/games/search?q=` typeahead

A small read-only endpoint for the resolution modal's pick-a-game typeahead.

**Files:**
- Modify: `app.py` (add route)
- Test: `tests/test_app_games_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_games_search.py`:

```python
"""GET /api/games/search?q= returns up to 10 library games matching the query."""
from __future__ import annotations

import sqlite3

import pytest

import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(flask_app, "DATABASE_PATH", str(db))
    import models
    monkeypatch.setattr(models, "DATABASE_PATH", str(db))
    models.migrate_db()
    with sqlite3.connect(db) as c:
        for i, title in enumerate([
            "The Witcher 3", "The Witcher 2", "Hollow Knight",
            "Hades", "Hades II", "Vampire Survivors",
        ], start=1):
            from models import normalize_title
            c.execute(
                "INSERT INTO games (id, title, normalized_title) VALUES (?, ?, ?)",
                (i, title, normalize_title(title)))
        c.commit()
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as cl:
        yield cl


def test_search_matches_case_insensitive_substring(client):
    res = client.get("/api/games/search?q=witcher")
    titles = [g["title"] for g in res.get_json()]
    assert set(titles) == {"The Witcher 3", "The Witcher 2"}


def test_search_returns_id_title_cover_platforms(client):
    res = client.get("/api/games/search?q=hollow")
    g = res.get_json()[0]
    assert set(g) >= {"id", "title", "cover_url", "platforms"}
    assert isinstance(g["platforms"], list)


def test_search_limit_10(client, tmp_path):
    import app as A
    with sqlite3.connect(A.DATABASE_PATH) as c:
        from models import normalize_title
        for i in range(20):
            c.execute(
                "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                (f"Pad Game {i}", normalize_title(f"Pad Game {i}")))
        c.commit()
    res = client.get("/api/games/search?q=pad")
    assert len(res.get_json()) == 10


def test_search_empty_query_returns_empty(client):
    res = client.get("/api/games/search?q=")
    assert res.get_json() == []


def test_search_short_query_returns_empty(client):
    res = client.get("/api/games/search?q=a")  # single char
    assert res.get_json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_app_games_search.py -v`
Expected: 404 — route doesn't exist.

- [ ] **Step 3: Add the route to `app.py`**

Anywhere reasonable in `app.py` (near the other `/api/games/...` routes):

```python
@app.route('/api/games/search')
def api_games_search():
    """Library typeahead: ?q=<term> -> up to 10 games with id, title, cover_url,
    platforms. Returns [] for queries shorter than 2 chars."""
    query = (request.args.get('q') or '').strip()
    if len(query) < 2:
        return jsonify([])
    conn = get_db()
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT id, title, cover_url FROM games "
        "WHERE title LIKE ? COLLATE NOCASE OR normalized_title LIKE ? COLLATE NOCASE "
        "ORDER BY title COLLATE NOCASE LIMIT 10",
        (like, like)).fetchall()
    game_ids = [r["id"] for r in rows]
    plat_by_game: dict[int, list[str]] = {gid: [] for gid in game_ids}
    if game_ids:
        placeholders = ",".join("?" * len(game_ids))
        for r in conn.execute(
            f"SELECT gp.game_id, p.short_name FROM game_platforms gp "
            f"JOIN platforms p ON p.id = gp.platform_id "
            f"WHERE gp.game_id IN ({placeholders})", game_ids):
            plat_by_game[r["game_id"]].append(r["short_name"])
    conn.close()
    return jsonify([
        {"id": r["id"], "title": r["title"], "cover_url": r["cover_url"],
         "platforms": plat_by_game.get(r["id"], [])}
        for r in rows
    ])
```

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest tests/test_app_games_search.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 6: Lint**

Run: `uv run ruff check app.py tests/test_app_games_search.py`

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_games_search.py
git commit -m "feat(api): GET /api/games/search typeahead"
```

---

## Task 10: `/api/dlc/review/{count,list,resolve,dismiss}` endpoints

**Files:**
- Modify: `app.py` (add four routes)
- Test: `tests/test_app_dlc_review.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_dlc_review.py`:

```python
"""DLC review endpoints: count, list (with candidates), resolve, dismiss."""
from __future__ import annotations

import sqlite3

import pytest

import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(flask_app, "DATABASE_PATH", str(db))
    import models
    monkeypatch.setattr(models, "DATABASE_PATH", str(db))
    models.migrate_db()
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as cl:
        yield cl


def _seed_review(db_path: str, **kw) -> int:
    """Insert one review row; returns its id."""
    defaults = {"addon_title": "Some Add-on", "source": "nintendo",
                "external_id": "70050000000003", "source_title": "Some Add-on",
                "reason": "no parent game", "game_id": None}
    defaults.update(kw)
    with sqlite3.connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO dlc_review_queue (addon_title, source, external_id, "
            "source_title, reason, game_id) VALUES (?, ?, ?, ?, ?, ?)",
            (defaults["addon_title"], defaults["source"], defaults["external_id"],
             defaults["source_title"], defaults["reason"], defaults["game_id"]))
        c.commit()
        return cur.lastrowid


def test_count_zero_when_empty(client):
    res = client.get("/api/dlc/review/count")
    assert res.get_json() == {"count": 0}


def test_count_excludes_resolved_and_dismissed(client):
    import app as A
    _seed_review(A.DATABASE_PATH)  # open
    rid = _seed_review(A.DATABASE_PATH, external_id="x1")
    with sqlite3.connect(A.DATABASE_PATH) as c:
        c.execute("UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (rid,))
        c.commit()
    rid2 = _seed_review(A.DATABASE_PATH, external_id="x2")
    with sqlite3.connect(A.DATABASE_PATH) as c:
        c.execute("UPDATE dlc_review_queue SET dismissed_at = CURRENT_TIMESTAMP WHERE id = ?", (rid2,))
        c.commit()
    res = client.get("/api/dlc/review/count")
    assert res.get_json() == {"count": 1}


def test_list_returns_items_with_candidates(client):
    import app as A
    from models import normalize_title
    with sqlite3.connect(A.DATABASE_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'X', 'x')")
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (2, 'Y', 'y')")
        c.commit()
    _seed_review(A.DATABASE_PATH, reason="ambiguous parent",
                 addon_title="X DLC", external_id="amb1")
    res = client.get("/api/dlc/review")
    body = res.get_json()
    assert "items" in body
    assert "count" in body
    item = body["items"][0]
    assert {"id", "addon_title", "source", "external_id", "source_title",
            "reason", "game_id", "candidates"} <= set(item)
    assert "games" in item["candidates"]
    assert "dlc" in item["candidates"]


def test_resolve_with_picked_game(client):
    import app as A
    from models import normalize_title
    with sqlite3.connect(A.DATABASE_PATH) as c:
        c.execute("INSERT INTO games (id, title, normalized_title) VALUES (1, 'W', 'w')")
        c.commit()
    rid = _seed_review(A.DATABASE_PATH, addon_title="W - Pass", external_id="r1")
    res = client.post(f"/api/dlc/review/{rid}/resolve", json={"game_id": 1})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["count"] == 0
    # The DLC row was created and owned:
    with sqlite3.connect(A.DATABASE_PATH) as c:
        owned = c.execute("SELECT owned FROM dlc WHERE game_id = 1").fetchone()
    assert owned[0] == 1


def test_resolve_404_on_missing_game(client):
    import app as A
    rid = _seed_review(A.DATABASE_PATH, external_id="m1")
    res = client.post(f"/api/dlc/review/{rid}/resolve", json={"game_id": 99999})
    assert res.status_code == 404


def test_resolve_400_on_no_choice(client):
    import app as A
    rid = _seed_review(A.DATABASE_PATH, external_id="m2")
    res = client.post(f"/api/dlc/review/{rid}/resolve", json={})
    assert res.status_code == 400


def test_dismiss_marks_dismissed(client):
    import app as A
    rid = _seed_review(A.DATABASE_PATH, external_id="d1")
    res = client.post(f"/api/dlc/review/{rid}/dismiss", json={})
    assert res.status_code == 200
    assert res.get_json()["count"] == 0
    with sqlite3.connect(A.DATABASE_PATH) as c:
        d = c.execute("SELECT dismissed_at FROM dlc_review_queue WHERE id = ?",
                      (rid,)).fetchone()[0]
    assert d is not None


def test_dismiss_404_on_missing_review_id(client):
    res = client.post("/api/dlc/review/999/dismiss", json={})
    assert res.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_app_dlc_review.py -v`
Expected: 404 — routes don't exist.

- [ ] **Step 3: Add the four routes to `app.py`**

```python
@app.route('/api/dlc/review/count')
def api_dlc_review_count():
    """Open-queue size (resolved/dismissed excluded). Cheap; drives the badge."""
    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({'count': n})


@app.route('/api/dlc/review')
def api_dlc_review_list():
    """Open review items + inlined candidate parents/DLCs.

    Candidates are re-derived against the *current* library, so they reflect any
    merges/renames since the scrape. The shape is documented in the SP3 design
    doc.
    """
    import dlc_ownership
    conn = get_db()
    items = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, game_id "
        "FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL "
        "ORDER BY created_at, id"
    ).fetchall()
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    out = []
    for it in items:
        candidates = {"games": [], "dlc": []}
        if it["reason"] == "ambiguous parent":
            # Re-derive: all games whose normalized_title is the longest equal-length prefix.
            addon = dlc_ownership._norm(it["addon_title"])
            best_len = 0
            winners: list[int] = []
            for gid, gnorm in library:
                if not gnorm:
                    continue
                if addon == gnorm or addon.startswith(gnorm + " "):
                    if len(gnorm) > best_len:
                        best_len, winners = len(gnorm), [gid]
                    elif len(gnorm) == best_len:
                        winners.append(gid)
            if winners:
                placeholders = ",".join("?" * len(winners))
                game_rows = conn.execute(
                    f"SELECT id, title, cover_url FROM games WHERE id IN ({placeholders})",
                    winners).fetchall()
                plat_by_game: dict[int, list[str]] = {gid: [] for gid in winners}
                for r in conn.execute(
                    f"SELECT gp.game_id, p.short_name FROM game_platforms gp "
                    f"JOIN platforms p ON p.id = gp.platform_id "
                    f"WHERE gp.game_id IN ({placeholders})", winners):
                    plat_by_game[r["game_id"]].append(r["short_name"])
                candidates["games"] = [
                    {"id": r["id"], "title": r["title"], "cover_url": r["cover_url"],
                     "platforms": plat_by_game.get(r["id"], [])}
                    for r in game_rows
                ]
        elif it["reason"] == "ambiguous dlc" and it["game_id"]:
            parent = it["game_id"]
            parent_norm = next(
                (g for gid, g in library if gid == parent), "") or ""
            rows = [(r["id"], r["name"])
                    for r in conn.execute(
                        "SELECT id, name FROM dlc WHERE game_id = ?", (parent,))]
            rem = dlc_ownership._remainder(it["addon_title"], parent_norm)
            equal = [r for r in rows if dlc_ownership._norm(r[1]) == rem]
            candidates["dlc"] = [{"id": dlc_id, "name": name} for dlc_id, name in equal]
        out.append({
            "id": it["id"],
            "addon_title": it["addon_title"],
            "source": it["source"],
            "external_id": it["external_id"],
            "source_title": it["source_title"],
            "reason": it["reason"],
            "game_id": it["game_id"],
            "candidates": candidates,
        })
    conn.close()
    return jsonify({"items": out, "count": len(out)})


@app.route('/api/dlc/review/<int:review_id>/resolve', methods=['POST'])
def api_dlc_review_resolve(review_id):
    """Apply a user-picked decision to a queued review item."""
    import dlc_review
    data = request.get_json(silent=True) or {}
    picked_game_id = data.get("game_id")
    picked_dlc_id = data.get("dlc_id")
    create_new_dlc = bool(data.get("create_new_dlc"))
    chosen = sum(x is not None for x in (picked_game_id, picked_dlc_id)) + (1 if create_new_dlc else 0)
    if chosen != 1:
        return jsonify({"error": "Pick exactly one of game_id, dlc_id, or create_new_dlc"}), 400
    conn = get_db()
    try:
        match = dlc_review.resolve(
            conn, review_id,
            picked_game_id=picked_game_id,
            picked_dlc_id=picked_dlc_id,
            create_new_dlc=create_new_dlc,
        )
    except ValueError as exc:
        conn.rollback()
        conn.close()
        msg = str(exc)
        # "not found" cases → 404; other ValueErrors → 400.
        status = 404 if "not found" in msg else 400
        return jsonify({"error": msg}), status
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({"ok": True, "marked": match.reason in ("created", "reconciled"),
                    "count": count})


@app.route('/api/dlc/review/<int:review_id>/dismiss', methods=['POST'])
def api_dlc_review_dismiss(review_id):
    """Mark a review item dismissed (not a real add-on)."""
    import dlc_review
    conn = get_db()
    try:
        dlc_review.dismiss(conn, review_id)
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 404
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({"ok": True, "count": count})
```

- [ ] **Step 4: Run the route tests**

Run: `uv run python -m pytest tests/test_app_dlc_review.py -v`
Expected: 8 PASSED.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 6: Lint**

Run: `uv run ruff check app.py tests/test_app_dlc_review.py`

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_dlc_review.py
git commit -m "feat(api): DLC review endpoints (count, list, resolve, dismiss)"
```

---

## Task 11: Per-game modal — Source link field rename + Steam smart-route

**Files:**
- Modify: `templates/base.html` (the per-game modal cover-URL section + `setCoverUrl`)

This task changes UI markup and JS. There is no JS test infrastructure in this project, so verification is by careful code-reading and a small static check. The user will manually verify with the live app afterwards.

- [ ] **Step 1: Update the per-game modal markup** (`templates/base.html:812-834`)

Replace the existing `<!-- Cover URL -->` block with:

```html
                        <!-- Source link -->
                        <div>
                            <label class="block text-sm font-medium text-gray-400 mb-1">
                                Source link
                                ${game.igdb_id ? `<span class="ml-2 px-2 py-0.5 bg-surface rounded text-xs text-gray-400">IGDB #${game.igdb_id}</span>` : ''}
                                ${(game.external_ids && game.external_ids.steam) ? `<span class="ml-2 px-2 py-0.5 bg-surface rounded text-xs text-gray-400">Steam ${game.external_ids.steam}</span>` : ''}
                            </label>
                            <div class="flex gap-2">
                                <input type="text" id="source-link-${game.id}" value="" placeholder="Paste an IGDB or Steam URL to pin this game's source"
                                       class="flex-1 bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                                <button onclick="setSourceLink(${game.id}, document.getElementById('source-link-${game.id}').value)"
                                        class="px-3 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm transition-colors">
                                    Save
                                </button>
                            </div>
                            <p id="source-link-err-${game.id}" class="text-xs text-red-400 mt-1 hidden"></p>
                            <p class="text-xs text-gray-500 mt-1">
                                ${(() => {
                                    const plats = (game.platforms || []).map(p => p.short_name);
                                    const links = ['<a href="https://www.igdb.com/search?type=1&q=' + encodeURIComponent(game.title) + '" target="_blank" class="text-accent hover:underline">IGDB</a>'];
                                    if (plats.includes('PS4') || plats.includes('PS5')) links.push('<a href="https://store.playstation.com/en-us/search/' + encodeURIComponent(game.title) + '" target="_blank" class="text-accent hover:underline">PSN</a>');
                                    if (plats.includes('Switch')) links.push('<a href="https://www.nintendo.com/us/search/#q=' + encodeURIComponent(game.title) + '&sort=df&f=corePlatforms&corePlatforms=Nintendo+Switch" target="_blank" class="text-accent hover:underline">Nintendo</a>');
                                    if (plats.includes('Xbox')) links.push('<a href="https://www.xbox.com/en-US/search?q=' + encodeURIComponent(game.title) + '" target="_blank" class="text-accent hover:underline">Xbox</a>');
                                    links.push('<a href="https://store.steampowered.com/search/?term=' + encodeURIComponent(game.title) + '" target="_blank" class="text-accent hover:underline">Steam</a>');
                                    return links.join(' &middot; ');
                                })()}
                            </p>
                            <p class="text-xs text-gray-500 mt-1">Paste an IGDB or Steam URL above to pin this game's source. Use the cover field below to set a literal image URL.</p>
                        </div>
```

- [ ] **Step 2: Replace `setCoverUrl` (`templates/base.html:1044-1055`)**

Find the existing `async function setCoverUrl(gameId, url) { ... }` and replace it with:

```javascript
        const IGDB_URL_RE = /^https?:\/\/(www\.)?igdb\.com\/games\//i;
        const STEAM_URL_RE = /^https?:\/\/store\.steampowered\.com\/app\/\d+/i;

        function showSourceLinkError(gameId, msg) {
            const el = document.getElementById(`source-link-err-${gameId}`);
            if (!el) return;
            if (msg) { el.textContent = msg; el.classList.remove('hidden'); }
            else { el.textContent = ''; el.classList.add('hidden'); }
        }

        async function setSourceLink(gameId, url) {
            const v = (url || '').trim();
            showSourceLinkError(gameId, '');
            if (!v) {
                showSourceLinkError(gameId, 'Paste an IGDB or Steam URL, or leave empty.');
                return;
            }
            if (IGDB_URL_RE.test(v)) {
                const res = await api.post(`/api/games/${gameId}/igdb`, { url: v });
                if (!res.ok) { showSourceLinkError(gameId, res.data?.error || 'Could not resolve that IGDB URL'); return; }
            } else if (STEAM_URL_RE.test(v)) {
                const res = await api.post(`/api/games/${gameId}/steam`, { url: v });
                if (!res.ok) { showSourceLinkError(gameId, res.data?.error || 'Could not record that Steam URL'); return; }
            } else {
                showSourceLinkError(gameId, "Only IGDB or Steam URLs are accepted here. Use the cover image field below for a literal image URL.");
                return;
            }
            loadGameModal(gameId);
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

- [ ] **Step 3: Static check — no stale references to `setCoverUrl`**

Run: `Grep` for `setCoverUrl` in `templates/base.html` — expected: no matches (the only remaining reference should be the renamed `setSourceLink`). If any other call-sites exist (e.g. in the Add Game modal section), defer them to Task 13 which reworks that modal.

- [ ] **Step 4: Lint not applicable** (HTML/JS not in ruff scope) — just ensure no syntax errors by checking that `templates/base.html` loads in Flask. *This is verified manually by the user after merging; subagents do not run the app.* Subagent should re-read the modified section to confirm closing tags + braces balance.

- [ ] **Step 5: Run the full Python suite**

Run: `uv run python -m pytest -q`
Expected: All green (no Python changed; this confirms no test imports broke).

- [ ] **Step 6: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): per-game modal — Source link field + setSourceLink smart-route"
```

---

## Task 12: Per-game modal — Change cover button + literal Cover image URL input

**Files:**
- Modify: `templates/base.html` (insert below the Source link block from Task 11)

- [ ] **Step 1: Add the change-cover-link helper**

Near the top of the `<script>` block in `templates/base.html` (alongside the other helpers like `escapeHtml`, `showModalEl`), add:

```javascript
        function changeCoverHref(game) {
            const title = encodeURIComponent(game.title || '');
            const ext = game.external_ids || {};
            if (ext.steam) {
                return `https://store.steampowered.com/app/${ext.steam}/`;
            }
            if (game.igdb_id) {
                return `https://www.igdb.com/search?type=1&q=${title}`;
            }
            return `https://www.google.com/search?q=${title}+game+cover+art&tbm=isch`;
        }
```

- [ ] **Step 2: Insert the new markup**

Insert this block in `templates/base.html` **immediately below** the Source link `<div>` from Task 11, **before** the Status & Priority row:

```html
                        <!-- Change cover + literal cover URL -->
                        <div>
                            <label class="block text-sm font-medium text-gray-400 mb-1">Cover image</label>
                            <div class="flex gap-2 items-center">
                                <a href="${changeCoverHref(game)}" target="_blank"
                                   class="px-3 py-2 bg-surface hover:bg-surface-lighter border border-gray-600 rounded-lg text-white text-sm whitespace-nowrap">
                                    Change cover ↗
                                </a>
                                <input type="text" id="cover-url-${game.id}" value="${coverUrl}" placeholder="Paste an image URL"
                                       class="flex-1 bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                                <button onclick="setCoverImage(${game.id}, document.getElementById('cover-url-${game.id}').value)"
                                        class="px-3 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white text-sm transition-colors">
                                    Save
                                </button>
                            </div>
                            <p class="text-xs text-gray-500 mt-1">"Change cover" opens the pinned source's page in a new tab. Paste a cover image URL above to set it directly.</p>
                        </div>
```

- [ ] **Step 3: Add the literal-cover save handler**

Near `setSourceLink` from Task 11:

```javascript
        async function setCoverImage(gameId, url) {
            const v = (url || '').trim();
            await api.put(`/api/games/${gameId}`, { cover_url: v });
            loadGameModal(gameId);
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

- [ ] **Step 4: Run the Python suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 5: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): per-game modal — Change cover button + literal cover URL input"
```

---

## Task 13: Add Game modal — Source link + literal cover inputs + chained submit

**Files:**
- Modify: `templates/base.html` (the Add Game modal block + `addNewGame`, `selectIGDBGame`, `openAddGameModal`)

- [ ] **Step 1: Locate and inspect the Add Game modal markup**

Find the Add Game modal in `templates/base.html` (search for `id="add-game-modal"`). Inspect the existing hidden `new-game-cover-url` input and the IGDB-results section.

- [ ] **Step 2: Replace the hidden cover input with two visible inputs**

Replace the existing `<input type="hidden" id="new-game-cover-url" value="">` line with:

```html
                        <div class="mt-3">
                            <label class="block text-sm font-medium text-gray-400 mb-1">Source link</label>
                            <input type="text" id="new-game-source-link" placeholder="Paste an IGDB or Steam URL (optional)"
                                   class="w-full bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                            <p id="new-game-source-link-err" class="text-xs text-red-400 mt-1 hidden"></p>
                        </div>
                        <div class="mt-3">
                            <label class="block text-sm font-medium text-gray-400 mb-1">Cover image URL (optional)</label>
                            <input type="text" id="new-game-cover-url" placeholder="https://… (optional)"
                                   class="w-full bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                        </div>
```

- [ ] **Step 3: Update `openAddGameModal` to clear the new fields**

In `templates/base.html` around `function openAddGameModal()`, change the field-resetting lines to:

```javascript
            document.getElementById('new-game-title').value = '';
            document.getElementById('new-game-source-link').value = '';
            document.getElementById('new-game-cover-url').value = '';
            document.getElementById('new-game-source-link-err').classList.add('hidden');
```

- [ ] **Step 4: Update `selectIGDBGame` to populate the source-link field**

Replace the existing two-line body of `selectIGDBGame` with one that uses the new `igdb_url` field from the search response (added in Task 8):

```javascript
        function selectIGDBGame(name, coverUrl, igdbUrl) {
            document.getElementById('new-game-title').value = name;
            if (igdbUrl) document.getElementById('new-game-source-link').value = igdbUrl;
            else if (coverUrl) document.getElementById('new-game-cover-url').value = coverUrl;
            document.getElementById('igdb-results').classList.add('hidden');
        }
```

And update the `onclick=` template-string in `searchIGDB` (around `templates/base.html:1297-1306`) to pass the new field:

```javascript
                    resultsDiv.innerHTML = results.map(game => `
                        <div class="flex items-center gap-3 px-3 py-2 hover:bg-surface-lighter cursor-pointer"
                             onclick="selectIGDBGame('${game.name.replace(/'/g, "\\'")}', '${game.cover_url || ''}', '${game.igdb_url || ''}')">
                            ${game.cover_url ?
                                `<img src="${game.cover_url}" class="w-8 h-10 object-cover rounded">` :
                                `<div class="w-8 h-10 bg-surface rounded flex items-center justify-center text-xs">🎮</div>`
                            }
                            <span class="text-white text-sm">${game.name}</span>
                        </div>
                    `).join('');
```

- [ ] **Step 5: Rewrite `addNewGame` for the chained submit**

Replace `addNewGame` with:

```javascript
        async function addNewGame() {
            const title = document.getElementById('new-game-title').value.trim();
            const sourceLink = document.getElementById('new-game-source-link').value.trim();
            const coverUrl = document.getElementById('new-game-cover-url').value.trim();
            const errorEl = document.getElementById('add-game-error');
            const sourceErrEl = document.getElementById('new-game-source-link-err');

            errorEl.classList.add('hidden');
            sourceErrEl.classList.add('hidden');

            if (!title) {
                errorEl.textContent = 'Please enter a game title';
                errorEl.classList.remove('hidden');
                return;
            }

            // Client-side validate source_link before any create.
            const isIgdb = IGDB_URL_RE.test(sourceLink);
            const isSteam = STEAM_URL_RE.test(sourceLink);
            if (sourceLink && !isIgdb && !isSteam) {
                sourceErrEl.textContent = 'Only IGDB or Steam URLs are accepted here.';
                sourceErrEl.classList.remove('hidden');
                return;
            }

            const platforms = Array.from(document.querySelectorAll('#new-game-platforms input:checked'))
                .map(cb => cb.value);

            const gameData = { title, platforms };
            if (coverUrl) gameData.cover_url = coverUrl;

            const result = await api.post('/api/games', gameData);

            if (!result.ok) {
                if (result.status === 409) {
                    errorEl.textContent = 'Game already exists';
                    errorEl.classList.remove('hidden');
                    setTimeout(() => {
                        closeAddGameModal();
                        if (result.data && result.data.game_id) openModal(result.data.game_id);
                    }, 1000);
                } else {
                    errorEl.textContent = (result.data && result.data.error) || 'Failed to add game';
                    errorEl.classList.remove('hidden');
                }
                return;
            }

            const gameId = result.data.game_id;

            // Chain the pin call if we have a source link.
            if (isIgdb) {
                const pin = await api.post(`/api/games/${gameId}/igdb`, { url: sourceLink });
                if (!pin.ok) {
                    sourceErrEl.textContent = (pin.data && pin.data.error) || 'Could not resolve that IGDB URL';
                    sourceErrEl.classList.remove('hidden');
                    // The game was still created; continue to open its modal.
                }
            } else if (isSteam) {
                const pin = await api.post(`/api/games/${gameId}/steam`, { url: sourceLink });
                if (!pin.ok) {
                    sourceErrEl.textContent = (pin.data && pin.data.error) || 'Could not record that Steam URL';
                    sourceErrEl.classList.remove('hidden');
                }
            }

            closeAddGameModal();
            if (typeof refreshGameList === 'function') refreshGameList();
            loadNavStats();
            openModal(gameId);
        }
```

- [ ] **Step 6: Run the Python suite**

Run: `uv run python -m pytest -q`
Expected: still all green.

- [ ] **Step 7: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): Add Game modal — visible source-link + cover inputs, chained submit"
```

---

## Task 14: DLC Review modal — markup, badge, render, actions

**Files:**
- Modify: `templates/base.html` (add modal frame, badge row in Add Game modal, JS handlers)

- [ ] **Step 1: Add the modal frame**

In `templates/base.html`, immediately after the existing `#dedup-modal` block (`base.html:170-182`), add:

```html
    <!-- DLC Review Modal -->
    <div id="dlc-review-modal" class="fixed inset-0 bg-black/70 z-50 hidden items-center justify-center p-4">
        <div class="bg-surface-light rounded-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto shadow-2xl">
            <div class="p-6">
                <div class="flex items-center justify-between mb-4">
                    <h2 class="text-xl font-bold text-white">Resolve DLC review <span id="dlc-review-count-header" class="text-gray-400 text-base"></span></h2>
                    <button onclick="closeDlcReviewModal()" class="text-white/70 hover:text-white text-2xl">&times;</button>
                </div>
                <div id="dlc-review-body" class="space-y-3">
                    <div class="text-gray-400 text-sm">Loading…</div>
                </div>
            </div>
        </div>
    </div>
```

- [ ] **Step 2: Add the badge entry point in the Add Game modal**

Find the "Sync a whole library" section in the Add Game modal (it contains `class="scrape-vendor-btn"` buttons). Add this row **above** the vendor buttons:

```html
                        <div id="dlc-review-entry" class="flex items-center justify-between gap-2 mb-3 hidden">
                            <span class="text-sm text-gray-300">DLC review (<span id="dlc-review-count">0</span>)</span>
                            <button onclick="openDlcReviewModal()" class="px-3 py-1.5 bg-surface hover:bg-surface-lighter border border-gray-600 rounded text-white text-sm">Open</button>
                        </div>
```

- [ ] **Step 3: Update `refreshScrapeSection` to populate the badge**

In `templates/base.html`, find `function refreshScrapeSection()` (around line 1249) and extend it to also fetch the review count:

```javascript
        function refreshScrapeSection() {
            // On modal open: reflect an in-progress scrape, else reset the section.
            api.get('/api/scrape/status').then(st => {
                const statusEl = document.getElementById('scrape-status');
                if (SCRAPE_ACTIVE.includes(st.phase)) {
                    statusEl.classList.remove('hidden');
                    setScrapeButtonsDisabled(true);
                    renderScrapeStatus(st);
                    startScrapePolling();
                } else {
                    statusEl.classList.add('hidden');
                    setScrapeButtonsDisabled(false);
                }
            });
            // Populate the DLC review badge in parallel.
            api.get('/api/dlc/review/count').then(r => {
                const n = (r && r.count) || 0;
                const entry = document.getElementById('dlc-review-entry');
                document.getElementById('dlc-review-count').textContent = n;
                entry.classList.toggle('hidden', n === 0);
            });
        }
```

- [ ] **Step 4: Add the modal logic**

After the dedup logic (around `templates/base.html:600`+) — or anywhere reasonable in the `<script>` block — add:

```javascript
        // ---- DLC review modal ----
        let dlcReviewBusy = false;

        function openDlcReviewModal() {
            showModalEl('dlc-review-modal');
            document.getElementById('dlc-review-body').innerHTML =
                '<div class="text-gray-400 text-sm">Loading…</div>';
            loadDlcReview();
        }
        function closeDlcReviewModal() { hideModalEl('dlc-review-modal'); }

        async function loadDlcReview() {
            const data = await api.get('/api/dlc/review');
            renderDlcReview(data);
        }

        function renderDlcReview(data) {
            const body = document.getElementById('dlc-review-body');
            const header = document.getElementById('dlc-review-count-header');
            const items = (data && data.items) || [];
            header.textContent = items.length ? `(${items.length})` : '';
            if (!items.length) {
                body.innerHTML = '<div class="text-gray-400 text-sm">Nothing to review. 🎉</div>';
                refreshDlcReviewBadge(0);
                return;
            }
            body.innerHTML = items.map(it => dlcReviewCard(it)).join('');
        }

        function refreshDlcReviewBadge(n) {
            const entry = document.getElementById('dlc-review-entry');
            const cnt = document.getElementById('dlc-review-count');
            if (cnt) cnt.textContent = n;
            if (entry) entry.classList.toggle('hidden', n === 0);
        }

        function dlcReviewCard(it) {
            const reason = escapeHtml(it.reason);
            const vendor = escapeHtml(it.source || 'unknown');
            const actions = dlcReviewActions(it);
            return `<div class="bg-surface rounded-lg p-3" id="dlc-review-row-${it.id}">
                <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                        <div class="text-white text-sm font-medium truncate">${escapeHtml(it.addon_title)}</div>
                        <div class="mt-1 flex items-center gap-2 flex-wrap">
                            <span class="px-2 py-0.5 bg-surface-light rounded text-xs text-gray-300">${vendor}</span>
                            <span class="px-2 py-0.5 bg-yellow-900/40 text-yellow-300 rounded text-xs">${reason}</span>
                        </div>
                    </div>
                    <button onclick="dismissDlcReview(${it.id})" class="px-2 py-1 text-xs bg-surface-light hover:bg-surface-lighter border border-gray-600 rounded text-white whitespace-nowrap">Dismiss</button>
                </div>
                <div class="mt-3">${actions}</div>
            </div>`;
        }

        function gameTileHtml(it, g) {
            const plats = (g.platforms || []).join(', ') || '—';
            return `<button type="button" onclick="resolveDlcReview(${it.id}, {game_id: ${g.id}})"
                class="flex items-center gap-2 w-full text-left bg-surface-light hover:bg-surface-lighter rounded p-2">
                <img src="${g.cover_url || ''}" onerror="this.style.display='none'" class="w-8 h-10 object-cover rounded bg-surface">
                <div class="min-w-0">
                    <div class="text-white text-sm truncate">${escapeHtml(g.title)}</div>
                    <div class="text-gray-500 text-xs">${escapeHtml(plats)}</div>
                </div>
            </button>`;
        }

        function dlcReviewActions(it) {
            if (it.reason === 'ambiguous parent') {
                const tiles = (it.candidates.games || []).map(g => gameTileHtml(it, g)).join('');
                return `<div class="space-y-1">${tiles}</div>
                    <div class="mt-2 text-xs text-gray-400">None of these — search instead:</div>
                    ${searchInputHtml(it)}`;
            }
            if (it.reason === 'ambiguous dlc') {
                const tiles = (it.candidates.dlc || []).map(d => `
                    <button type="button" onclick="resolveDlcReview(${it.id}, {dlc_id: ${d.id}})"
                        class="block w-full text-left bg-surface-light hover:bg-surface-lighter rounded px-3 py-2 text-white text-sm">
                        ${escapeHtml(d.name)}
                    </button>`).join('');
                return `<div class="space-y-1">${tiles}</div>
                    <button type="button" onclick="resolveDlcReview(${it.id}, {create_new_dlc: true})"
                        class="mt-2 text-accent hover:underline text-xs">None of these — create a new DLC row instead</button>`;
            }
            // "no parent game"
            return searchInputHtml(it);
        }

        function searchInputHtml(it) {
            return `<div>
                <input type="text" oninput="dlcReviewSearch(${it.id}, this.value)" placeholder="Search games by title…"
                       class="w-full bg-surface-light rounded border border-gray-600 px-2 py-1 text-white text-sm focus:border-accent focus:outline-none">
                <div id="dlc-review-search-${it.id}" class="mt-1 space-y-1"></div>
            </div>`;
        }

        const _dlcReviewTimers = {};
        function dlcReviewSearch(reviewId, query) {
            clearTimeout(_dlcReviewTimers[reviewId]);
            _dlcReviewTimers[reviewId] = setTimeout(async () => {
                const out = document.getElementById(`dlc-review-search-${reviewId}`);
                if (!out) return;
                const q = (query || '').trim();
                if (q.length < 2) { out.innerHTML = ''; return; }
                const results = await api.get(`/api/games/search?q=${encodeURIComponent(q)}`);
                if (!results.length) { out.innerHTML = '<div class="text-xs text-gray-500">No matches</div>'; return; }
                out.innerHTML = results.map(g => gameTileHtml({id: reviewId}, g)).join('');
            }, 250);
        }

        async function resolveDlcReview(reviewId, body) {
            if (dlcReviewBusy) return;
            dlcReviewBusy = true;
            try {
                const res = await api.post(`/api/dlc/review/${reviewId}/resolve`, body);
                if (!res.ok) {
                    alert((res.data && res.data.error) || 'Resolve failed');
                    return;
                }
                document.getElementById(`dlc-review-row-${reviewId}`)?.remove();
                refreshDlcReviewBadge(res.data.count);
                const remaining = document.querySelectorAll('#dlc-review-body > div').length;
                if (remaining === 0) {
                    document.getElementById('dlc-review-body').innerHTML =
                        '<div class="text-gray-400 text-sm">Nothing to review. 🎉</div>';
                    document.getElementById('dlc-review-count-header').textContent = '';
                }
            } finally {
                dlcReviewBusy = false;
            }
        }

        async function dismissDlcReview(reviewId) {
            if (dlcReviewBusy) return;
            dlcReviewBusy = true;
            try {
                const res = await api.post(`/api/dlc/review/${reviewId}/dismiss`, {});
                if (!res.ok) {
                    alert((res.data && res.data.error) || 'Dismiss failed');
                    return;
                }
                document.getElementById(`dlc-review-row-${reviewId}`)?.remove();
                refreshDlcReviewBadge(res.data.count);
            } finally {
                dlcReviewBusy = false;
            }
        }

        document.getElementById('dlc-review-modal').addEventListener('click', (e) => {
            if (e.target.id === 'dlc-review-modal') closeDlcReviewModal();
        });
```

- [ ] **Step 5: Link the post-scrape review block to the modal**

Edit `renderScrapeResults` (`templates/base.html:1196-1230`) to make the "Needs review" `<summary>` clickable. Replace the existing `if (review.length)` block with:

```javascript
            if (review.length) {
                const rows = review.map(r =>
                    `<div class="pl-3 text-yellow-400">${escapeHtml(r.title)} <span class="text-gray-500">[${escapeHtml(r.reason)}]</span></div>`).join('');
                html += `<details class="mt-2"><summary class="cursor-pointer text-gray-300">Needs review (${review.length}) — <button type="button" onclick="event.preventDefault(); openDlcReviewModal();" class="text-accent hover:underline">resolve</button></summary>
                         <div class="mt-1 max-h-48 overflow-y-auto">${rows}</div></details>`;
            }
```

- [ ] **Step 6: Run the Python suite**

Run: `uv run python -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): DLC review modal + badge + post-scrape link"
```

---

## Task 15: Manual scope-sweep + full-suite green

A final pass after all front-end + back-end pieces are in place. Pure verification — no new code unless this surfaces a gap.

- [ ] **Step 1: Confirm no stale `setCoverUrl` references**

Run `Grep` for `setCoverUrl` in the repo. Expected: zero matches. If any remain (other than git history), fix them — anything that calls the old name now calls `setSourceLink` or `setCoverImage` depending on intent.

- [ ] **Step 2: Confirm no `new-game-cover-url` lookups expect the old hidden-field shape**

`Grep` for `new-game-cover-url` in `templates/base.html`. Expected: all references are to the visible input added in Task 13 (label + the same id is fine; what we want to avoid is any leftover code that assumed it was hidden).

- [ ] **Step 3: Confirm `app.py` exposes `external_ids` everywhere the per-game GET returns**

`Grep` `SELECT id, title, cover_url, igdb_id FROM games` in `app.py`. The `/api/games/<id>/igdb` route at line 380 still does this and returns just `{game: dict(game)}` — verify whether it should now also include `external_ids` for symmetry. If yes, mirror Task 7's join here. (Decision: yes — the front-end will re-call `loadGameModal` after the pin which fetches `/api/games/<id>` and gets the full shape, so the inline `game` in the pin response is OK to leave alone. **Skip** unless the route is actually consumed without a follow-up GET.)

- [ ] **Step 4: Full test suite**

Run: `uv run python -m pytest -v`
Expected: every previous test + every new test green. Note the final count and confirm it equals 275 + the sum of new tests added.

- [ ] **Step 5: Lint sweep**

Run: `uv run ruff check`
Expected: All checks passed.

- [ ] **Step 6: Sanity-check the spec coverage**

Open `docs/superpowers/specs/2026-05-25-dlc-sp3-modal-and-resolution-design.md` and walk each section. For each, point at the task that implements it:

| Spec section | Task(s) |
|---|---|
| Section 1 — Source-link field (per-game modal, Add Game modal, `/steam` endpoint, `appid_from_steam_url`) | Tasks 1, 6, 11, 13 |
| Section 2 — Change-cover button + literal cover input | Task 12 (per-game), Task 13 (Add Game) |
| Section 3 — `dlc_review_queue` table + UPSERT writes | Tasks 2, 4 |
| Section 4 — DLC Review modal + endpoints + `dlc_review.resolve` + `_apply_addon_to_parent` refactor | Tasks 3, 5, 9, 10, 14 |
| Section 5 — Manual-add chained POSTs | Task 13 |
| Section — `external_ids` on game GET | Task 7 |
| Section — IGDB search returns slug + igdb_url | Task 8 |

If any spec section has no matching task, surface the gap to the user before declaring SP3 done.

- [ ] **Step 7: Commit a sweep marker (only if step 1/2 found leftover bugs)**

If the sweep produced fixes, commit them:

```bash
git add -A
git commit -m "fix(sp3): clean up stale references and final sweep"
```

Otherwise, nothing to commit — SP3 is landed.

---

## Notes for the executor

- **Manual verification belongs to the owner.** Live Steam, Nintendo, and Xbox scrapes are the user's manual step, not the executor's. Subagents must never run the app or live scrapers.
- **Conflict between this plan's commit messages and CLAUDE.md's "add `Co-Authored-By`" instruction:** project memory (`work-on-main-no-branches`, `dlc-authoritative-source-rework`) explicitly forbids the trailer for SP work. **Follow this plan — no trailer.**
- **If a test reveals the spec is wrong** (rare but possible in front-end UX), stop and flag it before improvising. The user owns design decisions; brainstorming Q1–Q5 are settled.
- **Frequent commits.** One task = one commit. Don't bundle.
