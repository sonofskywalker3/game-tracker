# DLC Vendor-Source Foundation (SP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scraped owned add-ons actually mark owned for Nintendo & Xbox by replacing the IGDB-name-only matching engine with a vendor-authoritative, id-aware one that records verified owned add-ons as real DLC rows.

**Architecture:** A new `dlc_external_ids` child table (mirroring `game_external_ids`) stores each DLC row's vendor add-on id. `dlc_ownership.mark_ownership` is rewritten: resolve parent by name-prefix → on a confident parent reconcile by vendor id, then by name-equality (recording the id), else create a vendor-sourced owned row; uncertain parents go to a review list. Ownership is 0→1-only and idempotent. The CLI (`import_scraped`) and web pipeline (`scrape_service`) are updated to the new report shape; the old containment-"hold" path and `--apply-flagged-ownership` flag are removed.

**Tech Stack:** Python 3, SQLite (stdlib `sqlite3`), pytest. Run tests with `uv run python -m pytest`; lint with `uv run ruff check` (NO `ruff format` — match the hand-aligned style).

**Spec:** `docs/superpowers/specs/2026-05-25-dlc-vendor-source-foundation-design.md`

**Conventions:** Work on `main`; conventional commits, NO co-author trailer. Never commit `games.db`/`.recon/`/`config.json`/etc. The engine and migration are pure / temp-DB testable; the suite runs offline.

---

### Task 1: Migration — `dlc_external_ids` table

**Files:**
- Create: `tests/test_dlc_external_ids_migration.py`
- Modify: `models.py` (add `migrate_dlc_external_ids`; call it in `migrate_db`; add the table to `init_db`'s `executescript`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dlc_external_ids_migration.py` (mirrors `tests/test_external_ids_migration.py`):

```python
import sqlite3

import pytest

from models import migrate_dlc_external_ids


def _conn_with_dlc():
    """A DB with a dlc table but no dlc_external_ids (pre-migration shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE dlc (id INTEGER PRIMARY KEY, name TEXT)")
    return conn


def test_migration_creates_table():
    conn = _conn_with_dlc()
    migrate_dlc_external_ids(conn)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(dlc_external_ids)").fetchall()}
    assert cols == {"id", "dlc_id", "source", "external_id", "source_title", "created_at"}


def test_migration_is_idempotent():
    conn = _conn_with_dlc()
    migrate_dlc_external_ids(conn)
    migrate_dlc_external_ids(conn)  # second run must not raise
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(dlc_external_ids)").fetchall()}
    assert "idx_dlc_ext_dlc" in indexes


def test_source_external_id_is_unique():
    conn = _conn_with_dlc()
    migrate_dlc_external_ids(conn)
    conn.execute("INSERT INTO dlc (id, name) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO dlc_external_ids (dlc_id, source, external_id) VALUES (1, 'nintendo', 'N1')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dlc_external_ids (dlc_id, source, external_id) VALUES (1, 'nintendo', 'N1')")


def test_fk_to_dlc_is_enforced():
    conn = _conn_with_dlc()
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_dlc_external_ids(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO dlc_external_ids (dlc_id, source, external_id) VALUES (999, 'xbox', 'X1')")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_external_ids_migration.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_dlc_external_ids' from 'models'`.

- [ ] **Step 3: Add the migration function to `models.py`**

Add this function immediately after `migrate_dlc` (after `models.py:442`):

```python
def migrate_dlc_external_ids(conn: sqlite3.Connection) -> None:
    """Create the dlc_external_ids table if missing. Idempotent.

    One DLC carries many rows here (one per store); identity is
    (source, external_id), so a re-scrape matches an owned add-on by its stable
    vendor id and the per-game deep-fetch (later SPs) can reconcile owned rows to
    catalogue rows by id."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dlc_external_ids (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dlc_id       INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id),
            FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dlc_ext_dlc ON dlc_external_ids(dlc_id);
    """)
    conn.commit()
```

- [ ] **Step 4: Call it from `migrate_db`**

In `migrate_db` (`models.py`), after the existing `migrate_dlc(conn)` call (`models.py:489`), add:

```python
    # Add the dlc_external_ids table (vendor add-on ids; DLC source-of-truth rework)
    migrate_dlc_external_ids(conn)
```

- [ ] **Step 5: Add the table to `init_db` so fresh DBs (incl. test temp DBs) have it**

In `init_db`'s `executescript`, immediately after the `idx_dlc_game` index line (`models.py:187`), add inside the same SQL string:

```sql
        -- Vendor add-on ids for DLC rows (one DLC may carry ids from several stores)
        CREATE TABLE IF NOT EXISTS dlc_external_ids (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dlc_id       INTEGER NOT NULL,
            source       TEXT    NOT NULL,
            external_id  TEXT    NOT NULL,
            source_title TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id),
            FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_dlc_ext_dlc ON dlc_external_ids(dlc_id);
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_dlc_external_ids_migration.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add models.py tests/test_dlc_external_ids_migration.py
git commit -m "feat: dlc_external_ids table for vendor add-on ids"
```

---

### Task 2: Rewrite the ownership engine (id-first reconcile/create)

This replaces the name-only `classify`/`match_dlc`/containment engine with the id-first reconcile/create engine. The whole module and its test file are rewritten.

**Files:**
- Modify (rewrite): `dlc_ownership.py`
- Modify (rewrite): `tests/test_dlc_ownership.py`

- [ ] **Step 1: Replace the test file with the new behavior**

Overwrite `tests/test_dlc_ownership.py` with:

```python
import dlc_ownership as own
import models


def _lib(*titles):
    """Build a [(game_id, normalized_title)] library from display titles."""
    return [(i + 1, models.normalize_title(models.clean_title(t))) for i, t in enumerate(titles)]


# --- parent_of (unchanged behavior) ---

def test_parent_of_exact_prefix():
    lib = _lib("The Witcher 3: Wild Hunt", "Other Game")
    assert own.parent_of("The Witcher 3: Wild Hunt - Hearts of Stone", lib) == 1


def test_parent_of_longest_prefix_wins():
    lib = _lib("Final Fantasy", "Final Fantasy XV")
    assert own.parent_of("Final Fantasy XV - Episode Ardyn", lib) == 2


def test_parent_of_no_prefix_is_none():
    lib = _lib("Hades", "Celeste")
    assert own.parent_of("Stardew Valley - Some Pack", lib) is None


def test_parent_of_cross_game_tie_is_ambiguous():
    lib = [(1, "spirit"), (2, "spirit")]
    assert own.parent_of("Spirit Extra Pack", lib) is own.AMBIGUOUS


def test_parent_of_exact_title_match():
    lib = _lib("Celeste")
    assert own.parent_of("Celeste", lib) == 1


def test_remainder_strips_parent_prefix():
    assert own._remainder("The Witcher 3 - Hearts of Stone", "the witcher 3") == "hearts of stone"
    assert own._remainder("Celeste", "celeste") == ""


# --- match_equal (equality only; no containment) ---

def test_match_equal_single():
    rows = [(10, "Hearts of Stone"), (11, "Blood and Wine")]
    assert own.match_equal("hearts of stone", rows) == 10


def test_match_equal_multiple_is_ambiguous():
    rows = [(10, "Season Pass"), (11, "Season Pass")]
    assert own.match_equal("season pass", rows) is own.AMBIGUOUS


def test_match_equal_none_on_containment():
    rows = [(10, "Hearts of Stone")]
    assert own.match_equal("hearts of stone expansion", rows) is None


def test_match_equal_empty():
    assert own.match_equal("", [(10, "X")]) is None


# --- _clean_remainder (display name for a created row) ---

def test_clean_remainder_strips_parent_prefix():
    assert own._clean_remainder("The Witcher 3: Wild Hunt - Hearts of Stone",
                                "The Witcher 3: Wild Hunt") == "Hearts of Stone"


def test_clean_remainder_falls_back_to_full_title():
    assert own._clean_remainder("Some Bundle Pack", "The Witcher 3") == "Some Bundle Pack"


# --- mark_ownership engine (temp DB) ---

def _seed(conn, title="The Witcher 3: Wild Hunt", dlc_names=("Hearts of Stone",)):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    for name in dlc_names:
        conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, ?, 'igdb')", (gid, name))
    return gid


def _addon(title, source="nintendo", external_id="A1"):
    return {"title": title, "source": source, "external_id": external_id, "kind": "addon"}


def test_reconcile_by_name_equality_flips_and_records_id(temp_db):
    conn = models.get_db()
    _seed(conn)  # has igdb "Hearts of Stone"
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Hearts of Stone")])
    conn.commit()
    assert rep.marked == 1 and rep.reconciled == 1 and rep.created == 0
    assert conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0] == 1
    row = conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='nintendo' AND external_id='A1'").fetchone()
    assert row is not None
    conn.close()


def test_create_when_no_matching_row(temp_db):
    conn = models.get_db()
    gid = _seed(conn, dlc_names=())  # game exists, no dlc rows (IGDB-missing case)
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Ultimate Pack")])
    conn.commit()
    assert rep.created == 1 and rep.marked == 1 and rep.reconciled == 0
    row = conn.execute("SELECT name, owned, source FROM dlc WHERE game_id=?", (gid,)).fetchone()
    assert row["name"] == "Ultimate Pack" and row["owned"] == 1 and row["source"] == "nintendo"
    ext = conn.execute("SELECT external_id FROM dlc_external_ids WHERE source='nintendo'").fetchone()
    assert ext["external_id"] == "A1"
    conn.close()


def test_idempotent_by_id_on_rerun(temp_db):
    conn = models.get_db()
    _seed(conn, dlc_names=())
    conn.commit()
    addon = _addon("The Witcher 3: Wild Hunt - Ultimate Pack")
    own.mark_ownership(conn, [addon])
    conn.commit()
    rep = own.mark_ownership(conn, [addon])
    conn.commit()
    assert rep.created == 0 and rep.marked == 0 and rep.already_owned == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 1
    conn.close()


def test_uncertain_parent_goes_to_review_no_write(temp_db):
    conn = models.get_db()
    _seed(conn)
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("Totally Unknown Game - Bonus", external_id="Z9")])
    assert rep.marked == 0 and len(rep.review) == 1 and rep.review[0].reason == "no parent game"
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 0
    assert conn.execute("SELECT owned FROM dlc").fetchone()[0] == 0
    conn.close()


def test_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _seed(conn, dlc_names=())
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Ultimate Pack")],
                             dry_run=True)
    assert rep.created == 1 and rep.marked == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 0
    conn.close()


def test_already_owned_not_remarked(temp_db):
    conn = models.get_db()
    gid = _seed(conn)
    conn.execute("UPDATE dlc SET owned=1 WHERE game_id=?", (gid,))  # already owned
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Hearts of Stone")])
    conn.commit()
    assert rep.marked == 0 and rep.already_owned == 1
    conn.close()


def test_marked_items_for_result_list(temp_db):
    conn = models.get_db()
    _seed(conn, dlc_names=())
    conn.commit()
    rep = own.mark_ownership(conn, [_addon("The Witcher 3: Wild Hunt - Ultimate Pack")])
    conn.commit()
    assert len(rep.marked_items) == 1
    assert rep.marked_items[0].dlc_id is not None
    conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_dlc_ownership.py -v`
Expected: FAIL — `AttributeError: module 'dlc_ownership' has no attribute 'match_equal'` (and `_clean_remainder`), plus `OwnershipReport` field errors.

- [ ] **Step 3: Rewrite `dlc_ownership.py`**

Overwrite `dlc_ownership.py` with:

```python
"""Match scraped owned add-ons to DLC rows and flip owned (vendor = source of truth).

Parent is resolved by longest normalized-title prefix (the only link a
purchased-library scrape gives -- no vendor exposes a parent pointer). On a
*confident* parent the owned add-on is reconciled to an existing dlc row by vendor
id, then by normalized-name equality (recording the vendor id), else a new
vendor-sourced dlc row is created (owned=1). Every vendor id is recorded in
dlc_external_ids so re-scrapes match by id and the later per-game deep-fetch can
reconcile by id. Uncertain parents are reported for review; nothing is written for
them. Ownership is only ever set 0 -> 1; the pass is idempotent. See
docs/superpowers/specs/2026-05-25-dlc-vendor-source-foundation-design.md.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

import models

logger = logging.getLogger(__name__)

# Sentinel: more than one equally-plausible match (parent or dlc). Distinct from
# None ("no match at all").
AMBIGUOUS = "__ambiguous__"


def _norm(title: str | None) -> str:
    """Normalized match key (normalize_title(clean_title(...)))."""
    return models.normalize_title(models.clean_title(title or ""))


def parent_of(addon_title: str, library: list[tuple[int, str]]) -> int | str | None:
    """Resolve an add-on's parent game by longest normalized-title prefix.

    `library` is [(game_id, normalized_title)]. Returns the game_id, None (no
    prefix matched), or AMBIGUOUS (the longest match ties across different
    game_ids). A normalized game title matches when it equals the normalized
    add-on title or is a whole-word prefix of it.
    """
    addon = _norm(addon_title)
    if not addon:
        return None
    best_len = 0
    winners: set[int] = set()
    for game_id, gnorm in library:
        if not gnorm:
            continue
        if addon == gnorm or addon.startswith(gnorm + " "):
            if len(gnorm) > best_len:
                best_len, winners = len(gnorm), {game_id}
            elif len(gnorm) == best_len:
                winners.add(game_id)
    if not winners:
        return None
    if len(winners) > 1:
        return AMBIGUOUS
    return next(iter(winners))


def _remainder(addon_title: str, parent_norm: str) -> str:
    """The normalized add-on title with the parent's normalized prefix removed."""
    addon = _norm(addon_title)
    if addon == parent_norm:
        return ""
    prefix = parent_norm + " "
    if addon.startswith(prefix):
        return addon[len(prefix):]
    return addon


def match_equal(remainder: str, dlc_rows: list[tuple[int, str]]) -> int | str | None:
    """Match an add-on remainder to a parent's dlc row by normalized-name equality.

    Returns a dlc_id, AMBIGUOUS (several equal), or None. `dlc_rows` is
    [(dlc_id, name)]. Equality only -- containment is intentionally not used (it
    produced false matches in the old engine).
    """
    rem = (remainder or "").strip()
    if not rem:
        return None
    equal = [dlc_id for dlc_id, name in dlc_rows if models.normalize_title(name) == rem]
    if len(equal) == 1:
        return equal[0]
    if len(equal) > 1:
        return AMBIGUOUS
    return None


def _clean_remainder(addon_title: str, parent_title: str) -> str:
    """Display name for a created DLC row: the add-on's original title with the
    parent's display-title prefix and any joining separator stripped; falls back to
    the full add-on title when the parent is not a clean prefix."""
    addon = (addon_title or "").strip()
    parent = (parent_title or "").strip()
    if parent and addon.lower().startswith(parent.lower()):
        rem = addon[len(parent):].lstrip(" -–:|").strip()
        if rem:
            return rem
    return addon


@dataclass
class Match:
    """One add-on's outcome: a newly-owned row (dlc_id set) or a review item."""
    addon_title: str
    game_id: int | None = None
    dlc_id: int | None = None
    reason: str = ""


@dataclass
class OwnershipReport:
    """Outcome counts + the rows newly owned and the add-ons needing review."""
    created: int = 0
    reconciled: int = 0
    already_owned: int = 0
    marked: int = 0  # created + reconciled (rows newly set owned this run)
    marked_items: list[Match] = field(default_factory=list)
    review: list[Match] = field(default_factory=list)


def _addon_field(addon, key: str):
    """Read a field off a scrape dict or a ScrapedGame-like object."""
    return addon.get(key) if isinstance(addon, dict) else getattr(addon, key, None)


def _record_ext_id(conn: sqlite3.Connection, dlc_id: int, source: str | None,
                   ext: str | None, source_title: str | None) -> None:
    """Record a vendor add-on id for a dlc row (no-op without source+id)."""
    if source and ext:
        conn.execute(
            "INSERT OR IGNORE INTO dlc_external_ids "
            "(dlc_id, source, external_id, source_title) VALUES (?, ?, ?, ?)",
            (dlc_id, source, ext, source_title))


def _flip(conn: sqlite3.Connection, report: OwnershipReport, dlc_id: int, title: str,
          parent: int, dry_run: bool) -> None:
    """Set an existing dlc row owned (0 -> 1). Idempotent: an already-owned row is
    counted, not re-marked."""
    owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (dlc_id,)).fetchone()[0]
    if owned:
        report.already_owned += 1
        return
    report.marked += 1
    report.reconciled += 1
    report.marked_items.append(Match(title, game_id=parent, dlc_id=dlc_id, reason="reconciled"))
    if not dry_run:
        conn.execute("UPDATE dlc SET owned = 1 WHERE id = ?", (dlc_id,))


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
        source = _addon_field(addon, "source")
        ext = _addon_field(addon, "external_id")
        source_title = _addon_field(addon, "source_title") or title

        parent = parent_of(title, library)
        if parent is None:
            report.review.append(Match(title, reason="no parent game"))
            continue
        if parent is AMBIGUOUS:
            report.review.append(Match(title, reason="ambiguous parent"))
            continue

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
            continue

        # (b) reconcile by normalized-name equality
        parent_norm = next(gnorm for gid, gnorm in library if gid == parent)
        rows = [(r["id"], r["name"])
                for r in conn.execute("SELECT id, name FROM dlc WHERE game_id = ?", (parent,))]
        match = match_equal(_remainder(title, parent_norm), rows)
        if match is AMBIGUOUS:
            report.review.append(Match(title, game_id=parent, reason="ambiguous dlc"))
            continue
        if match is not None:
            if not dry_run:
                _record_ext_id(conn, match, source, ext, source_title)
            _flip(conn, report, match, title, parent, dry_run)
            continue

        # (c) create a vendor-sourced owned row
        report.created += 1
        report.marked += 1
        if dry_run:
            report.marked_items.append(Match(title, game_id=parent, reason="created"))
            continue
        name = _clean_remainder(title, titles.get(parent, ""))
        cur = conn.execute(
            "INSERT INTO dlc (game_id, name, kind, owned, source) VALUES (?, ?, 'dlc', 1, ?)",
            (parent, name, source or "vendor"))
        new_id = cur.lastrowid
        _record_ext_id(conn, new_id, source, ext, source_title)
        report.marked_items.append(Match(title, game_id=parent, dlc_id=new_id, reason="created"))
    return report
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_dlc_ownership.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add dlc_ownership.py tests/test_dlc_ownership.py
git commit -m "feat: id-first DLC ownership engine (reconcile by id/name, else create)"
```

---

### Task 3: CLI integration — `import_scraped`

Update the CLI reporting to the new `OwnershipReport` shape and remove the obsolete `--apply-flagged-ownership` flag (the "hold" category it applied no longer exists).

**Files:**
- Modify: `import_scraped.py` (`_log_ownership` at `import_scraped.py:564-574`; argparse + call at `import_scraped.py:597-598` and `:658-663`)
- Verify: `tests/test_import_scraped.py` (existing ownership tests must still pass unchanged)

- [ ] **Step 1: Rewrite `_log_ownership`**

Replace the whole `_log_ownership` function (`import_scraped.py:564-574`) with:

```python
def _log_ownership(report: "dlc_ownership.OwnershipReport", *, dry_run: bool) -> None:
    label = "WOULD MARK (dry run)" if dry_run else "MARKED"
    logger.info("--- DLC OWNERSHIP (%s) ---", label)
    logger.info("created:            %d", report.created)
    logger.info("reconciled:         %d", report.reconciled)
    logger.info("already owned:      %d", report.already_owned)
    logger.info("needs review:       %d", len(report.review))
    for m in report.review:
        logger.info("  REVIEW     '%s'  [%s]", m.addon_title, m.reason)
```

- [ ] **Step 2: Remove the obsolete CLI flag**

Delete these two argparse lines (`import_scraped.py:597-598`):

```python
    parser.add_argument("--apply-flagged-ownership", action="store_true",
                        help="also apply held (ambiguous/containment-only) ownership matches")
```

- [ ] **Step 3: Update the `mark_ownership` call**

Replace the ownership block (`import_scraped.py:658-660`) — the call that passes `include_flagged` — with:

```python
        report = dlc_ownership.mark_ownership(conn, all_addons, dry_run=args.dry_run)
```

(Leave the surrounding `if not args.no_ownership and all_addons:` / dry-run note / `conn.commit()` / `_log_ownership(...)` lines intact.)

- [ ] **Step 4: Run the import-scraped suite to verify it passes**

Run: `uv run python -m pytest tests/test_import_scraped.py -v`
Expected: PASS — including `test_partition_imports_games_and_marks_addon_ownership` and `test_main_runs_ownership_after_enrichment` (the add-on "Hearts of Stone" reconciles to the IGDB row by equality → `marked == 1`, `owned == 1`).

- [ ] **Step 5: Commit**

```bash
git add import_scraped.py
git commit -m "refactor: import_scraped ownership reporting to new report shape"
```

---

### Task 4: Web integration — `scrape_service` summary

Update the pipeline summary to the new report fields. The scrape-result UI in `templates/base.html` already reads `owned_marked`, `added_dlc`, `newly_owned`, and `review` (and NOT `held`/`unmatched`), so no template change is needed — newly-created vendor rows surface automatically via the existing `added_dlc` query.

**Files:**
- Modify: `scrape_service.py` (`_run_pipeline`, `scrape_service.py:121-133`)
- Modify: `tests/test_scrape_service.py` (`test_run_pipeline_reports_added_owned_review`)

- [ ] **Step 1: Update the failing test to the auto-create behavior**

Replace `test_run_pipeline_reports_added_owned_review` (`tests/test_scrape_service.py:166-213`) with:

```python
def test_run_pipeline_reports_added_owned_review(temp_db, monkeypatch):
    import igdb_dlc
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 ("The Witcher 3: Wild Hunt",
                  models.normalize_title(models.clean_title("The Witcher 3: Wild Hunt"))))
    gid = conn.execute("SELECT id FROM games WHERE title LIKE 'The Witcher%'").fetchone()[0]
    # pre-existing DLC, clearly created before this run
    conn.execute("INSERT INTO dlc (game_id, name, source, created_at) "
                 "VALUES (?, 'Hearts of Stone', 'igdb', '2000-01-01 00:00:00')", (gid,))
    conn.commit()
    conn.close()

    def fake_enrich(conn, *, client_id, token):
        for (g,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
            conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (g,))
            conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                         "VALUES (?, 'Blood and Wine', 'igdb')", (g,))
        conn.commit()
        return {"games": 1, "matched": 1, "added": 1, "errors": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_missing", fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    games = [
        ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                    source="playstation", external_id="G1"),
        # reconciles to the pre-existing IGDB row -> newly owned
        ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone", platform="PS5",
                    source="playstation", external_id="A1", kind="addon"),
        # confident parent, no matching row -> auto-created + owned this run
        ScrapedGame(title="The Witcher 3: Wild Hunt - Mystery Pack", platform="PS5",
                    source="playstation", external_id="A2", kind="addon"),
        # no parent in the library -> review
        ScrapedGame(title="Unknown Game - Bonus", platform="PS5",
                    source="playstation", external_id="A3", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()

    # added this run: IGDB "Blood and Wine" (not owned) + created "Mystery Pack" (owned).
    added = {d["name"]: d for d in summary["added_dlc"]}
    assert "Blood and Wine" in added and added["Blood and Wine"]["owned"] is False
    assert "Mystery Pack" in added and added["Mystery Pack"]["owned"] is True
    assert "Hearts of Stone" not in added  # created before this run

    owned_names = sorted(d["name"] for d in summary["newly_owned"])
    assert owned_names == ["Hearts of Stone", "Mystery Pack"]

    review_titles = [r["title"] for r in summary["review"]]
    assert review_titles == ["Unknown Game - Bonus"]
    assert summary["owned_marked"] == 2
    assert summary["created"] == 1
    conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/test_scrape_service.py::test_run_pipeline_reports_added_owned_review -v`
Expected: FAIL — `KeyError: 'created'` (summary has no `created` key yet) / the new assertions don't hold against the old held/unmatched summary.

- [ ] **Step 3: Update `_run_pipeline`'s summary**

In `scrape_service._run_pipeline`, replace the `review` list build (`scrape_service.py:121-122`):

```python
    review = [{"title": m.addon_title, "reason": m.reason}
              for m in (report.held + report.unmatched)]
```

with:

```python
    review = [{"title": m.addon_title, "reason": m.reason} for m in report.review]
```

Then in the returned `summary` dict, replace the two lines (`scrape_service.py:132-133`):

```python
        "held": len(report.held),
        "unmatched": len(report.unmatched),
```

with:

```python
        "created": report.created,
```

(`owned_marked`, `added_dlc`, `newly_owned`, `review`, etc. are unchanged. The
`newly_owned` loop already resolves each `report.marked_items` Match by its
`dlc_id`, which is set for both reconciled and created rows.)

- [ ] **Step 4: Run the scrape-service suite to verify it passes**

Run: `uv run python -m pytest tests/test_scrape_service.py -v`
Expected: PASS — including the unchanged `test_run_pipeline_imports_enriches_marks` (`owned_marked == 1`) and `test_start_runs_full_flow`.

- [ ] **Step 5: Commit**

```bash
git add scrape_service.py tests/test_scrape_service.py
git commit -m "feat: scrape summary reports created/owned/review for vendor DLC"
```

---

### Task 5: Full-suite green, lint, and verification notes

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run python -m pytest`
Expected: PASS (all green). If `tests/test_migration.py` or any DLC/API test fails, confirm the new `dlc_external_ids` table is created by both `init_db` (Task 1 Step 5) and `migrate_db` (Task 1 Step 4).

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: no errors. Do NOT run `ruff format` (the repo is hand-aligned). Fix any lint findings by hand, matching surrounding style.

- [ ] **Step 3: Confirm no dangling references to removed symbols**

Run: `uv run python -c "import import_scraped, scrape_service, dlc_ownership, app"`
Expected: imports succeed (no `AttributeError`/`ImportError` from removed `classify`/`match_dlc`/`held`/`unmatched`/`include_flagged`).

- [ ] **Step 4: Commit any lint fixes (if any)**

```bash
git add -A
git commit -m "chore: lint fixes for DLC vendor-source foundation"
```

- [ ] **Step 5: Manual verification (owner, live — not run by agents)**

The live headed scrape is verified manually and never run against the real `games.db` by agents. Owner steps, when ready:
1. Back up first (the web pipeline also auto-backs up via `scrape_service.backup_db`).
2. `uv run python app.py` (port 5000, `use_reloader=False`), open the Add Game modal → "Sync a whole library" → Nintendo, log in, Continue.
3. On completion, expect the scrape result to show **Marked owned this run** populated (the previously-unmarked owned add-ons) and any genuinely unknown-parent add-ons under **Needs review**; the hero `owned/total DLC` tile should rise.
4. Spot-check a game like Vampire Survivors: its owned add-ons now appear as owned DLC rows in the game's DLC tab.

---

## Notes for the implementer

- **IGDB enrichment is unchanged** and still runs before ownership (it is now the *fallback* catalogue; equality-reconcile finds its rows). Do not modify `igdb_dlc.py` or `run_dlc_enrichment`.
- **`dlc.kind` vs scrape `kind`:** the scrape add-on's `kind="addon"` is the game-vs-addon partition flag, NOT the `dlc.kind` column. Created `dlc` rows use `kind='dlc'` (the engine hardcodes it). Never write `'addon'` into `dlc.kind`.
- **PlayStation is intentionally not fixed here** — it does not scrape add-ons yet (SP4). Only Nintendo & Xbox already emit `kind="addon"` rows, so only they benefit from SP1.
- **`fetch_covers.py`'s `include_flagged`** is a separate canonical-rename feature — do not touch it.
