# UPC Enrichment — Plan A (Phase 1: Collection Backfill) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Proactively backfill retail UPCs for the owner's owned collection into `barcode_registry` via a throttled, idempotent daily-drip worker that auto-links confident matches and queues uncertain ones for a web Needs-review pass.

**Architecture:** A new `enrichment.py` module selects owned `(game, platform)` pairs with no known UPC, queries the keyless UPCitemdb name-search (`barcode.search_products_by_name`), classifies each result (confident / uncertain / no_match) with a pure classifier, and writes confident matches to `barcode_registry` (via the existing `barcode.registry_put`) or rows to a new `upc_review` table. A daemon thread in `background_tasks.py` runs at most one batch per UTC calendar day (mirroring the cover-fetch task pattern); a manual "Run a batch now" button + status polling + a Needs-review list live in `templates/settings.html`. New Flask routes expose run/status/review/confirm/reject.

**Tech Stack:** Python 3 / Flask / SQLite (stdlib `sqlite3`), `requests`, `uv` for env/deps, `pytest` for tests, `ruff` for lint. No new third-party dependencies.

## Global Constraints

- **Verified pre-flight (controller, 2026-06-23):** the UPCitemdb trial name-search `GET https://api.upcitemdb.com/prod/trial/search?s=<query>` works **keyless** → `HTTP 200`, returns `items:[{upc, title, ...}]`, and exposes `X-RateLimit-Limit: 100` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` headers. The `/search` and `/lookup` endpoints share one per-IP daily bucket of ~100. Plan A therefore (a) defaults the daily budget below 100 and (b) reads `X-RateLimit-Remaining` to stop precisely. **Phase 1 makes ZERO IGDB calls** — auto-linking uses the game's already-stored `igdb_id`/`cover_url`; only UPCitemdb name-search hits the network.
- **Tests:** run with `uv run python -m pytest` (NEVER plain `pytest` — fails `ModuleNotFoundError: models`). Lint with `ruff check` ONLY — NEVER `ruff format` (codebase is hand-aligned).
- **Migrations** must be idempotent and registered in BOTH `models.migrate_db()` and `tests/conftest.py::temp_db`, in the same relative order.
- **Subagents:** pytest temp-DB + static review ONLY. NEVER touch the live `games.db`, the running `:5000` server, the network, or the device. The controller does all live ops (server restart, browser verify, live curl).
- **App uses `use_reloader=False`** — Python route/migration changes need a manual server restart to take effect (controller does this in the gate task).
- **Error pattern:** these modules degrade/return (never raise out to the caller), matching `barcode.py`'s contract: external lookups return `[]`/`None` on failure and log a warning. Use specific exceptions (`requests.RequestException`, `ValueError`, `sqlite3.Error`), never bare `except:`. Every `except` logs.
- **Style:** type hints on all signatures; module-scope named constants for URLs/budgets/thresholds; `logging` not `print`; `frozenset`/tuples for immutable lookups.
- **Work directly on `main` and push** (no feature branches/PRs). Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: <session URL>
  ```

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `models.py` | `migrate_upc_review` + `migrate_upc_enrichment_state` table migrations; registered in `migrate_db()` | Modify |
| `tests/conftest.py` | register the two new migrations in `temp_db` | Modify |
| `barcode.py` | add `search_products_by_name()` + rate-limit header capture | Modify |
| `enrichment.py` | NEW — pure classifier, eligible-pair selection, state get/set, `run_batch` | Create |
| `background_tasks.py` | extend `TaskProgress`; `run_enrichment_background`, `get_enrichment_status`, daily-drip daemon + `should_run_today` helper | Modify |
| `app.py` | enrichment routes (run/status/review/confirm/reject) + boot the drip thread | Modify |
| `templates/settings.html` | "UPC Enrichment" section: trigger button, progress poll, Needs-review list | Modify |
| `tests/test_enrichment_migrations.py` | migration presence + idempotency | Create |
| `tests/test_barcode_search.py` | `search_products_by_name` parse/failure/header | Create |
| `tests/test_enrichment_classify.py` | pure classifier cases | Create |
| `tests/test_enrichment_select.py` | eligible-pair selection + state persistence | Create |
| `tests/test_enrichment_run_batch.py` | run_batch writes + idempotency + quota cap | Create |
| `tests/test_enrichment_status.py` | `should_run_today` + status dict + double-start guard | Create |
| `tests/test_api_enrichment.py` | the five routes via the `client` fixture | Create |

### Reference: existing schemas & signatures (verbatim, do not re-derive)

```
games(id INTEGER PK, title TEXT, normalized_title TEXT UNIQUE, cover_url TEXT, igdb_id INTEGER, ...)
platforms(id INTEGER PK, name TEXT UNIQUE, short_name TEXT UNIQUE, category TEXT DEFAULT 'modern_console')
game_platforms(game_id, platform_id, owned BOOLEAN DEFAULT 1, psprices_id TEXT, format TEXT NULL, PK(game_id,platform_id))
barcode_registry(upc PK, igdb_id, title, platform, cover_url, game_id, confirmed_at)
```

```python
# barcode.py — REUSE these (do not reimplement):
def clean_product_title(raw: str | None) -> str: ...        # strips retail noise
def parse_retail_platform(raw: str | None) -> str | None: ...  # title -> app short_name
def registry_put(conn, upc, *, igdb_id=None, title=None, platform=None,
                 cover_url=None, game_id=None) -> None: ...  # upsert UPC->game, COALESCE cover
def lookup_product_title(upc, *, url=UPCITEMDB_TRIAL_URL, timeout=UPC_LOOKUP_TIMEOUT) -> str | None: ...
UPCITEMDB_TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"   # NOTE: /lookup
UPC_LOOKUP_TIMEOUT = 8
# models.py:
def normalize_title(title: str) -> str: ...   # the canonical normalizer used by games.normalized_title
def get_db() -> sqlite3.Connection: ...        # row_factory=sqlite3.Row, foreign_keys ON
# background_tasks.py:
task_manager = TaskManager()  # .create_task / .get_task / .update_task(id, **kw) / .is_running(id)
# update_task only sets attrs that already exist on TaskProgress (hasattr guard).
```

**Mobile/subscription platforms have no physical retail UPC** (definitionally). Their `platforms.category` is `'mobile'` or `'subscription'` (seeded by `migrate_seed_extra_platforms`). The selection query excludes those two categories so the worker never burns quota on impossible pairs. This is category-driven (extensible), not a hardcoded platform list.

---

## Task 1: `upc_review` + `upc_enrichment_state` migrations

**Files:**
- Modify: `models.py` (add two migration functions near the other `migrate_*` defs ~line 995; register both in `migrate_db()` between the last `migrate_*` call and `backfill_series_source(conn)` ~line 1088)
- Modify: `tests/conftest.py` (add both calls in `temp_db` immediately before `models.seed_default_slots(conn)`)
- Test: `tests/test_enrichment_migrations.py`

**Interfaces:**
- Produces: `models.migrate_upc_review(conn)`, `models.migrate_upc_enrichment_state(conn)`. Tables `upc_review(id, game_id, platform, upc, product_title, cover_url, status, reason, created_at)` and `upc_enrichment_state(id, last_run_date, last_run_count)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_migrations.py`:

```python
"""upc_review + upc_enrichment_state migrations: present, shaped, idempotent."""
import models


def _cols(conn, table):
    return {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_upc_review_table_present_and_shaped(temp_db):
    conn = models.get_db()
    cols = _cols(conn, "upc_review")
    assert {"id", "game_id", "platform", "upc", "product_title",
            "cover_url", "status", "reason", "created_at"} <= cols
    conn.close()


def test_upc_enrichment_state_table_present(temp_db):
    conn = models.get_db()
    cols = _cols(conn, "upc_enrichment_state")
    assert {"id", "last_run_date", "last_run_count"} <= cols
    conn.close()


def test_status_check_constraint_rejects_bad_status(temp_db):
    import sqlite3
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    gid = conn.execute("SELECT id FROM games WHERE normalized_title='g'").fetchone()[0]
    try:
        conn.execute(
            "INSERT INTO upc_review (game_id, platform, status) VALUES (?, 'Switch', 'bogus')",
            (gid,))
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "CHECK(status IN (...)) should reject an unknown status"
    conn.close()


def test_migrations_are_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_upc_review(conn)          # second run is a no-op
    models.migrate_upc_enrichment_state(conn)
    assert _cols(conn, "upc_review")          # still present, no error
    assert _cols(conn, "upc_enrichment_state")
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_enrichment_migrations.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: upc_review` (and `migrate_upc_review` AttributeError).

- [ ] **Step 3: Write minimal implementation**

In `models.py`, add near the other migration helpers (e.g. just after `migrate_game_platform_format`, ~line 1011):

```python
def migrate_upc_review(conn: sqlite3.Connection) -> None:
    """Create the upc_review table if missing. Idempotent.

    Doubles as the enrichment review queue (status='pending') and the
    dedup/attempt ledger ('no_match' attempted, 'dismissed' rejected). Confirmed
    links live in barcode_registry, not here.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upc_review (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id       INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            platform      TEXT    NOT NULL,
            upc           TEXT,
            product_title TEXT,
            cover_url     TEXT,
            status        TEXT NOT NULL CHECK(status IN ('pending', 'no_match', 'dismissed')),
            reason        TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_upc_review_game_platform
            ON upc_review(game_id, platform)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_upc_review_pending
            ON upc_review(status)
    """)
    conn.commit()


def migrate_upc_enrichment_state(conn: sqlite3.Connection) -> None:
    """Create the single-row upc_enrichment_state table if missing. Idempotent.

    Holds the daily-drip bookkeeping: last UTC date a batch ran + calls used that
    day (shared per-IP UPCitemdb quota). One row, id=1, seeded on create.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upc_enrichment_state (
            id             INTEGER PRIMARY KEY CHECK(id = 1),
            last_run_date  TEXT,
            last_run_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO upc_enrichment_state (id, last_run_date, last_run_count) "
        "VALUES (1, NULL, 0)")
    conn.commit()
```

In `models.migrate_db()`, add these two calls immediately before `backfill_series_source(conn)` (the line after the last `migrate_*` call, ~line 1088):

```python
    migrate_upc_review(conn)
    migrate_upc_enrichment_state(conn)
```

In `tests/conftest.py`, add both calls in `temp_db` immediately before `models.seed_default_slots(conn)`:

```python
    models.migrate_upc_review(conn)
    models.migrate_upc_enrichment_state(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_enrichment_migrations.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check models.py tests/conftest.py tests/test_enrichment_migrations.py
git add models.py tests/conftest.py tests/test_enrichment_migrations.py
git commit -m "feat(enrichment): upc_review + upc_enrichment_state migrations"
```

---

## Task 2: `barcode.search_products_by_name` + rate-limit capture

**Files:**
- Modify: `barcode.py` (add a name-search URL constant near `UPCITEMDB_TRIAL_URL` ~line 19; add a module global `_last_rate_remaining`; add `search_products_by_name` + `last_rate_remaining` near `lookup_product_title` ~line 99)
- Test: `tests/test_barcode_search.py`

**Interfaces:**
- Consumes: `requests`, `log` (existing module logger).
- Produces:
  - `barcode.UPCITEMDB_SEARCH_URL = "https://api.upcitemdb.com/prod/trial/search"`
  - `barcode.search_products_by_name(query: str, *, url=UPCITEMDB_SEARCH_URL, timeout=UPC_LOOKUP_TIMEOUT) -> list[dict]` → `[{"title": str, "upc": str}, ...]` (skips items with no upc); `[]` on any failure (never raises). Captures `X-RateLimit-Remaining` into `_last_rate_remaining` on success.
  - `barcode.last_rate_remaining() -> int | None` → last-seen remaining trial quota, or `None` if unknown.

- [ ] **Step 1: Write the failing test**

Create `tests/test_barcode_search.py`:

```python
"""barcode.search_products_by_name: parse, failure->[], rate-limit capture."""
import requests

import barcode


class _Resp:
    def __init__(self, payload, headers=None, exc=None):
        self._payload = payload
        self.headers = headers or {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


def test_parses_items_into_title_upc(monkeypatch):
    payload = {"code": "OK", "items": [
        {"title": "Mario Kart 8 Deluxe (Nintendo Switch)", "upc": "045496590475"},
        {"title": "No UPC here", "ean": "x"},  # skipped: no upc
        {"title": "Animal Crossing New Horizons", "upc": "045496596439"},
    ]}
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(payload, {"X-RateLimit-Remaining": "68"}))
    out = barcode.search_products_by_name("Mario Kart 8 Deluxe")
    assert out == [
        {"title": "Mario Kart 8 Deluxe (Nintendo Switch)", "upc": "045496590475"},
        {"title": "Animal Crossing New Horizons", "upc": "045496596439"},
    ]
    assert barcode.last_rate_remaining() == 68


def test_network_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.search_products_by_name("anything") == []


def test_bad_json_returns_empty(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(None, exc=ValueError("bad json")))
    assert barcode.search_products_by_name("anything") == []


def test_missing_header_leaves_remaining_none(monkeypatch):
    barcode._last_rate_remaining = None  # reset module state for the assertion
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp({"items": []}, {}))
    barcode.search_products_by_name("q")
    assert barcode.last_rate_remaining() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode_search.py -v`
Expected: FAIL — `AttributeError: module 'barcode' has no attribute 'search_products_by_name'`.

- [ ] **Step 3: Write minimal implementation**

In `barcode.py`, add the URL constant next to `UPCITEMDB_TRIAL_URL`:

```python
# Free UPCitemdb trial name-search: shares the ~100/day per-IP quota with /lookup.
UPCITEMDB_SEARCH_URL = "https://api.upcitemdb.com/prod/trial/search"
```

Add a module global near the other module-level state (top of file, after constants):

```python
# Last X-RateLimit-Remaining seen from a UPCitemdb call (shared trial quota), or None.
_last_rate_remaining: int | None = None
```

Add the functions near `lookup_product_title`:

```python
def last_rate_remaining() -> int | None:
    """Last-seen UPCitemdb trial quota remaining (X-RateLimit-Remaining), or None."""
    return _last_rate_remaining


def search_products_by_name(query: str, *, url: str = UPCITEMDB_SEARCH_URL,
                            timeout: int = UPC_LOOKUP_TIMEOUT) -> list[dict]:
    """Return [{title, upc}, ...] for a UPCitemdb name-search, or [] on failure.

    Counts against the shared trial quota; captures X-RateLimit-Remaining into
    the module global. Network/parse failures log and degrade to [] (never raise).
    """
    global _last_rate_remaining
    try:
        resp = requests.get(url, params={"s": query}, timeout=timeout)
        resp.raise_for_status()
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                _last_rate_remaining = int(remaining)
            except (TypeError, ValueError):
                _last_rate_remaining = None
        items = resp.json().get("items") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("UPC name-search failed for %r: %s", query, exc)
        return []
    out: list[dict] = []
    for it in items:
        upc = (it.get("upc") or "").strip()
        title = (it.get("title") or "").strip()
        if upc and title:
            out.append({"title": title, "upc": upc})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_barcode_search.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check barcode.py tests/test_barcode_search.py
git add barcode.py tests/test_barcode_search.py
git commit -m "feat(enrichment): barcode.search_products_by_name + rate-limit capture"
```

---

## Task 3: `enrichment.classify_match` (pure confidence classifier)

**Files:**
- Create: `enrichment.py`
- Test: `tests/test_enrichment_classify.py`

**Interfaces:**
- Consumes: `barcode.clean_product_title`, `barcode.parse_retail_platform`, `models.normalize_title`.
- Produces:
  - `enrichment.classify_match(normalized_title: str, short_name: str, products: list[dict]) -> dict`
    returns `{"status": "confident"|"uncertain"|"no_match", "upc": str|None, "product_title": str|None, "reason": str|None}`.
  - Constants `enrichment.CONFIDENT`, `UNCERTAIN`, `NO_MATCH` (the status strings).

**Classifier rules (strict — wrong UPCs must never auto-link):**
- **confident**: a product whose `normalize_title(clean_product_title(title)) == normalized_title` AND (`parse_retail_platform(title)` is `None` OR equals `short_name`). First such product wins (any correct UPC for the same game+platform resolves to the same game).
- **uncertain**: no confident hit, but a product is *close*: exact normalized-title match with a *mismatched* platform, OR one normalized title contains the other (both length ≥ 4). First such wins; reason records why.
- **no_match**: nothing plausible.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_classify.py`:

```python
"""enrichment.classify_match: confident / uncertain / no_match (pure, no I/O)."""
import enrichment
import models


def _nt(title):
    return models.normalize_title(title)


def test_confident_exact_title_and_platform():
    res = enrichment.classify_match(
        _nt("Mario Kart 8 Deluxe"), "Switch",
        [{"title": "Mario Kart 8 Deluxe (Nintendo Switch)", "upc": "045496590475"}])
    assert res["status"] == enrichment.CONFIDENT
    assert res["upc"] == "045496590475"


def test_confident_when_product_names_no_platform():
    res = enrichment.classify_match(
        _nt("Hades"), "Switch",
        [{"title": "Hades", "upc": "810017710003"}])
    assert res["status"] == enrichment.CONFIDENT
    assert res["upc"] == "810017710003"


def test_confident_picks_first_of_multiple_exact():
    res = enrichment.classify_match(
        _nt("Celeste"), "Switch",
        [{"title": "Celeste (Nintendo Switch)", "upc": "AAA"},
         {"title": "Celeste Nintendo Switch", "upc": "BBB"}])
    assert res["status"] == enrichment.CONFIDENT
    assert res["upc"] == "AAA"


def test_uncertain_exact_title_wrong_platform():
    res = enrichment.classify_match(
        _nt("Doom Eternal"), "Switch",
        [{"title": "Doom Eternal (PlayStation 5)", "upc": "CCC"}])
    assert res["status"] == enrichment.UNCERTAIN
    assert res["upc"] == "CCC"
    assert "platform" in (res["reason"] or "").lower()


def test_uncertain_partial_title_containment():
    res = enrichment.classify_match(
        _nt("Zelda Tears of the Kingdom"), "Switch",
        [{"title": "The Legend of Zelda Tears of the Kingdom Collector Edition", "upc": "DDD"}])
    assert res["status"] == enrichment.UNCERTAIN
    assert res["upc"] == "DDD"


def test_no_match_when_nothing_close():
    res = enrichment.classify_match(
        _nt("Stardew Valley"), "Switch",
        [{"title": "USB-C Charging Cable 3-pack", "upc": "EEE"}])
    assert res["status"] == enrichment.NO_MATCH
    assert res["upc"] is None


def test_no_match_on_empty_products():
    res = enrichment.classify_match(_nt("Anything"), "PS5", [])
    assert res["status"] == enrichment.NO_MATCH
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_enrichment_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrichment'`.

- [ ] **Step 3: Write minimal implementation**

Create `enrichment.py`:

```python
"""Collection UPC backfill worker.

Selects owned (game, platform) pairs lacking a known UPC, name-searches
UPCitemdb, classifies each result, and writes confident matches to
barcode_registry or rows to upc_review. External calls go through barcode.py
(which degrades to []/None); this module never raises out to its caller.
"""
import logging
import sqlite3

import barcode
import models

log = logging.getLogger(__name__)

CONFIDENT = "confident"
UNCERTAIN = "uncertain"
NO_MATCH = "no_match"

# Minimum normalized-title length for the containment heuristic (avoids "go"/"a").
_MIN_CONTAIN_LEN = 4


def classify_match(normalized_title: str, short_name: str,
                   products: list[dict]) -> dict:
    """Classify name-search products against an owned game+platform.

    confident: exact normalized-title match AND platform matches (or product
        names no platform) -> auto-linkable.
    uncertain: exact title with a mismatched platform, or a normalized-title
        containment near-match -> needs human review.
    no_match: nothing plausible.
    Returns {status, upc, product_title, reason}.
    """
    uncertain: dict | None = None
    for p in products:
        raw = p.get("title") or ""
        upc = (p.get("upc") or "").strip()
        if not upc:
            continue
        clean = barcode.clean_product_title(raw)
        prod_nt = models.normalize_title(clean)
        prod_plat = barcode.parse_retail_platform(raw)
        if prod_nt == normalized_title and (prod_plat is None or prod_plat == short_name):
            return {"status": CONFIDENT, "upc": upc, "product_title": clean, "reason": None}
        if uncertain is None:
            if prod_nt == normalized_title and prod_plat and prod_plat != short_name:
                uncertain = {"status": UNCERTAIN, "upc": upc, "product_title": clean,
                             "reason": f"platform mismatch: product names {prod_plat}"}
            elif (len(prod_nt) >= _MIN_CONTAIN_LEN
                  and len(normalized_title) >= _MIN_CONTAIN_LEN
                  and (prod_nt in normalized_title or normalized_title in prod_nt)):
                uncertain = {"status": UNCERTAIN, "upc": upc, "product_title": clean,
                             "reason": "near title match"}
    if uncertain is not None:
        return uncertain
    return {"status": NO_MATCH, "upc": None, "product_title": None, "reason": "no plausible product"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_enrichment_classify.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check enrichment.py tests/test_enrichment_classify.py
git add enrichment.py tests/test_enrichment_classify.py
git commit -m "feat(enrichment): pure classify_match confidence classifier"
```

---

## Task 4: eligible-pair selection + enrichment-state helpers

**Files:**
- Modify: `enrichment.py`
- Test: `tests/test_enrichment_select.py`

**Interfaces:**
- Consumes: `models.get_db` (tests use the `temp_db` fixture + `models.get_db()`).
- Produces:
  - `enrichment.select_eligible_pairs(conn: sqlite3.Connection, *, limit: int | None = None) -> list[sqlite3.Row]`
    each row: `id, title, normalized_title, igdb_id, cover_url, short_name`. Excludes pairs that already have a `barcode_registry` row for that `(game_id, platform)`, OR any `upc_review` row for that `(game_id, platform)`, OR whose platform `category` is `'mobile'`/`'subscription'`. Only `owned = 1` pairs. Ordered by `games.id, short_name` (stable). `limit` caps the row count.
  - `enrichment.count_eligible_pairs(conn) -> int`
  - `enrichment.get_enrichment_state(conn) -> dict` → `{"last_run_date": str|None, "last_run_count": int}`
  - `enrichment.set_enrichment_state(conn, *, last_run_date: str, last_run_count: int) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_select.py`:

```python
"""enrichment.select_eligible_pairs + state helpers (uses temp_db)."""
import enrichment
import models


def _platform(conn, name, short, category="modern_console"):
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category))
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (short,)).fetchone()[0]


def _game(conn, title, *, igdb_id=None, cover="c.jpg"):
    conn.execute(
        "INSERT INTO games (title, normalized_title, igdb_id, cover_url) VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(title), igdb_id, cover))
    return conn.execute("SELECT id FROM games WHERE normalized_title=?",
                        (models.normalize_title(title),)).fetchone()[0]


def _own(conn, gid, pid):
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?, ?, 1)",
                 (gid, pid))


def test_selects_owned_pairs_without_upc(temp_db):
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    g = _game(conn, "Hades", igdb_id=7)
    _own(conn, g, sw)
    conn.commit()
    pairs = enrichment.select_eligible_pairs(conn)
    assert [(r["title"], r["short_name"], r["igdb_id"]) for r in pairs] == [("Hades", "Switch", 7)]
    conn.close()


def test_excludes_pair_already_in_registry(temp_db):
    import barcode
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    g = _game(conn, "Hades")
    _own(conn, g, sw)
    barcode.registry_put(conn, "111", game_id=g, platform="Switch", title="Hades")
    conn.commit()
    assert enrichment.select_eligible_pairs(conn) == []
    conn.close()


def test_excludes_pair_with_any_review_row(temp_db):
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    g = _game(conn, "Hades")
    _own(conn, g, sw)
    conn.execute("INSERT INTO upc_review (game_id, platform, status, reason) "
                 "VALUES (?, 'Switch', 'no_match', 'x')", (g,))
    conn.commit()
    assert enrichment.select_eligible_pairs(conn) == []
    conn.close()


def test_excludes_mobile_and_subscription_platforms(temp_db):
    conn = models.get_db()
    ios = _platform(conn, "iOS", "iOS", category="mobile")
    gp = _platform(conn, "Game Pass", "GamePass", category="subscription")
    g = _game(conn, "Hades")
    _own(conn, g, ios)
    _own(conn, g, gp)
    conn.commit()
    assert enrichment.select_eligible_pairs(conn) == []
    conn.close()


def test_limit_caps_rows(temp_db):
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    for t in ("A", "B", "C"):
        _own(conn, _game(conn, t), sw)
    conn.commit()
    assert len(enrichment.select_eligible_pairs(conn, limit=2)) == 2
    assert enrichment.count_eligible_pairs(conn) == 3
    conn.close()


def test_state_round_trips(temp_db):
    conn = models.get_db()
    assert enrichment.get_enrichment_state(conn) == {"last_run_date": None, "last_run_count": 0}
    enrichment.set_enrichment_state(conn, last_run_date="2026-06-23", last_run_count=42)
    assert enrichment.get_enrichment_state(conn) == {"last_run_date": "2026-06-23", "last_run_count": 42}
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_enrichment_select.py -v`
Expected: FAIL — `AttributeError: module 'enrichment' has no attribute 'select_eligible_pairs'`.

- [ ] **Step 3: Write minimal implementation**

Append to `enrichment.py`:

```python
# Platform categories that can never have a physical retail UPC.
_NON_RETAIL_CATEGORIES = ("mobile", "subscription")

_ELIGIBLE_SQL = """
    SELECT g.id, g.title, g.normalized_title, g.igdb_id, g.cover_url, p.short_name
    FROM games g
    JOIN game_platforms gp ON gp.game_id = g.id
    JOIN platforms p ON p.id = gp.platform_id
    WHERE gp.owned = 1
      AND p.category NOT IN ('mobile', 'subscription')
      AND NOT EXISTS (SELECT 1 FROM barcode_registry br
                      WHERE br.game_id = g.id AND br.platform = p.short_name)
      AND NOT EXISTS (SELECT 1 FROM upc_review ur
                      WHERE ur.game_id = g.id AND ur.platform = p.short_name)
    ORDER BY g.id, p.short_name
"""


def select_eligible_pairs(conn: sqlite3.Connection, *,
                          limit: int | None = None) -> list[sqlite3.Row]:
    """Owned (game, platform) pairs with no known UPC and no review row.

    Excludes mobile/subscription platforms (no physical retail UPC exists).
    Idempotent: covered/queued/attempted/dismissed pairs are all skipped.
    """
    sql = _ELIGIBLE_SQL
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def count_eligible_pairs(conn: sqlite3.Connection) -> int:
    """How many eligible pairs remain (for the status display)."""
    return conn.execute(
        f"SELECT COUNT(*) FROM ({_ELIGIBLE_SQL})").fetchone()[0]


def get_enrichment_state(conn: sqlite3.Connection) -> dict:
    """The single drip-state row as a dict (seeded by the migration)."""
    row = conn.execute(
        "SELECT last_run_date, last_run_count FROM upc_enrichment_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"last_run_date": None, "last_run_count": 0}
    return {"last_run_date": row["last_run_date"], "last_run_count": row["last_run_count"]}


def set_enrichment_state(conn: sqlite3.Connection, *, last_run_date: str,
                         last_run_count: int) -> None:
    """Persist the last drip run date + per-day call count."""
    conn.execute(
        "UPDATE upc_enrichment_state SET last_run_date = ?, last_run_count = ? WHERE id = 1",
        (last_run_date, last_run_count))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_enrichment_select.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check enrichment.py tests/test_enrichment_select.py
git add enrichment.py tests/test_enrichment_select.py
git commit -m "feat(enrichment): eligible-pair selection + drip-state helpers"
```

---

## Task 5: `enrichment.run_batch` (selection + quota + writes)

**Files:**
- Modify: `enrichment.py`
- Test: `tests/test_enrichment_run_batch.py`

**Interfaces:**
- Consumes: `select_eligible_pairs`, `classify_match`, `barcode.registry_put`, `barcode.search_products_by_name`, `barcode.last_rate_remaining`.
- Produces:
  - `enrichment.UPC_ENRICH_DAILY_BUDGET = 90`
  - `enrichment.UPC_ENRICH_QUOTA_SAFETY_MARGIN = 5`
  - `enrichment.run_batch(conn, *, budget=UPC_ENRICH_DAILY_BUDGET, search_fn=barcode.search_products_by_name, remaining_fn=barcode.last_rate_remaining, progress=None) -> dict`
    returns `{"found": int, "queued": int, "no_match": int, "calls_used": int, "total": int}`.
    - For each eligible pair (capped at `budget`): call `search_fn(title)`, `classify_match(...)`, then:
      - confident → `registry_put(conn, upc, igdb_id=row.igdb_id, title=row.title, platform=short_name, game_id=row.id, cover_url=row.cover_url)`; `found += 1`.
      - uncertain → insert `upc_review` `status='pending'`; `queued += 1`.
      - no_match → insert `upc_review` `status='no_match'` (upc/product NULL, keep cover for the UI); `no_match += 1`.
    - Stops when `budget` calls made OR `remaining_fn()` (if not None) drops to `<= UPC_ENRICH_QUOTA_SAFETY_MARGIN`.
    - `progress(done, total, found, queued, no_match)` called after each pair if provided.
    - Commits after each write so a crash mid-batch never loses progress.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_run_batch.py`:

```python
"""enrichment.run_batch: writes, idempotency, quota cap (uses temp_db)."""
import enrichment
import models


def _setup(conn, title, short, *, igdb_id=5, category="modern_console"):
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
                 (short, short, category))
    pid = conn.execute("SELECT id FROM platforms WHERE short_name=?", (short,)).fetchone()[0]
    conn.execute("INSERT INTO games (title, normalized_title, igdb_id, cover_url) VALUES (?, ?, ?, ?)",
                 (title, models.normalize_title(title), igdb_id, "cov.jpg"))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title=?",
                       (models.normalize_title(title),)).fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?, ?, 1)",
                 (gid, pid))
    conn.commit()
    return gid


def test_confident_writes_registry(temp_db):
    conn = models.get_db()
    gid = _setup(conn, "Hades", "Switch", igdb_id=42)
    res = enrichment.run_batch(
        conn, search_fn=lambda q: [{"title": "Hades (Nintendo Switch)", "upc": "999"}],
        remaining_fn=lambda: None)
    assert res["found"] == 1
    row = conn.execute("SELECT game_id, igdb_id, platform, cover_url FROM barcode_registry "
                       "WHERE upc='999'").fetchone()
    assert (row["game_id"], row["igdb_id"], row["platform"], row["cover_url"]) == (gid, 42, "Switch", "cov.jpg")
    conn.close()


def test_uncertain_writes_pending_review(temp_db):
    conn = models.get_db()
    gid = _setup(conn, "Doom Eternal", "Switch")
    res = enrichment.run_batch(
        conn, search_fn=lambda q: [{"title": "Doom Eternal (PlayStation 5)", "upc": "777"}],
        remaining_fn=lambda: None)
    assert res["queued"] == 1
    row = conn.execute("SELECT status, upc, game_id FROM upc_review WHERE game_id=?", (gid,)).fetchone()
    assert (row["status"], row["upc"]) == ("pending", "777")
    conn.close()


def test_no_match_writes_no_match_row(temp_db):
    conn = models.get_db()
    gid = _setup(conn, "Stardew Valley", "Switch")
    res = enrichment.run_batch(
        conn, search_fn=lambda q: [{"title": "USB Cable", "upc": "1"}], remaining_fn=lambda: None)
    assert res["no_match"] == 1
    row = conn.execute("SELECT status, upc FROM upc_review WHERE game_id=?", (gid,)).fetchone()
    assert (row["status"], row["upc"]) == ("no_match", None)
    conn.close()


def test_rerun_is_idempotent_no_duplicate_work(temp_db):
    conn = models.get_db()
    _setup(conn, "Hades", "Switch")
    calls = []
    fn = lambda q: (calls.append(q) or [{"title": "Hades (Nintendo Switch)", "upc": "999"}])
    enrichment.run_batch(conn, search_fn=fn, remaining_fn=lambda: None)
    enrichment.run_batch(conn, search_fn=fn, remaining_fn=lambda: None)  # nothing eligible now
    assert len(calls) == 1  # second batch selected nothing
    conn.close()


def test_budget_caps_calls(temp_db):
    conn = models.get_db()
    for t in ("A", "B", "C", "D"):
        _setup(conn, t, "Switch")
    calls = []
    fn = lambda q: (calls.append(q) or [{"title": "x", "upc": "z"}])
    res = enrichment.run_batch(conn, budget=2, search_fn=fn, remaining_fn=lambda: None)
    assert len(calls) == 2 and res["calls_used"] == 2
    conn.close()


def test_stops_when_remaining_quota_low(temp_db):
    conn = models.get_db()
    for t in ("A", "B", "C"):
        _setup(conn, t, "Switch")
    calls = []
    # remaining drops to the safety margin after the first call -> stop before the 2nd
    seq = iter([enrichment.UPC_ENRICH_QUOTA_SAFETY_MARGIN])
    fn = lambda q: (calls.append(q) or [{"title": "x", "upc": "z"}])
    res = enrichment.run_batch(conn, budget=10, search_fn=fn,
                               remaining_fn=lambda: next(seq, enrichment.UPC_ENRICH_QUOTA_SAFETY_MARGIN))
    assert len(calls) == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_enrichment_run_batch.py -v`
Expected: FAIL — `AttributeError: module 'enrichment' has no attribute 'run_batch'`.

- [ ] **Step 3: Write minimal implementation**

Append to `enrichment.py` (add `UPC_ENRICH_DAILY_BUDGET`/`UPC_ENRICH_QUOTA_SAFETY_MARGIN` near the top constants):

```python
UPC_ENRICH_DAILY_BUDGET = 90          # < 100/day trial cap (shared per-IP bucket)
UPC_ENRICH_QUOTA_SAFETY_MARGIN = 5    # stop if live remaining drops to/below this
```

```python
def run_batch(conn: sqlite3.Connection, *, budget: int = UPC_ENRICH_DAILY_BUDGET,
              search_fn=barcode.search_products_by_name,
              remaining_fn=barcode.last_rate_remaining, progress=None) -> dict:
    """Run one throttled enrichment batch. Idempotent + resumable.

    Selects up to `budget` eligible pairs, name-searches each, and writes a
    confident registry link / pending review / no_match row. Stops early if the
    live trial quota (remaining_fn) drops to the safety margin. Commits per write.
    """
    pairs = select_eligible_pairs(conn, limit=budget)
    total = len(pairs)
    found = queued = no_match = calls_used = 0
    for row in pairs:
        if calls_used >= budget:
            break
        live = remaining_fn()
        if live is not None and live <= UPC_ENRICH_QUOTA_SAFETY_MARGIN:
            log.warning("UPC enrichment stopping: trial quota remaining=%s", live)
            break
        products = search_fn(row["title"])
        calls_used += 1
        verdict = classify_match(row["normalized_title"], row["short_name"], products)
        status = verdict["status"]
        if status == CONFIDENT:
            barcode.registry_put(conn, verdict["upc"], igdb_id=row["igdb_id"],
                                 title=row["title"], platform=row["short_name"],
                                 game_id=row["id"], cover_url=row["cover_url"])
            found += 1
        elif status == UNCERTAIN:
            conn.execute(
                "INSERT INTO upc_review (game_id, platform, upc, product_title, "
                "cover_url, status, reason) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (row["id"], row["short_name"], verdict["upc"], verdict["product_title"],
                 row["cover_url"], verdict["reason"]))
            queued += 1
        else:
            conn.execute(
                "INSERT INTO upc_review (game_id, platform, upc, product_title, "
                "cover_url, status, reason) VALUES (?, ?, NULL, NULL, ?, 'no_match', ?)",
                (row["id"], row["short_name"], row["cover_url"], verdict["reason"]))
            no_match += 1
        conn.commit()
        if progress is not None:
            progress(calls_used, total, found, queued, no_match)
    return {"found": found, "queued": queued, "no_match": no_match,
            "calls_used": calls_used, "total": total}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_enrichment_run_batch.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check enrichment.py tests/test_enrichment_run_batch.py
git add enrichment.py tests/test_enrichment_run_batch.py
git commit -m "feat(enrichment): run_batch with idempotent selection + quota cap"
```

---

## Task 6: background launcher, status, daily-drip daemon

**Files:**
- Modify: `background_tasks.py` (extend `TaskProgress`; add `run_enrichment_background`, `get_enrichment_status`, `should_run_today`, `start_enrichment_drip`)
- Test: `tests/test_enrichment_status.py`

**Interfaces:**
- Consumes: `enrichment.run_batch`, `enrichment.get_enrichment_state`, `enrichment.set_enrichment_state`, `enrichment.count_eligible_pairs`, `models.get_db`.
- Produces:
  - extra `TaskProgress` fields: `queued:int=0`, `no_match:int=0`, `last_run_date:Optional[str]=None`, `remaining_eligible:int=0`.
  - `background_tasks.ENRICH_TASK_ID = "upc_enrichment"`
  - `should_run_today(last_run_date: str | None, today: str) -> bool` → `last_run_date != today`.
  - `run_enrichment_background(budget: int, *, db_factory=models.get_db) -> tuple[bool, str]` (mirrors `run_cover_fetch_background`: `is_running` guard, daemon thread, status updates, records state on completion).
  - `get_enrichment_status() -> dict` → `{status, current, total, found, queued, no_match, error, started_at, completed_at}` (or `{"status": "idle"}`).
  - `start_enrichment_drip(*, db_factory=models.get_db, sleep_fn=time.sleep, today_fn=...) -> threading.Thread` — daemon loop: on each wake, if `should_run_today(state.last_run_date, today)`, compute `remaining_today = budget - (count if same UTC day)`, run a batch, persist state; then sleep.

**Note on testability:** the daemon loop itself is thin and not unit-tested (like the existing cover thread). The once-per-day decision is extracted into the pure `should_run_today` and IS tested. The status dict + double-start guard are tested by driving `run_enrichment_background` with an injected fast `db_factory` + a deterministic `enrichment.run_batch` monkeypatch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrichment_status.py`:

```python
"""background_tasks enrichment launcher + status + drip gate."""
import time

import background_tasks
import enrichment
import models


def test_should_run_today_gate():
    assert background_tasks.should_run_today(None, "2026-06-23") is True
    assert background_tasks.should_run_today("2026-06-22", "2026-06-23") is True
    assert background_tasks.should_run_today("2026-06-23", "2026-06-23") is False


def test_status_idle_before_any_run():
    # Fresh task id never created -> idle
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)
    assert background_tasks.get_enrichment_status() == {"status": "idle"}


def test_run_enrichment_background_runs_and_reports(temp_db, monkeypatch):
    # Deterministic batch: no network. Patch run_batch to a quick canned result.
    def fake_batch(conn, *, budget, search_fn=None, remaining_fn=None, progress=None):
        if progress:
            progress(1, 1, 1, 0, 0)
        return {"found": 1, "queued": 0, "no_match": 0, "calls_used": 1, "total": 1}
    monkeypatch.setattr(enrichment, "run_batch", fake_batch)
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)

    ok, msg = background_tasks.run_enrichment_background(90, db_factory=models.get_db)
    assert ok is True
    # Wait briefly for the daemon thread to finish.
    for _ in range(50):
        st = background_tasks.get_enrichment_status()
        if st["status"] in ("complete", "error"):
            break
        time.sleep(0.02)
    st = background_tasks.get_enrichment_status()
    assert st["status"] == "complete"
    assert st["found"] == 1


def test_double_start_guarded(monkeypatch):
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)
    task = background_tasks.task_manager.create_task(background_tasks.ENRICH_TASK_ID)
    task.status = "running"
    ok, msg = background_tasks.run_enrichment_background(90, db_factory=models.get_db)
    assert ok is False and "progress" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_enrichment_status.py -v`
Expected: FAIL — `AttributeError: module 'background_tasks' has no attribute 'should_run_today'`.

- [ ] **Step 3: Write minimal implementation**

In `background_tasks.py`:

Extend the `TaskProgress` dataclass with the new optional fields (keep existing fields):

```python
    queued: int = 0
    no_match: int = 0
    last_run_date: Optional[str] = None
    remaining_eligible: int = 0
```

Add imports at the top (alongside the existing ones):

```python
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)
```

Add the launcher + status + drip:

```python
ENRICH_TASK_ID = "upc_enrichment"
DRIP_SLEEP_SECONDS = 3 * 60 * 60   # re-check the daily gate every 3 hours


def should_run_today(last_run_date: str | None, today: str) -> bool:
    """The drip runs at most one batch per UTC calendar day."""
    return last_run_date != today


def get_enrichment_status() -> dict:
    """Current enrichment task status (mirrors get_cover_fetch_status)."""
    task = task_manager.get_task(ENRICH_TASK_ID)
    if not task:
        return {"status": "idle"}
    return {
        "status": task.status,
        "current": task.current,
        "total": task.total,
        "found": task.found,
        "queued": task.queued,
        "no_match": task.no_match,
        "error": task.error,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def run_enrichment_background(budget: int, *, db_factory=None) -> tuple[bool, str]:
    """Start one enrichment batch in a daemon thread. Mirrors cover-fetch."""
    import enrichment
    import models
    db_factory = db_factory or models.get_db

    if task_manager.is_running(ENRICH_TASK_ID):
        return False, "Enrichment already in progress"
    if budget <= 0:
        return False, "Daily quota exhausted"

    task = task_manager.create_task(ENRICH_TASK_ID)
    task.status = "running"
    task.started_at = datetime.now()

    def do_run():
        try:
            conn = db_factory()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            state = enrichment.get_enrichment_state(conn)
            already = state["last_run_count"] if state["last_run_date"] == today else 0

            def progress(done, total, found, queued, no_match):
                task_manager.update_task(ENRICH_TASK_ID, current=done, total=total,
                                         found=found, queued=queued, no_match=no_match)

            result = enrichment.run_batch(conn, budget=budget, progress=progress)
            enrichment.set_enrichment_state(
                conn, last_run_date=today, last_run_count=already + result["calls_used"])
            task_manager.update_task(
                ENRICH_TASK_ID, status="complete", completed_at=datetime.now(),
                found=result["found"], queued=result["queued"], no_match=result["no_match"],
                current=result["calls_used"], total=result["total"])
            conn.close()
        except Exception as exc:  # daemon must never crash the app; logged + isolated
            log.exception("UPC enrichment batch failed")
            task_manager.update_task(ENRICH_TASK_ID, status="error", error=str(exc),
                                     completed_at=datetime.now())

    threading.Thread(target=do_run, daemon=True).start()
    return True, "Enrichment started"


def start_enrichment_drip(*, db_factory=None, sleep_fn=time.sleep) -> threading.Thread:
    """Daemon: at most one batch per UTC day, re-checking every few hours."""
    import enrichment
    import models
    db_factory = db_factory or models.get_db

    def loop():
        while True:
            try:
                conn = db_factory()
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                state = enrichment.get_enrichment_state(conn)
                conn.close()
                if should_run_today(state["last_run_date"], today):
                    from enrichment import UPC_ENRICH_DAILY_BUDGET
                    run_enrichment_background(UPC_ENRICH_DAILY_BUDGET, db_factory=db_factory)
            except Exception:
                log.exception("UPC enrichment drip tick failed")
            sleep_fn(DRIP_SLEEP_SECONDS)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_enrichment_status.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check background_tasks.py tests/test_enrichment_status.py
git add background_tasks.py tests/test_enrichment_status.py
git commit -m "feat(enrichment): background launcher, status, daily-drip daemon"
```

---

## Task 7: Flask routes (run / status / review / confirm / reject)

**Files:**
- Modify: `app.py` (add five routes near the cover-fetch routes ~line 2248; import `enrichment` + the new `background_tasks` functions; start the drip in the boot block ~line 2306)
- Test: `tests/test_api_enrichment.py`

**Interfaces:**
- Consumes: `background_tasks.run_enrichment_background`, `background_tasks.get_enrichment_status`, `enrichment.count_eligible_pairs`, `enrichment.get_enrichment_state`, `enrichment.UPC_ENRICH_DAILY_BUDGET`, `barcode.registry_put`, `models.get_db`.
- Produces routes:
  - `POST /api/enrichment/run` → computes `remaining_today` from state vs UTC today; calls `run_enrichment_background(remaining_today)`; `200 {success, message}` / `409 {error}` if running / `200 {success:false, message:"Daily quota exhausted"}` if `remaining_today<=0`.
  - `GET /api/enrichment/status` → `get_enrichment_status()` augmented with `last_run_date`, `remaining_eligible`, `remaining_today`.
  - `GET /api/enrichment/review` → `{candidates:[{id, game_id, title, platform, upc, product_title, cover_url, reason}]}` for `status='pending'`, joined to `games`.
  - `POST /api/enrichment/review/<int:rid>/confirm` → `registry_put(...)` from the row + game, delete the row, `200 {success:true}`; `404` if no pending row.
  - `POST /api/enrichment/review/<int:rid>/reject` → `UPDATE upc_review SET status='dismissed'`, `200 {success:true}`; `404` if no pending row.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_enrichment.py`:

```python
"""Enrichment routes via the shared `client` fixture (temp DB + Flask test client)."""
import models


def _game_on_switch(conn, title="Hades", igdb_id=9):
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) "
                 "VALUES ('Switch','Switch','modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    conn.execute("INSERT INTO games (title, normalized_title, igdb_id, cover_url) VALUES (?,?,?,?)",
                 (title, models.normalize_title(title), igdb_id, "c.jpg"))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title=?",
                       (models.normalize_title(title),)).fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?,?,1)", (gid, pid))
    conn.commit()
    return gid


def test_status_returns_shape(client):
    r = client.get("/api/enrichment/status")
    assert r.status_code == 200
    data = r.get_json()
    assert "status" in data and "remaining_eligible" in data and "remaining_today" in data


def test_review_lists_pending(client):
    conn = models.get_db()
    gid = _game_on_switch(conn)
    conn.execute("INSERT INTO upc_review (game_id, platform, upc, product_title, cover_url, status, reason) "
                 "VALUES (?, 'Switch', '777', 'Hades', 'c.jpg', 'pending', 'near title match')", (gid,))
    conn.commit()
    conn.close()
    r = client.get("/api/enrichment/review")
    cands = r.get_json()["candidates"]
    assert len(cands) == 1
    assert cands[0]["title"] == "Hades" and cands[0]["upc"] == "777" and cands[0]["platform"] == "Switch"


def test_confirm_links_registry_and_clears_row(client):
    conn = models.get_db()
    gid = _game_on_switch(conn, igdb_id=55)
    conn.execute("INSERT INTO upc_review (game_id, platform, upc, product_title, cover_url, status, reason) "
                 "VALUES (?, 'Switch', '777', 'Hades', 'c.jpg', 'pending', 'x')", (gid,))
    conn.commit()
    rid = conn.execute("SELECT id FROM upc_review").fetchone()[0]
    conn.close()

    r = client.post(f"/api/enrichment/review/{rid}/confirm")
    assert r.status_code == 200 and r.get_json()["success"] is True

    conn = models.get_db()
    reg = conn.execute("SELECT game_id, igdb_id, platform FROM barcode_registry WHERE upc='777'").fetchone()
    assert (reg["game_id"], reg["igdb_id"], reg["platform"]) == (gid, 55, "Switch")
    assert conn.execute("SELECT COUNT(*) FROM upc_review WHERE id=?", (rid,)).fetchone()[0] == 0
    conn.close()


def test_reject_marks_dismissed(client):
    conn = models.get_db()
    gid = _game_on_switch(conn)
    conn.execute("INSERT INTO upc_review (game_id, platform, upc, product_title, cover_url, status, reason) "
                 "VALUES (?, 'Switch', '777', 'Hades', 'c.jpg', 'pending', 'x')", (gid,))
    conn.commit()
    rid = conn.execute("SELECT id FROM upc_review").fetchone()[0]
    conn.close()

    r = client.post(f"/api/enrichment/review/{rid}/reject")
    assert r.status_code == 200
    conn = models.get_db()
    assert conn.execute("SELECT status FROM upc_review WHERE id=?", (rid,)).fetchone()[0] == "dismissed"
    conn.close()


def test_confirm_missing_row_404(client):
    r = client.post("/api/enrichment/review/99999/confirm")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_enrichment.py -v`
Expected: FAIL — 404s / missing routes.

- [ ] **Step 3: Write minimal implementation**

In `app.py`, add near the top imports (with the other module imports): `import enrichment` and extend the `background_tasks` import to include `run_enrichment_background, get_enrichment_status, start_enrichment_drip`.

Add the routes near the cover-fetch routes (~line 2248):

```python
@app.route('/api/enrichment/run', methods=['POST'])
def api_enrichment_run():
    """Trigger one enrichment batch now (respects the shared daily quota cap)."""
    from datetime import datetime, timezone
    conn = get_db()
    state = enrichment.get_enrichment_state(conn)
    conn.close()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = state["last_run_count"] if state["last_run_date"] == today else 0
    remaining_today = enrichment.UPC_ENRICH_DAILY_BUDGET - used
    if remaining_today <= 0:
        return jsonify({'success': False, 'message': 'Daily quota exhausted'})
    success, message = run_enrichment_background(remaining_today)
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 409


@app.route('/api/enrichment/status')
def api_enrichment_status():
    """Enrichment task status + eligible-remaining + today's remaining quota."""
    from datetime import datetime, timezone
    status = get_enrichment_status()
    conn = get_db()
    state = enrichment.get_enrichment_state(conn)
    status['remaining_eligible'] = enrichment.count_eligible_pairs(conn)
    conn.close()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = state["last_run_count"] if state["last_run_date"] == today else 0
    status['last_run_date'] = state["last_run_date"]
    status['remaining_today'] = max(0, enrichment.UPC_ENRICH_DAILY_BUDGET - used)
    return jsonify(status)


@app.route('/api/enrichment/review')
def api_enrichment_review():
    """List pending review candidates (game + candidate product)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT ur.id, ur.game_id, ur.platform, ur.upc, ur.product_title, "
        "       COALESCE(ur.cover_url, g.cover_url) AS cover_url, ur.reason, g.title "
        "FROM upc_review ur JOIN games g ON g.id = ur.game_id "
        "WHERE ur.status = 'pending' ORDER BY g.title").fetchall()
    conn.close()
    return jsonify({'candidates': [dict(r) for r in rows]})


@app.route('/api/enrichment/review/<int:rid>/confirm', methods=['POST'])
def api_enrichment_review_confirm(rid):
    """Link the candidate UPC to the game (registry_put) + clear the review row."""
    conn = get_db()
    row = conn.execute(
        "SELECT ur.upc, ur.platform, ur.game_id, g.igdb_id, g.title, g.cover_url "
        "FROM upc_review ur JOIN games g ON g.id = ur.game_id "
        "WHERE ur.id = ? AND ur.status = 'pending'", (rid,)).fetchone()
    if not row or not row["upc"]:
        conn.close()
        return jsonify({'error': 'No pending review row with a UPC'}), 404
    barcode.registry_put(conn, row["upc"], igdb_id=row["igdb_id"], title=row["title"],
                         platform=row["platform"], game_id=row["game_id"],
                         cover_url=row["cover_url"])
    conn.execute("DELETE FROM upc_review WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/enrichment/review/<int:rid>/reject', methods=['POST'])
def api_enrichment_review_reject(rid):
    """Dismiss a review candidate so the drip never re-surfaces that pair."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE upc_review SET status = 'dismissed' WHERE id = ? AND status = 'pending'", (rid,))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return jsonify({'error': 'No pending review row'}), 404
    return jsonify({'success': True})
```

Confirm `import barcode` is present in `app.py` (it is used by the existing scan routes — verify; if absent, add it).

In the app boot block (~line 2306, where `migrate_db()` runs at startup), start the drip after migrations:

```python
    start_enrichment_drip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_enrichment.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run lint + commit**

```bash
ruff check app.py tests/test_api_enrichment.py
git add app.py tests/test_api_enrichment.py
git commit -m "feat(enrichment): run/status/review/confirm/reject routes + drip boot"
```

---

## Task 8: Web "UPC Enrichment" section (settings.html)

**Files:**
- Modify: `templates/settings.html` (add a section after the Cover Art section ~line 107; add JS in the `{% block scripts %}` block)

**Interfaces:** consumes the Task-7 routes via the existing `api` helper (`api.get`, `api.post`) and raw `fetch`. No pytest — verified by the controller in a real browser (Task 9).

**This task has no automated test.** Build the UI mirroring the Cover Art section + its polling exactly. After building, the gate task (Task 9) verifies it in real Chrome against a COPY of `games.db` (never the live DB).

- [ ] **Step 1: Add the section markup**

Insert after the Cover Art `</div>` (~line 107), before the Instructions section:

```html
    <!-- UPC Enrichment Section -->
    <div class="bg-surface-light rounded-xl p-6">
        <h2 class="text-lg font-medium text-white mb-4 flex items-center">
            <span class="mr-2">🏷️</span>
            UPC Enrichment
        </h2>
        <p class="text-sm text-gray-400 mb-4">
            Backfill retail UPCs for your collection so scans resolve instantly.
            Runs automatically once a day; you can also run a batch now.
        </p>

        <div id="enrich-status" class="mb-4 text-sm text-gray-400">Loading status...</div>

        <button onclick="runEnrichment()" id="enrich-button"
                class="px-4 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            Run an enrichment batch now
        </button>

        <div id="enrich-progress" class="hidden mt-4">
            <div class="flex items-center justify-between mb-2">
                <span class="text-sm text-gray-400">Enriching…</span>
                <span id="enrich-progress-text" class="text-sm text-white">0/0</span>
            </div>
            <div class="w-full bg-surface rounded-full h-2 overflow-hidden">
                <div id="enrich-progress-bar" class="bg-accent h-full transition-all duration-200" style="width: 0%"></div>
            </div>
            <div id="enrich-counts" class="mt-2 text-sm text-gray-500"></div>
        </div>

        <!-- Needs-review queue -->
        <div class="mt-6">
            <h3 class="font-medium text-white mb-2">Needs review</h3>
            <div id="enrich-review-empty" class="text-sm text-gray-500">No items awaiting review.</div>
            <div id="enrich-review-list" class="grid grid-cols-1 sm:grid-cols-2 gap-3"></div>
        </div>
    </div>
```

- [ ] **Step 2: Add the JS**

Add inside `{% block scripts %}` (after the cover-fetch JS), and call `loadEnrichment()` from the existing `loadSettings()` (append `loadEnrichment();` at the end of `loadSettings`):

```javascript
    let enrichPoll = null;

    async function loadEnrichment() {
        await loadEnrichStatus();
        await loadEnrichReview();
    }

    async function loadEnrichStatus() {
        const s = await api.get('/api/enrichment/status');
        const el = document.getElementById('enrich-status');
        const btn = document.getElementById('enrich-button');
        const last = s.last_run_date ? `Last run ${s.last_run_date}.` : 'Not run yet.';
        el.innerHTML = `<span><strong class="text-white">${s.remaining_eligible}</strong> games still to enrich.</span>
            <span class="ml-3">${last}</span>
            <span class="ml-3 text-gray-500">${s.remaining_today} lookups left today.</span>`;
        if (s.status === 'running') { showEnrichProgress(); updateEnrichUI(s); startEnrichPolling(); }
        btn.disabled = (s.remaining_today <= 0) || (s.remaining_eligible <= 0) || s.status === 'running';
    }

    function showEnrichProgress() {
        document.getElementById('enrich-button').disabled = true;
        document.getElementById('enrich-progress').classList.remove('hidden');
    }

    function updateEnrichUI(s) {
        const bar = document.getElementById('enrich-progress-bar');
        const txt = document.getElementById('enrich-progress-text');
        const counts = document.getElementById('enrich-counts');
        if (s.total > 0) { bar.style.width = `${(s.current / s.total) * 100}%`; }
        txt.textContent = `${s.current || 0}/${s.total || 0}`;
        counts.textContent = `linked ${s.found || 0} · review ${s.queued || 0} · no match ${s.no_match || 0}`;
    }

    function startEnrichPolling() {
        if (enrichPoll) return;
        enrichPoll = setInterval(async () => {
            const s = await api.get('/api/enrichment/status');
            if (s.status === 'running') { updateEnrichUI(s); }
            else {
                clearInterval(enrichPoll); enrichPoll = null;
                updateEnrichUI(s);
                await loadEnrichStatus();
                await loadEnrichReview();
            }
        }, 500);
    }

    async function runEnrichment() {
        showEnrichProgress();
        try {
            const resp = await fetch('/api/enrichment/run', { method: 'POST' });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            if (data.success === false) { alert(data.message); await loadEnrichStatus(); return; }
            startEnrichPolling();
        } catch (e) {
            alert('Error: ' + e.message);
            await loadEnrichStatus();
        }
    }

    async function loadEnrichReview() {
        const res = await api.get('/api/enrichment/review');
        const list = document.getElementById('enrich-review-list');
        const empty = document.getElementById('enrich-review-empty');
        const cands = res.candidates || [];
        empty.classList.toggle('hidden', cands.length > 0);
        const cover = (u) => u
            ? `<img src="${escapeHtml(u)}" class="w-12 aspect-[3/4] object-contain rounded bg-black/20">`
            : `<div class="w-12 aspect-[3/4] rounded bg-black/20 flex items-center justify-center">🎮</div>`;
        list.innerHTML = cands.map(c => `
            <div class="bg-surface rounded-lg border border-gray-700 p-3 flex gap-3">
                ${cover(c.cover_url)}
                <div class="flex-1 min-w-0">
                    <div class="text-sm text-white truncate" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</div>
                    <div class="text-[11px] text-gray-400 truncate">${escapeHtml(c.platform || '')} · UPC ${escapeHtml(c.upc || '')}</div>
                    <div class="text-[11px] text-gray-500 truncate" title="${escapeHtml(c.product_title || '')}">${escapeHtml(c.product_title || '')}</div>
                    <div class="text-[11px] text-yellow-400/80 truncate">${escapeHtml(c.reason || '')}</div>
                    <div class="mt-2 flex gap-2">
                        <button onclick="confirmEnrich(${c.id})" class="text-xs bg-accent/80 hover:bg-accent text-white rounded px-2 py-1">Confirm</button>
                        <button onclick="rejectEnrich(${c.id})" class="text-xs bg-surface-light hover:bg-gray-600 rounded px-2 py-1">Reject</button>
                    </div>
                </div>
            </div>`).join('');
    }

    async function confirmEnrich(id) {
        const r = await api.post(`/api/enrichment/review/${id}/confirm`, {});
        if (!r.ok) { alert(r.data?.error || 'Could not confirm'); return; }
        await loadEnrichReview(); await loadEnrichStatus();
    }

    async function rejectEnrich(id) {
        const r = await api.post(`/api/enrichment/review/${id}/reject`, {});
        if (!r.ok) { alert(r.data?.error || 'Could not reject'); return; }
        await loadEnrichReview(); await loadEnrichStatus();
    }
```

> NOTE: `escapeHtml` and the `api` helper (`api.get`/`api.post` returning `{ok, data}`) already exist in `base.html` — confirm `api.post` returns an object with `.ok`/`.data` (the IGDB flow uses `res.ok`/`res.data`); if the local `api.post` shape differs, mirror exactly what `pickIgdb`/`keepCurrentIgdb` in `base.html` use.

- [ ] **Step 3: Commit (verification deferred to Task 9 gate)**

```bash
git add templates/settings.html
git commit -m "feat(enrichment): Settings UPC Enrichment section + Needs-review UI"
```

---

## Task 9: Full gate + fresh-DB migrate + live browser verification

**Files:** none (verification + fixes only).

- [ ] **Step 1: Full backend suite**

Run: `uv run python -m pytest`
Expected: ALL pass (the pre-existing ~764 + the new enrichment tests). Fix any regression before proceeding.

- [ ] **Step 2: Lint gate**

Run: `ruff check .`
Expected: clean (no `ruff format`).

- [ ] **Step 3: Fresh-DB migration check (controller, throwaway DB — never the live one)**

```bash
cd "C:/Users/Jeff/Documents/Projects/Game Tracker"
uv run python -c "import tempfile, os, models; \
p=os.path.join(tempfile.mkdtemp(),'fresh.db'); models.DB_PATH=__import__('pathlib').Path(p); \
models.init_db(); models.migrate_db(); c=models.get_db(); \
print('upc_review' , [r[1] for r in c.execute('PRAGMA table_info(upc_review)')]); \
print('state', [r[1] for r in c.execute('PRAGMA table_info(upc_enrichment_state)')]); \
print('state row', c.execute('SELECT * FROM upc_enrichment_state').fetchall())"
```
Expected: both tables present + a single seeded state row `(1, None, 0)`.

- [ ] **Step 4: Controller live browser verification (real Chrome, COPY of games.db)**

Mirror the `verify-ui-changes-yourself` pattern: a throwaway `_verify_server.py` that copies `games.db`, patches `models.DB_PATH`, runs on port 5057; drive with Playwright + Chrome:
- Open `/settings`; confirm the "UPC Enrichment" section renders with the eligible count + "X lookups left today".
- Seed one `pending` and one `no_match` `upc_review` row in the copy DB; reload; confirm exactly one card renders in Needs-review with cover + title + UPC + reason.
- Click **Reject** → card disappears; DB shows `status='dismissed'`.
- Seed another `pending` with a real UPC → click **Confirm** → card disappears; `barcode_registry` gains the row (game_id + platform + igdb_id); review row gone.
- Click **Run an enrichment batch now** (the copy DB has eligible pairs) → button disables, progress shows ticking `current/total` and the `linked/review/no match` counts (live-progress principle); on completion the eligible count drops and any new review cards appear. (UPCitemdb calls hit the real trial quota — keep this to a *small* budget by temporarily lowering the copy's eligible set, or accept ≤ a handful of real lookups.)
- Confirm ZERO console/page errors.
- Tear down the verify server + copy DB.

- [ ] **Step 5: Controller live server restart (the real app)**

Restart `:5000` on the new code so migrations + the drip thread load (memory `windows-process-and-reloader-hygiene`): kill the python.exe on 5000 via PowerShell `Stop-Process`, then `cd "C:/Users/Jeff/Documents/Projects/Game Tracker" && HOST=0.0.0.0 uv run python app.py` (run_in_background). `migrate_db()` adds the two tables; `start_enrichment_drip()` launches. Verify the live `GET /api/enrichment/status` returns a sane `remaining_eligible` for the real ~822-game collection.

- [ ] **Step 6: Whole-branch final review**

Dispatch the final whole-branch review (most-capable model) over the Plan-A commit range. Triage Critical/Important; fix before declaring Plan A done. Then update `.superpowers/sdd/progress.md`.

---

## Self-Review (against Spec 2)

- **§3.1 source (name-search, keyless, []-on-fail):** Task 2 ✓ (+ rate-limit capture, beyond spec, from the verified pre-flight).
- **§3.2 matching & confidence (confident/uncertain/no_match, platform-absent case):** Task 3 ✓.
- **§3.3 idempotency / quota / drip / manual trigger / status:** selection idempotency Task 4 ✓; quota cap + header stop Task 5 ✓; drip daemon + once-per-day gate Task 6 ✓; manual trigger route Task 7 ✓; status object Tasks 6–7 ✓ (live-progress ticking counts Task 8 ✓).
- **§3.4 data model (`upc_review` + enrichment state, idempotent, registered both places):** Task 1 ✓ (chose a one-row `upc_enrichment_state` table — the spec's offered option).
- **§3.5 web review UI (confirm → registry_put + clear; reject → dismissed; cover+title+candidate; web-only):** Tasks 7 + 8 ✓.
- **§5 endpoints (run/status/review/confirm/reject):** Task 7 ✓.
- **§6 error handling (degrade, hard quota stop, isolated worker, named constants):** Tasks 2/5/6 ✓.
- **§7 testing (all external mocked; classifier; idempotency; quota; auto-link; review; migration; — Wikidata/chain are Plan B):** Tasks 1–7 ✓ (Plan A scope).
- **§9 risk #1 (keyless /search):** VERIFIED by controller before this plan (see Global Constraints). Risk neutralized.

**Scope decision flagged for review:** the eligible-pair selection excludes `mobile`/`subscription` platform categories (they have no physical retail UPC). This is a category-driven efficiency guard, not in the spec verbatim, but consistent with its goal and the "fixes must be general" principle. The reviewer/owner should confirm it.

**Plan B (Phase 2)** — `PRODUCT_SOURCES` chain refactor of `resolve()` + `lookup_wikidata_gtin` — is a separate, smaller plan written next; it is independent of Plan A.
