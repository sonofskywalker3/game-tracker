# Vendor-agnostic Add-on Parent Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At scrape time, link every owned Xbox add-on to its parent game — matching the parent by Microsoft Store product id, else by name (backfilling the id), else creating the parent from the catalog — so a single rerun marks them owned and clears the review queue with no manual steps. Built on a vendor-pluggable resolver so Nintendo slots in later.

**Architecture:** A vendor-agnostic core (`addon_parent.py`) holds the `ParentRef` type, a per-source resolver registry, and the `resolve_and_link` pipeline (ensure-parent → link owned → clear review rows). The Xbox resolver (`scrapers/xbox_catalog.py`) reads Microsoft `displaycatalog`'s `addOnParent` relationship. `scrape_service` runs a two-pass flow for the vendor's add-ons: catalog resolution first, then the existing name-based `mark_ownership` for whatever didn't resolve. `mark_ownership` itself is unchanged.

**Tech Stack:** Python 3, sqlite3, `requests` (already a dep; used by `steam_dlc.py`), pytest. Package mgmt via `uv`. Tests run ONLY with `uv run python -m pytest`. Lint ONLY with `uv run ruff check` (NEVER `ruff format`).

**Conventions (from CLAUDE.md / project memory):**
- Type hints on every signature; named constants (no magic strings in conditions); specific exceptions only; every `except` logs / re-raises / returns typed; `logging` not `print`.
- Work on `main`, commit directly, end each commit message with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Subagents: pytest temp-DB + static review ONLY. NEVER touch the live `games.db` or the running app on http://127.0.0.1:5000.

**Reference reading (look, don't modify unless a task says so):**
- `dlc_ownership.py` — `apply_addon_to_parent`, `OwnershipReport`, `Match`, `norm`, `_addon_field`.
- `import_scraped.py:277` `_safe_auto_confirm`, `:284` `import_games` (records `game_external_ids` via INSERT OR IGNORE; `ImportStats.new_games` counts created games).
- `steam_dlc.py:63` `fetch_appdetails` — the on-disk cache + injected-`session` pattern to mirror.
- `scrapers/xbox.py` — `SOURCE="xbox"`, `PLATFORM="Xbox"`, how add-ons are emitted (`kind="addon"`, `external_id`=Store product id).
- `dlc_review.py` — `rematch_unresolved` (style reference for a DB pass that clears review rows).
- Design spec: `docs/superpowers/specs/2026-06-05-addon-parent-resolution-design.md`.

---

## File Structure

- **Create** `addon_parent.py` — vendor-agnostic: `ParentRef`, `ResolveReport`, `RESOLVERS` registry, `_ensure_parent_game`, `resolve_and_link`. Pure DB + injected resolver; no vendor HTTP.
- **Create** `scrapers/xbox_catalog.py` — `resolve_addon_parents(product_ids, *, fetch=...)` using `displaycatalog`; on-disk cache in `.xbox_cache/`.
- **Modify** `scrape_service.py` — in `_run_pipeline`, run `addon_parent.resolve_and_link` for vendors with a registered resolver, then pass the unresolved add-ons to `mark_ownership`.
- **Modify** `.gitignore` — add `.xbox_cache/`.
- **Create** `tests/test_addon_parent.py`, `tests/test_xbox_catalog.py`, and extend `tests/test_scrape_service.py`.

---

## Task 1: `addon_parent.py` — ParentRef + ResolveReport + ensure-parent

**Files:**
- Create: `addon_parent.py`
- Test: `tests/test_addon_parent.py`

This task builds the data types and the `_ensure_parent_game` helper (id-lookup → import-to-backfill-or-create). The full `resolve_and_link` pipeline is Task 2.

- [ ] **Step 1: Write the failing test for the dataclasses + ensure-parent**

Create `tests/test_addon_parent.py`. Use a temp sqlite DB with the real-ish schema the engine needs (mirror `tests/test_dlc_review_resolve.py`'s fixture, plus `game_external_ids`).

```python
"""addon_parent: vendor-agnostic add-on -> parent-game resolution."""
from __future__ import annotations

import sqlite3

import pytest

import addon_parent
import models
from addon_parent import ParentRef
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE game_external_ids (
            game_id INTEGER NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
            source_title TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, external_id),
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE TABLE dlc (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL, name TEXT NOT NULL,
            igdb_id INTEGER, kind TEXT DEFAULT 'dlc', owned INTEGER DEFAULT 0,
            source TEXT DEFAULT 'igdb', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_id, name), FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE TABLE dlc_external_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dlc_id INTEGER NOT NULL, source TEXT NOT NULL,
            external_id TEXT NOT NULL, source_title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source, external_id), FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
        );
    """)
    models.migrate_dlc_review_queue(c)
    c.commit()
    yield c
    c.close()


def _add_game(conn, title, *, source=None, ext=None):
    cur = conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                       (title, normalize_title(title)))
    gid = cur.lastrowid
    if source and ext:
        conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                     "VALUES (?, ?, ?)", (gid, source, ext))
    return gid


def test_ensure_parent_game_by_id(conn):
    gid = _add_game(conn, "Borderlands 4", source="xbox", ext="9MX6HKF5647G")
    pr = ParentRef(product_id="9MX6HKF5647G", name="Borderlands 4")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr)
    assert got == gid
    assert how == "id"  # matched existing by id


def test_ensure_parent_game_by_name_backfills_id(conn):
    gid = _add_game(conn, "Borderlands 4")  # exists, NO xbox id yet
    pr = ParentRef(product_id="9MX6HKF5647G", name="Borderlands 4")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr)
    assert got == gid          # matched the existing game by name
    assert how == "backfill"
    row = conn.execute("SELECT game_id FROM game_external_ids "
                       "WHERE source='xbox' AND external_id='9MX6HKF5647G'").fetchone()
    assert row["game_id"] == gid   # id backfilled onto the existing game


def test_ensure_parent_game_creates_when_missing(conn):
    pr = ParentRef(product_id="BNG8P3Q7C78Z", name="Rock Band 4")
    got, how = addon_parent._ensure_parent_game(conn, "xbox", "Xbox", pr)
    assert got is not None
    assert how == "created"
    g = conn.execute("SELECT title FROM games WHERE id=?", (got,)).fetchone()
    assert g["title"] == "Rock Band 4"
    row = conn.execute("SELECT game_id FROM game_external_ids "
                       "WHERE source='xbox' AND external_id='BNG8P3Q7C78Z'").fetchone()
    assert row["game_id"] == got
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_addon_parent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'addon_parent'` (or `AttributeError`).

- [ ] **Step 3: Implement `addon_parent.py` types + `_ensure_parent_game`**

Create `addon_parent.py`:

```python
"""Vendor-agnostic add-on -> parent-game resolution at scrape time.

Store vendors (Xbox, Nintendo) scrape add-ons as a flat list with no parent
reference, so the name-based matcher in `dlc_ownership.mark_ownership` files them
as "no parent game". This module resolves each owned add-on to its parent GAME via
a per-vendor resolver (e.g. Microsoft displaycatalog's `addOnParent`), ensures that
parent exists in the library (match by vendor id, else by name + backfill the id,
else create it from catalog metadata), links the add-on owned through the existing
ownership engine, and clears any matching review-queue rows. Pure DB; the resolver's
network I/O is injected. See
docs/superpowers/specs/2026-06-05-addon-parent-resolution-design.md.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

import dlc_ownership
import import_scraped
from dlc_ownership import Match, OwnershipReport

logger = logging.getLogger(__name__)

# A resolver maps a list of add-on vendor product ids to each one's parent GAME.
ParentResolver = Callable[[list[str]], "dict[str, ParentRef | None]"]


@dataclass
class ParentRef:
    """A resolved parent GAME identity for one add-on (from the vendor catalogue)."""
    product_id: str
    name: str | None = None
    cover_url: str | None = None


@dataclass
class ResolveReport:
    """Outcome of a resolve_and_link pass."""
    linked: int = 0              # dlc rows newly set owned (created + reconciled)
    created_parents: int = 0     # parent games created from the catalogue
    backfilled_ids: int = 0      # existing games that gained a vendor id this pass
    review_cleared: int = 0      # open review rows marked resolved
    linked_items: list[Match] = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # add-ons with no catalogue parent


def _ensure_parent_game(
    conn: sqlite3.Connection, source: str, platform: str, parent: ParentRef,
    *, create_missing: bool = True,
) -> tuple[int | None, str]:
    """Return (game_id, how) for an add-on's parent.

    `how` is one of: "id" (matched a game already carrying this vendor product id),
    "backfill" (the import name-matched an existing game, recording the id onto it),
    "created" (the import created a new game), or "" (no parent: create_missing False
    with no id match, or the import produced no game, e.g. a non-game title).

    Resolution order: (1) existing game by vendor product id; (2) otherwise, when
    create_missing, a synthetic one-game `import_scraped.import_games` call, which
    name-matches an existing game (id backfilled via its INSERT OR IGNORE) or creates
    a new one (`ImportStats.new_games` distinguishes the two).
    """
    row = conn.execute(
        "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
        (source, parent.product_id)).fetchone()
    if row:
        return row[0], "id"
    if not create_missing or not parent.name:
        return None, ""
    synthetic = {
        "title": parent.name, "platform": platform, "source": source,
        "external_id": parent.product_id, "cover_url": parent.cover_url, "kind": "game",
    }
    stats = import_scraped.import_games(
        conn, [synthetic], source, confirm_fn=import_scraped._safe_auto_confirm)
    row = conn.execute(
        "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
        (source, parent.product_id)).fetchone()
    if not row:
        logger.warning("addon_parent: parent %r (%s) did not import", parent.name, parent.product_id)
        return None, ""
    return row[0], ("created" if stats.new_games > 0 else "backfill")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_addon_parent.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint**

Run: `uv run ruff check addon_parent.py tests/test_addon_parent.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add addon_parent.py tests/test_addon_parent.py
git commit -m "feat(dlc): addon_parent ParentRef + ensure-parent (id/name-backfill/create)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `addon_parent.resolve_and_link` pipeline

**Files:**
- Modify: `addon_parent.py`
- Test: `tests/test_addon_parent.py`

- [ ] **Step 1: Write the failing tests for `resolve_and_link`**

Append to `tests/test_addon_parent.py`. A "resolver" here is just a dict-backed fake (no network).

```python
def _addon(title, ext, source="xbox"):
    return {"title": title, "source": source, "external_id": ext, "source_title": title}


def _fake_resolver(mapping):
    """mapping: {addon_ext: ParentRef | None} -> a ParentResolver."""
    return lambda ids: {i: mapping.get(i) for i in ids}


def test_resolve_and_link_creates_parent_and_owns_addon(conn):
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 1
    assert rep.created_parents == 1
    # owned dlc exists under the created parent, with the vendor id recorded
    d = conn.execute("SELECT d.owned, d.game_id FROM dlc d "
                     "JOIN games g ON g.id=d.game_id WHERE g.title='Borderlands 4'").fetchone()
    assert d["owned"] == 1
    assert conn.execute("SELECT 1 FROM dlc_external_ids "
                        "WHERE source='xbox' AND external_id='ADDON1'").fetchone()


def test_resolve_and_link_shared_parent_for_many_addons(conn):
    # 3 song add-ons, one shared Rock Band 4 parent -> one game, 3 owned dlc.
    rb = ParentRef("RBPID", "Rock Band 4")
    addons = [_addon("Song A", "S1"), _addon("Song B", "S2"), _addon("Song C", "S3")]
    resolver = _fake_resolver({"S1": rb, "S2": rb, "S3": rb})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 3
    assert rep.created_parents == 1  # parent created once, not thrice
    n_games = conn.execute("SELECT COUNT(*) FROM games WHERE title='Rock Band 4'").fetchone()[0]
    assert n_games == 1
    n_dlc = conn.execute("SELECT COUNT(*) FROM dlc WHERE owned=1").fetchone()[0]
    assert n_dlc == 3


def test_resolve_and_link_backfills_existing_game(conn):
    gid = _add_game(conn, "Borderlands 4")  # exists, no xbox id
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 1
    assert rep.backfilled_ids == 1
    assert rep.created_parents == 0
    row = conn.execute("SELECT game_id FROM game_external_ids "
                       "WHERE source='xbox' AND external_id='PARENTPID'").fetchone()
    assert row["game_id"] == gid


def test_resolve_and_link_clears_open_review_row(conn):
    # Seed an open review row for this add-on; linking it should resolve it.
    conn.execute("INSERT INTO dlc_review_queue (addon_title, source, external_id, reason) "
                 "VALUES ('Gilded Glory Pack', 'xbox', 'ADDON1', 'no parent game')")
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.review_cleared == 1
    r = conn.execute("SELECT resolved_at FROM dlc_review_queue "
                     "WHERE source='xbox' AND external_id='ADDON1'").fetchone()
    assert r["resolved_at"] is not None


def test_resolve_and_link_unresolved_passes_through(conn):
    addons = [_addon("Mystery DLC", "M1")]
    resolver = _fake_resolver({"M1": None})  # catalogue had no parent
    rep = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep.linked == 0
    assert len(rep.unresolved) == 1
    assert rep.unresolved[0]["external_id"] == "M1"


def test_resolve_and_link_idempotent(conn):
    addons = [_addon("Gilded Glory Pack", "ADDON1")]
    resolver = _fake_resolver({"ADDON1": ParentRef("PARENTPID", "Borderlands 4")})
    addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    n_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    n_dlc = conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]
    rep2 = addon_parent.resolve_and_link(conn, "xbox", "Xbox", addons, resolver)
    assert rep2.linked == 0  # already owned, nothing newly marked
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == n_games
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == n_dlc
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_addon_parent.py -q`
Expected: FAIL — `AttributeError: module 'addon_parent' has no attribute 'resolve_and_link'`.

- [ ] **Step 3: Implement `resolve_and_link` (+ the resolver registry)**

Append to `addon_parent.py`:

```python
def _clear_review(conn: sqlite3.Connection, source: str, ext: str | None) -> int:
    """Mark any open review rows for this vendor add-on resolved. Returns rowcount."""
    if not ext:
        return 0
    cur = conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP "
        "WHERE source = ? AND external_id = ? "
        "AND resolved_at IS NULL AND dismissed_at IS NULL",
        (source, ext))
    return cur.rowcount


def resolve_and_link(
    conn: sqlite3.Connection, source: str, platform: str, addons: list,
    resolver: ParentResolver, *, create_missing: bool = True,
) -> ResolveReport:
    """Resolve each owned add-on to its parent game and mark it owned.

    For every add-on (a scrape dict with title/source/external_id/source_title):
    ask `resolver` for its parent, ensure that parent game exists (see
    `_ensure_parent_game`), link the add-on owned via the shared engine
    `dlc_ownership.apply_addon_to_parent`, and clear matching open review rows.
    Add-ons with no catalogue parent go to `report.unresolved` for the caller to
    fall back on `dlc_ownership.mark_ownership`. Caller owns commit.
    """
    report = ResolveReport()
    ids = [dlc_ownership._addon_field(a, "external_id") for a in addons]
    ids = [i for i in ids if i]
    parents = resolver(ids) if ids else {}

    for addon in addons:
        ext = dlc_ownership._addon_field(addon, "external_id")
        parent_ref = parents.get(ext) if ext else None
        if parent_ref is None:
            report.unresolved.append(addon)
            continue
        parent_id, how = _ensure_parent_game(
            conn, source, platform, parent_ref, create_missing=create_missing)
        if parent_id is None:
            report.unresolved.append(addon)
            continue
        if how == "created":
            report.created_parents += 1
        elif how == "backfill":
            report.backfilled_ids += 1

        prow = conn.execute(
            "SELECT title, normalized_title FROM games WHERE id = ?", (parent_id,)).fetchone()
        parent_norm = (prow["normalized_title"] if prow else "") or ""
        titles = {parent_id: prow["title"] if prow else ""}

        sub = OwnershipReport()
        dlc_ownership.apply_addon_to_parent(
            conn, sub, parent_id, parent_norm, titles, addon, dry_run=False)
        report.linked += sub.marked
        report.linked_items.extend(sub.marked_items)
        if sub.marked or sub.already_owned:
            report.review_cleared += _clear_review(conn, source, ext)

    return report
```

- [ ] **Step 4: Run all Task-1+2 tests**

Run: `uv run python -m pytest tests/test_addon_parent.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Add the resolver registry**

Append to `addon_parent.py` (the Xbox entry is filled in by Task 3 after the resolver exists; keep it empty here to avoid an import cycle):

```python
# Per-source add-on parent resolvers. Populated at import time by scrape wiring
# (see scrape_service). A source with no resolver falls back to name matching.
RESOLVERS: dict[str, ParentResolver] = {}
```

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check addon_parent.py tests/test_addon_parent.py`
Expected: All checks passed.

```bash
git add addon_parent.py tests/test_addon_parent.py
git commit -m "feat(dlc): resolve_and_link pipeline (link owned + create parent + clear review)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Xbox resolver — `scrapers/xbox_catalog.py`

**Files:**
- Create: `scrapers/xbox_catalog.py`
- Modify: `.gitignore`
- Test: `tests/test_xbox_catalog.py`

The displaycatalog response shape (from live recon):
- Single: `GET /v7.0/products/{id}?market=US&languages=en-US&fieldsTemplate=details` → `{"Product": {...}}`.
- Batch: `GET /v7.0/products?bigIds=ID1,ID2&market=US&languages=en-US&fieldsTemplate=details` → `{"Products": [ {...}, ... ]}`.
- A product's parent: `Product.MarketProperties[0].RelatedProducts[*]` where
  `RelationshipType == "addOnParent"` → `RelatedProductId`.
- Product type: `Product.ProductType` (`"Game"`, `"Durable"`, `"Consumable"`).
- Title: `Product.LocalizedProperties[0].ProductTitle`.

- [ ] **Step 1: Write the failing parser tests (offline, fixture dicts)**

Create `tests/test_xbox_catalog.py`:

```python
"""xbox_catalog: resolve an add-on's parent GAME via Microsoft displaycatalog."""
from __future__ import annotations

from scrapers import xbox_catalog
from addon_parent import ParentRef


def _product(pid, ptype, title, *, parent_id=None):
    rel = ([{"RelatedProductId": parent_id, "RelationshipType": "addOnParent"}]
           if parent_id else [])
    return {
        "ProductId": pid, "ProductType": ptype,
        "LocalizedProperties": [{"ProductTitle": title}],
        "MarketProperties": [{"RelatedProducts": rel}],
    }


def test_parse_addon_parent_id():
    addon = _product("ADDON1", "Durable", "Gilded Glory Pack", parent_id="PARENT1")
    assert xbox_catalog._parent_id_of(addon) == "PARENT1"


def test_parse_no_parent_returns_none():
    game = _product("GAME1", "Game", "Borderlands 4")  # no addOnParent
    assert xbox_catalog._parent_id_of(game) is None


def test_resolve_uses_fetch_and_accepts_game_parent():
    # fetch returns products by id (a fake catalogue).
    catalogue = {
        "ADDON1": _product("ADDON1", "Durable", "Gilded Glory Pack", parent_id="PARENT1"),
        "PARENT1": _product("PARENT1", "Game", "Borderlands 4"),
    }
    def fake_fetch(ids):
        return {i: catalogue.get(i) for i in ids}
    out = xbox_catalog.resolve_addon_parents(["ADDON1"], fetch=fake_fetch)
    assert out["ADDON1"] == ParentRef(product_id="PARENT1", name="Borderlands 4")


def test_resolve_rejects_non_game_parent():
    catalogue = {
        "ADDON1": _product("ADDON1", "Durable", "Some Pass Perk", parent_id="PASS1"),
        "PASS1": _product("PASS1", "Pass", "A Subscription"),  # not a Game
    }
    def fake_fetch(ids):
        return {i: catalogue.get(i) for i in ids}
    out = xbox_catalog.resolve_addon_parents(["ADDON1"], fetch=fake_fetch)
    assert out["ADDON1"] is None


def test_resolve_missing_addon_is_none():
    def fake_fetch(ids):
        return {i: None for i in ids}
    out = xbox_catalog.resolve_addon_parents(["NOPE"], fetch=fake_fetch)
    assert out["NOPE"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_xbox_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrapers.xbox_catalog'`.

- [ ] **Step 3: Implement `scrapers/xbox_catalog.py`**

```python
"""Resolve an Xbox add-on's parent GAME via Microsoft's public displaycatalog.

displaycatalog needs no auth. Each Durable/Consumable product carries a
`RelatedProducts` entry of type "addOnParent" pointing at the base game's Store
product id; we keep it only when that parent's ProductType is "Game". Responses are
cached on disk (mirrors steam_dlc's appdetails cache). The network fetch is injected
so tests run offline.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import requests

from addon_parent import ParentRef

logger = logging.getLogger(__name__)

CATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
MARKET = "US"
LANGUAGES = "en-US"
ADDON_PARENT_REL = "addOnParent"
PARENT_GAME_TYPE = "Game"
BATCH_SIZE = 20           # displaycatalog bigIds cap is generous; stay modest
REQUEST_DELAY_S = 0.4
CACHE_DIR = Path(__file__).parent.parent / ".xbox_cache"

# fetch(ids) -> {product_id: product_dict | None}
CatalogFetch = Callable[[list[str]], "dict[str, dict | None]"]


def _parent_id_of(product: dict | None) -> str | None:
    """The product's addOnParent RelatedProductId, or None."""
    if not product:
        return None
    mp = (product.get("MarketProperties") or [{}])[0]
    for rel in mp.get("RelatedProducts") or []:
        if rel.get("RelationshipType") == ADDON_PARENT_REL and rel.get("RelatedProductId"):
            return rel["RelatedProductId"]
    return None


def _title_of(product: dict | None) -> str | None:
    if not product:
        return None
    loc = (product.get("LocalizedProperties") or [{}])[0]
    return loc.get("ProductTitle")


def _fetch_products(ids: list[str], *, cache_dir: Path = CACHE_DIR,
                    session=requests, delay_s: float = REQUEST_DELAY_S) -> dict[str, dict | None]:
    """Return {id: product_dict | None}, cached per id on disk.

    Cache hits skip the network. Misses are fetched via the bigIds batch endpoint
    in chunks; a not-found id is cached as an empty object so it isn't refetched.
    """
    cache_dir = Path(cache_dir)
    out: dict[str, dict | None] = {}
    misses: list[str] = []
    for pid in ids:
        f = cache_dir / f"{pid}.json"
        if f.exists():
            out[pid] = json.loads(f.read_text(encoding="utf-8")) or None
        else:
            misses.append(pid)
    for i in range(0, len(misses), BATCH_SIZE):
        chunk = misses[i:i + BATCH_SIZE]
        params = {"bigIds": ",".join(chunk), "market": MARKET,
                  "languages": LANGUAGES, "fieldsTemplate": "details"}
        try:
            resp = session.get(CATALOG_URL, params=params, timeout=30)
            resp.raise_for_status()
            products = (resp.json() or {}).get("Products") or []
        except (requests.RequestException, json.JSONDecodeError) as exc:
            logger.warning("xbox displaycatalog batch failed (%s): %s", chunk, exc)
            products = []
        by_id = {p.get("ProductId"): p for p in products if p.get("ProductId")}
        cache_dir.mkdir(parents=True, exist_ok=True)
        for pid in chunk:
            prod = by_id.get(pid)
            (cache_dir / f"{pid}.json").write_text(
                json.dumps(prod or {}, ensure_ascii=False), encoding="utf-8")
            out[pid] = prod
        if delay_s:
            time.sleep(delay_s)
    return out


def resolve_addon_parents(product_ids: list[str], *,
                          fetch: CatalogFetch = _fetch_products) -> dict[str, ParentRef | None]:
    """Map each add-on product id to its parent GAME ParentRef (or None).

    Two passes: fetch the add-ons, read each one's addOnParent id, then fetch those
    parents and keep only ProductType == "Game", returning their id + title.
    """
    addons = fetch(list(dict.fromkeys(product_ids)))
    parent_ids: dict[str, str | None] = {pid: _parent_id_of(p) for pid, p in addons.items()}
    wanted = list({pid for pid in parent_ids.values() if pid})
    parents = fetch(wanted) if wanted else {}

    result: dict[str, ParentRef | None] = {}
    for pid in product_ids:
        ppid = parent_ids.get(pid)
        parent = parents.get(ppid) if ppid else None
        if parent and parent.get("ProductType") == PARENT_GAME_TYPE:
            result[pid] = ParentRef(product_id=ppid, name=_title_of(parent))
        else:
            result[pid] = None
    return result
```

- [ ] **Step 4: Run the parser tests to verify pass**

Run: `uv run python -m pytest tests/test_xbox_catalog.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Add `.xbox_cache/` to `.gitignore`**

Append a line `.xbox_cache/` to `.gitignore` (confirm `.steam_cache/` is already there as the pattern to follow).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check scrapers/xbox_catalog.py tests/test_xbox_catalog.py`
Expected: All checks passed.

```bash
git add scrapers/xbox_catalog.py tests/test_xbox_catalog.py .gitignore
git commit -m "feat(xbox): displaycatalog addOnParent resolver (cached, offline-testable)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire into the scrape pipeline

**Files:**
- Modify: `scrape_service.py` (`_run_pipeline`, the non-Steam branch around lines 254-266)
- Modify: `addon_parent.py` (register the Xbox resolver)
- Test: extend `tests/test_scrape_service.py`

- [ ] **Step 1: Read the current non-Steam branch**

Read `scrape_service.py:254-266`. The non-Steam branch currently does:
```python
        report = dlc_ownership.mark_ownership(conn, addons)
        ...
        review = [{"title": m.addon_title, "reason": m.reason} for m in report.review]
```
You will insert a resolve-and-link pass BEFORE `mark_ownership`, and feed only the unresolved add-ons to `mark_ownership`.

- [ ] **Step 2: Write the failing integration test**

Add to `tests/test_scrape_service.py` (follow the file's existing fixture/import style; if it uses a temp DB + monkeypatch, mirror that). The test patches the registry with a fake resolver so no network is hit.

```python
def test_pipeline_resolves_xbox_addon_parent(monkeypatch, tmp_path):
    # Build a temp DB with schema; import one xbox game + one owned addon whose
    # catalogue parent is a different game not yet in the library.
    import models, addon_parent, scrape_service
    from addon_parent import ParentRef
    db = tmp_path / "g.db"
    monkeypatch.setattr(models, "DB_PATH", db)
    models.init_db()
    conn = models.get_db()
    for mig in ("migrate_dlc", "migrate_dlc_external_ids", "migrate_dlc_review_queue"):
        getattr(models, mig)(conn)
    conn.commit()

    # Fake Xbox resolver: addon 'AON1' -> parent game 'PARENTPID' named 'Rock Band 4'
    monkeypatch.setitem(
        addon_parent.RESOLVERS, "xbox",
        lambda ids: {i: (ParentRef("PARENTPID", "Rock Band 4") if i == "AON1" else None) for i in ids})

    games = [
        {"title": "Some Game", "platform": "Xbox", "source": "xbox",
         "external_id": "G1", "kind": "game"},
        {"title": "Synth Track", "platform": "Xbox", "source": "xbox",
         "external_id": "AON1", "kind": "addon", "source_title": "Synth Track"},
    ]
    summary = scrape_service._run_pipeline(conn, "xbox", games)
    # The owned add-on is linked to a newly-created Rock Band 4 game.
    owned = conn.execute("SELECT COUNT(*) FROM dlc WHERE owned=1").fetchone()[0]
    assert owned == 1
    rb = conn.execute("SELECT 1 FROM games WHERE title='Rock Band 4'").fetchone()
    assert rb is not None
    conn.close()
```

NOTE: confirm `_run_pipeline`'s real signature and return shape from `scrape_service.py:217` before finalizing the test; adapt the call/asserts to match (it returns a summary dict). If `_run_pipeline` requires the scrape `_state`/progress globals, the existing tests in `tests/test_scrape_service.py` show how they set those up — follow that setup exactly.

- [ ] **Step 3: Run to verify failure**

Run: `uv run python -m pytest tests/test_scrape_service.py::test_pipeline_resolves_xbox_addon_parent -q`
Expected: FAIL (add-on goes to review; no Rock Band 4 game created) — `assert owned == 1` fails with `owned == 0`.

- [ ] **Step 4: Register the resolver + insert the pass**

In `addon_parent.py`, register Xbox by importing the resolver lazily to avoid an import cycle. Add at the bottom of `addon_parent.py`:

```python
def _register_default_resolvers() -> None:
    """Register built-in vendor resolvers (called once at import)."""
    try:
        from scrapers import xbox_catalog
    except ImportError:  # pragma: no cover - scrapers always present in app runtime
        logger.warning("addon_parent: xbox_catalog unavailable; xbox parent resolution disabled")
        return
    RESOLVERS.setdefault("xbox", xbox_catalog.resolve_addon_parents)


_register_default_resolvers()
```

In `scrape_service.py`, modify the non-Steam branch. Replace:
```python
        _set(phase="matching", message="matching DLC ownership...")
        report = dlc_ownership.mark_ownership(conn, addons)
        conn.commit()
```
with:
```python
        _set(phase="matching", message="matching DLC ownership...")
        import addon_parent
        platform = _PLATFORM_BY_VENDOR.get(vendor, vendor.title())
        resolver = addon_parent.RESOLVERS.get(vendor)
        if resolver and addons:
            link = addon_parent.resolve_and_link(conn, vendor, platform, addons, resolver)
            conn.commit()
            remaining = link.unresolved
        else:
            link = None
            remaining = addons
        report = dlc_ownership.mark_ownership(conn, remaining)
        conn.commit()
```
And just after the existing `owned_marked, created = report.marked, report.created` line in that branch, fold in the resolver pass's counts:
```python
        if link is not None:
            owned_marked += link.linked
            created += link.created_parents
```
Add a module-level constant near the top of `scrape_service.py` (with the other constants):
```python
# Vendor -> platform label for parent games created from a vendor catalogue.
_PLATFORM_BY_VENDOR = {"xbox": "Xbox", "nintendo": "Switch", "playstation": "PS4"}
```

- [ ] **Step 5: Run the integration test + the full suite**

Run: `uv run python -m pytest tests/test_scrape_service.py -q`
Expected: PASS (new test + existing scrape_service tests green).

Run: `uv run python -m pytest -q`
Expected: PASS (entire suite; was 622 before this plan — expect 622 + the new tests).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check scrape_service.py addon_parent.py tests/test_scrape_service.py`
Expected: All checks passed.

```bash
git add scrape_service.py addon_parent.py tests/test_scrape_service.py
git commit -m "feat(xbox): resolve add-on parents via catalogue during scrape; name fallback for the rest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Capture real displaycatalog fixtures + a live-shape regression test

**Files:**
- Create: `tests/fixtures/xbox_displaycatalog_sample.json`
- Test: `tests/test_xbox_catalog.py` (add one test)

This pins the parser against the REAL response shape (the recon used `Product` singular for single-get and `Products` for batch — guard against a future shape drift).

- [ ] **Step 1: Add a fixture built from the real recon shape**

Create `tests/fixtures/xbox_displaycatalog_sample.json` — a trimmed but real-shaped batch response containing one Durable add-on (with an `addOnParent` RelatedProduct) and its Game parent. Use the exact field nesting from `scrapers/xbox_catalog.py` (`Products` list; each has `ProductId`, `ProductType`, `LocalizedProperties[0].ProductTitle`, `MarketProperties[0].RelatedProducts[*]`). Use the real ids `9MZKJPZXTVGM` (add-on) → `9MX6HKF5647G` (Borderlands®4):

```json
{
  "Products": [
    {
      "ProductId": "9MZKJPZXTVGM",
      "ProductType": "Durable",
      "LocalizedProperties": [{"ProductTitle": "Borderlands®4: Gilded Glory Pack"}],
      "MarketProperties": [{"RelatedProducts": [
        {"RelatedProductId": "9MX6HKF5647G", "RelationshipType": "addOnParent"},
        {"RelatedProductId": "9MX6HKF5647G", "RelationshipType": "SellableBy"}
      ]}]
    },
    {
      "ProductId": "9MX6HKF5647G",
      "ProductType": "Game",
      "LocalizedProperties": [{"ProductTitle": "Borderlands®4"}],
      "MarketProperties": [{"RelatedProducts": []}]
    }
  ]
}
```

- [ ] **Step 2: Write the test that drives the parser off the fixture**

Add to `tests/test_xbox_catalog.py`:

```python
import json
from pathlib import Path


def test_resolve_against_real_shaped_fixture():
    body = json.loads(
        (Path(__file__).parent / "fixtures" / "xbox_displaycatalog_sample.json")
        .read_text(encoding="utf-8"))
    by_id = {p["ProductId"]: p for p in body["Products"]}

    def fake_fetch(ids):
        return {i: by_id.get(i) for i in ids}

    out = xbox_catalog.resolve_addon_parents(["9MZKJPZXTVGM"], fetch=fake_fetch)
    assert out["9MZKJPZXTVGM"].product_id == "9MX6HKF5647G"
    assert out["9MZKJPZXTVGM"].name.startswith("Borderlands")
```

- [ ] **Step 3: Run + verify pass**

Run: `uv run python -m pytest tests/test_xbox_catalog.py -q`
Expected: PASS (6 passed).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/xbox_displaycatalog_sample.json tests/test_xbox_catalog.py
git commit -m "test(xbox): pin displaycatalog parser to the real response shape

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Final verification + push

- [ ] **Step 1: Full suite green**

Run: `uv run python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 2: Lint the whole change set**

Run: `uv run ruff check addon_parent.py scrapers/xbox_catalog.py scrape_service.py tests/test_addon_parent.py tests/test_xbox_catalog.py tests/test_scrape_service.py`
Expected: All checks passed.

- [ ] **Step 3: Push**

```bash
git push origin main
```

- [ ] **Step 4: Hand back to the owner**

The owner reruns a live **Xbox** scrape from the app (the agent must NOT run the live scrape or touch the running app). Expected after one rerun: the 29 Xbox review rows clear; Rock Band 4 / Borderlands 4 / Spellbreak etc. exist (created or backfilled) with their add-ons owned; owned-DLC count rises. Note for the owner: `.xbox_cache/` will populate on the first run (one displaycatalog batch per ~20 ids).

---

## Self-Review notes (addressed)

- **Spec coverage:** resolver interface (Task 1/2), Xbox displaycatalog resolver (Task 3/5), ensure-parent by id/name-backfill/create (Task 1), link + clear review rows (Task 2), scrape wiring + name fallback for unresolved (Task 4), `.xbox_cache` + offline tests (Task 3/5), Nintendo deferred (no task — by design; the `RESOLVERS` registry is the extension point). All covered.
- **No network in tests:** every resolver call in tests injects a fake `fetch`/resolver. The live `_fetch_products` is never exercised by tests.
- **`mark_ownership` untouched:** Task 4 routes only unresolved add-ons to it; its signature/behavior are unchanged, so existing PSN/Steam tests stay green.
- **Type consistency:** `_ensure_parent_game` returns `tuple[int | None, str]` (states `"id"/"backfill"/"created"/""`) — Task 1 tests and Task 2's pipeline both use that shape. `resolve_and_link(conn, source, platform, addons, resolver, *, create_missing=True)` signature is identical in Task 2's impl and Task 4's call site.
- **Idempotency / 0→1 only:** inherited from `apply_addon_to_parent`; pinned by `test_resolve_and_link_idempotent`.
