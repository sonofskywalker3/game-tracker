# Android Companion — Phase 1: Barcode Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the backend endpoints the Android app needs to resolve a scanned barcode into a game and persist confirmed UPC→game mappings — the self-growing, free games-UPC database.

**Architecture:** A new `barcode.py` module holds UPC lookup + a `barcode_cache` table backed resolution chain (cache → UPCitemdb free API → IGDB match), with an ownership check against existing games. Two HTTP surfaces: a read endpoint `GET /api/barcode/resolve` and an additive `upc` field on the existing `POST /api/games` that writes the confirmed mapping to the cache.

**Tech Stack:** Python 3, Flask, SQLite (`sqlite3`), `requests`, existing `igdb_match` module, pytest.

## Global Constraints

- Package/env management via `uv`; run tests with `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError: models).
- Lint gate is `ruff check` ONLY — never run `ruff format` (codebase is hand-aligned).
- Tests run against a **pytest temp DB**, never the live `games.db`. Every new migration must be registered in BOTH `models.migrate_db()` and the `tests/conftest.py` `temp_db` fixture.
- Type hints on all new function signatures (params + return).
- Use named constants, not magic numbers/strings in conditions.
- One error pattern: this codebase **raises**; best-effort/external calls **catch specific exceptions, log via `logging`, and degrade** (the barcode resolve flow must never 500 on network failure).
- Use `logging`, not `print()`, for new operational output.
- Commit directly to `main` (no feature branches). End commit messages with the Co-Authored-By trailer.

---

### Task 1: `barcode_cache` table migration

**Files:**
- Modify: `models.py` (add `migrate_barcode_cache`, call it from `migrate_db`)
- Modify: `tests/conftest.py:14-29` (register the migration in `temp_db`)
- Test: `tests/test_barcode_cache.py` (create)

**Interfaces:**
- Produces: `models.migrate_barcode_cache(conn: sqlite3.Connection) -> None` — creates table `barcode_cache(upc TEXT PRIMARY KEY, igdb_id INTEGER, title TEXT, platform TEXT, game_id INTEGER, confirmed_at TEXT)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_barcode_cache.py`:

```python
import models


def test_barcode_cache_table_exists(temp_db):
    conn = models.get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(barcode_cache)")}
    conn.close()
    assert cols == {"upc", "igdb_id", "title", "platform", "game_id", "confirmed_at"}


def test_barcode_cache_upc_is_primary_key(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO barcode_cache (upc, title) VALUES ('111', 'A')")
    conn.commit()
    # Second insert of same upc must violate the PK.
    import sqlite3
    try:
        conn.execute("INSERT INTO barcode_cache (upc, title) VALUES ('111', 'B')")
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    assert raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode_cache.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: barcode_cache` (the migration isn't wired into `temp_db` yet).

- [ ] **Step 3: Add the migration function**

In `models.py`, add near the other `migrate_*` functions:

```python
def migrate_barcode_cache(conn):
    """Self-growing UPC -> game cache for mobile barcode scanning.

    Each confirmed scan writes a row, so repeat scans of the same barcode are
    instant, free, and human-accurate (no UPC-API rate limit, no fuzzy parsing)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barcode_cache (
            upc TEXT PRIMARY KEY,
            igdb_id INTEGER,
            title TEXT,
            platform TEXT,
            game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
            confirmed_at TEXT
        )
    """)
    conn.commit()
```

- [ ] **Step 4: Wire it into `migrate_db()`**

In `models.py` `migrate_db()`, add the call alongside the other migrations (e.g. just after `migrate_decider_chats(conn)` near line 965):

```python
    migrate_decider_chats(conn)
    migrate_barcode_cache(conn)
```

- [ ] **Step 5: Register it in the test fixture**

In `tests/conftest.py`, add to the `temp_db` fixture after `models.migrate_psn_addons_synced_at(conn)` (line 27):

```python
    models.migrate_psn_addons_synced_at(conn)
    models.migrate_barcode_cache(conn)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode_cache.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check models.py tests/test_barcode_cache.py tests/conftest.py
git add models.py tests/conftest.py tests/test_barcode_cache.py
git commit -m "feat(barcode): add barcode_cache table + migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: UPCitemdb product-title lookup

**Files:**
- Create: `barcode.py`
- Test: `tests/test_barcode.py` (create)

**Interfaces:**
- Produces: `barcode.lookup_product_title(upc: str, *, url: str = UPCITEMDB_TRIAL_URL, timeout: int = UPC_LOOKUP_TIMEOUT) -> str | None` — returns the first product title for a UPC, or `None` on miss/error. Never raises.
- Produces module constants `barcode.UPCITEMDB_TRIAL_URL: str`, `barcode.UPC_LOOKUP_TIMEOUT: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_barcode.py`:

```python
import requests

import barcode


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_lookup_returns_first_item_title(monkeypatch):
    def fake_get(url, params, timeout):
        assert params["upc"] == "711719541028"
        return _FakeResp({"items": [{"title": "Marvel's Spider-Man 2 - PS5"}]})

    monkeypatch.setattr(barcode.requests, "get", fake_get)
    assert barcode.lookup_product_title("711719541028") == "Marvel's Spider-Man 2 - PS5"


def test_lookup_returns_none_when_no_items(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get", lambda url, params, timeout: _FakeResp({"items": []}))
    assert barcode.lookup_product_title("000") is None


def test_lookup_degrades_to_none_on_network_error(monkeypatch):
    def boom(url, params, timeout):
        raise requests.Timeout("slow")

    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.lookup_product_title("000") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'barcode'`.

- [ ] **Step 3: Create `barcode.py` with the lookup**

```python
"""UPC/barcode resolution for the mobile companion.

Resolution chain (see resolve()): local barcode_cache -> UPCitemdb free API
-> IGDB title match. External lookups degrade gracefully (never raise) so the
scan flow never 500s.
"""
import logging

import requests

import igdb_match
from models import normalize_title

log = logging.getLogger(__name__)

# Free UPCitemdb trial endpoint: ~100 lookups/day, no API key required.
UPCITEMDB_TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"
UPC_LOOKUP_TIMEOUT = 8  # seconds
MAX_CANDIDATES = 5


def lookup_product_title(upc: str, *, url: str = UPCITEMDB_TRIAL_URL,
                         timeout: int = UPC_LOOKUP_TIMEOUT) -> str | None:
    """Return the first product title for a UPC via UPCitemdb, or None.

    Network/parse failures are logged and degrade to None (never raise)."""
    try:
        resp = requests.get(url, params={"upc": upc}, timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get("items") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("UPC lookup failed for %s: %s", upc, exc)
        return None
    if not items:
        return None
    return (items[0].get("title") or "").strip() or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check barcode.py tests/test_barcode.py
git add barcode.py tests/test_barcode.py
git commit -m "feat(barcode): UPCitemdb product-title lookup with graceful degradation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Cache helpers + resolution chain

**Files:**
- Modify: `barcode.py`
- Test: `tests/test_barcode.py`

**Interfaces:**
- Consumes: `barcode.lookup_product_title` (Task 2); `igdb_match.candidates_for(title, game_platform_ids, collection_name, client_id, token) -> list[dict]` (each `{igdb_id, name, cover_url, platforms, source}`); `igdb_match.short_names_for(ids) -> list[str]`; `models.normalize_title`.
- Produces:
  - `barcode.cache_get(conn, upc: str) -> dict | None`
  - `barcode.cache_put(conn, upc: str, *, igdb_id: int | None = None, title: str | None = None, platform: str | None = None, game_id: int | None = None) -> None`
  - `barcode.resolve(conn, upc: str, *, client_id: str | None = None, token: str | None = None) -> dict` returning `{"upc": str, "source": "cache"|"upc_api"|"none", "candidates": list[dict], "product_title"?: str}` where each candidate is `{"igdb_id", "title", "platform", "cover_url", "owned_game_id"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_barcode.py`:

```python
import models


def _seed_game(title, platform_short=None):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT id FROM games WHERE title = ?", (title,)).fetchone()[0]
    conn.commit()
    conn.close()
    return gid


def test_cache_put_then_get_roundtrip(temp_db):
    conn = models.get_db()
    barcode.cache_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=7)
    conn.commit()
    row = barcode.cache_get(conn, "abc")
    conn.close()
    assert row["igdb_id"] == 42
    assert row["title"] == "Halo"
    assert row["platform"] == "xbox"
    assert row["game_id"] == 7


def test_resolve_returns_cache_hit_without_calling_api(temp_db, monkeypatch):
    conn = models.get_db()
    barcode.cache_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=7)
    conn.commit()

    def fail(*a, **k):
        raise AssertionError("API must not be called on a cache hit")

    monkeypatch.setattr(barcode, "lookup_product_title", fail)
    result = barcode.resolve(conn, "abc")
    conn.close()
    assert result["source"] == "cache"
    assert result["candidates"][0]["title"] == "Halo"


def test_resolve_miss_returns_source_none(temp_db, monkeypatch):
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: None)
    conn = models.get_db()
    result = barcode.resolve(conn, "999")
    conn.close()
    assert result == {"upc": "999", "source": "none", "candidates": []}


def test_resolve_via_api_maps_candidates_and_flags_ownership(temp_db, monkeypatch):
    owned_id = _seed_game("Marvel's Spider-Man 2")
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Marvel's Spider-Man 2 - PS5")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 119171, "name": "Marvel's Spider-Man 2",
         "cover_url": "https://img/x.jpg", "platforms": [167], "source": "search"},
    ])
    monkeypatch.setattr(igdb_match, "short_names_for", lambda ids: ["ps5"])
    conn = models.get_db()
    result = barcode.resolve(conn, "711719541028", client_id="cid", token="tok")
    conn.close()
    assert result["source"] == "upc_api"
    cand = result["candidates"][0]
    assert cand["igdb_id"] == 119171
    assert cand["platform"] == "ps5"
    assert cand["owned_game_id"] == owned_id


def test_resolve_api_hit_no_igdb_match_returns_product_title(temp_db, monkeypatch):
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: "Some Obscure Game")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [])
    conn = models.get_db()
    result = barcode.resolve(conn, "555", client_id="cid", token="tok")
    conn.close()
    assert result["source"] == "upc_api"
    assert result["candidates"] == []
    assert result["product_title"] == "Some Obscure Game"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_barcode.py -v`
Expected: FAIL — `AttributeError: module 'barcode' has no attribute 'cache_put'`.

- [ ] **Step 3: Implement cache helpers + resolve in `barcode.py`**

Append to `barcode.py`:

```python
def cache_get(conn, upc: str) -> dict | None:
    """Return the cached mapping for a UPC, or None."""
    row = conn.execute(
        "SELECT upc, igdb_id, title, platform, game_id FROM barcode_cache WHERE upc = ?",
        (upc,),
    ).fetchone()
    return dict(row) if row else None


def cache_put(conn, upc: str, *, igdb_id: int | None = None, title: str | None = None,
              platform: str | None = None, game_id: int | None = None) -> None:
    """Upsert a confirmed UPC -> game mapping (stamps confirmed_at)."""
    conn.execute(
        "INSERT INTO barcode_cache (upc, igdb_id, title, platform, game_id, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(upc) DO UPDATE SET igdb_id=excluded.igdb_id, title=excluded.title, "
        "platform=excluded.platform, game_id=excluded.game_id, confirmed_at=datetime('now')",
        (upc, igdb_id, title, platform, game_id),
    )


def _owned_game_id(conn, title: str) -> int | None:
    """id of an existing game whose normalized title matches, else None."""
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM games WHERE normalized_title = ?",
        (normalize_title(title),),
    ).fetchone()
    return row["id"] if row else None


def resolve(conn, upc: str, *, client_id: str | None = None,
            token: str | None = None) -> dict:
    """Resolve a UPC to candidate games: cache -> UPC API -> IGDB match.

    Returns {upc, source, candidates[, product_title]}. Each candidate:
    {igdb_id, title, platform, cover_url, owned_game_id}."""
    cached = cache_get(conn, upc)
    if cached:
        return {"upc": upc, "source": "cache", "candidates": [{
            "igdb_id": cached["igdb_id"],
            "title": cached["title"],
            "platform": cached["platform"],
            "cover_url": None,
            "owned_game_id": cached["game_id"] or _owned_game_id(conn, cached["title"] or ""),
        }]}

    product = lookup_product_title(upc)
    if not product:
        return {"upc": upc, "source": "none", "candidates": []}

    candidates: list[dict] = []
    if client_id and token:
        for c in igdb_match.candidates_for(product, set(), None, client_id, token)[:MAX_CANDIDATES]:
            shorts = igdb_match.short_names_for(c.get("platforms") or [])
            candidates.append({
                "igdb_id": c.get("igdb_id"),
                "title": c.get("name"),
                "platform": shorts[0] if shorts else None,
                "cover_url": c.get("cover_url"),
                "owned_game_id": _owned_game_id(conn, c.get("name") or ""),
            })

    if not candidates:
        # Found a product name but no IGDB match: hand the raw title back so the
        # app can prefill manual search.
        return {"upc": upc, "source": "upc_api", "candidates": [], "product_title": product}
    return {"upc": upc, "source": "upc_api", "candidates": candidates}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode.py -v`
Expected: PASS (8 passed total in this file).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check barcode.py tests/test_barcode.py
git add barcode.py tests/test_barcode.py
git commit -m "feat(barcode): cache helpers + cache->API->IGDB resolution chain

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `GET /api/barcode/resolve` endpoint

**Files:**
- Modify: `app.py` (add `import barcode` near the other imports ~line 10; add route near the IGDB search route ~line 2075)
- Test: `tests/test_api_barcode.py` (create)

**Interfaces:**
- Consumes: `barcode.resolve` (Task 3); existing `get_db`, `get_twitch_credentials`, `fetch_covers.get_access_token`.
- Produces: HTTP `GET /api/barcode/resolve?upc=<digits>` → JSON from `barcode.resolve`; `400 {"error": "upc required"}` when `upc` is missing/blank.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_barcode.py`:

```python
import barcode


def test_resolve_requires_upc(client):
    resp = client.get("/api/barcode/resolve")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "upc required"


def test_resolve_returns_cache_hit(client, monkeypatch):
    import models
    conn = models.get_db()
    barcode.cache_put(conn, "abc", igdb_id=42, title="Halo", platform="xbox", game_id=None)
    conn.commit()
    conn.close()

    resp = client.get("/api/barcode/resolve?upc=abc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["source"] == "cache"
    assert body["candidates"][0]["title"] == "Halo"


def test_resolve_miss_is_source_none(client, monkeypatch):
    # No Twitch creds in the temp config -> client_id None -> never calls IGDB;
    # force the UPC lookup to miss so we get source 'none'.
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: None)
    resp = client.get("/api/barcode/resolve?upc=999")
    assert resp.status_code == 200
    assert resp.get_json() == {"upc": "999", "source": "none", "candidates": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: FAIL — 404 on the route (and `import barcode` is fine, but the route isn't registered).

- [ ] **Step 3: Add the import**

In `app.py`, add to the import block (after `import import_scraped` ~line 12):

```python
import barcode
```

- [ ] **Step 4: Add the route**

In `app.py`, add just above the `@app.route('/api/igdb/search')` route (~line 2075):

```python
@app.route('/api/barcode/resolve')
def api_barcode_resolve():
    """Resolve a scanned UPC to candidate games (cache -> UPCitemdb -> IGDB)."""
    upc = (request.args.get('upc') or '').strip()
    if not upc:
        return jsonify({'error': 'upc required'}), 400

    client_id, client_secret = get_twitch_credentials()
    token = None
    if client_id:
        try:
            from fetch_covers import get_access_token
            token = get_access_token(client_id, client_secret)
        except Exception as exc:   # best-effort: a token failure just skips IGDB matching
            app.logger.warning("IGDB token fetch failed during barcode resolve: %s", exc)
            client_id = None

    conn = get_db()
    result = barcode.resolve(conn, upc, client_id=client_id, token=token)
    conn.close()
    return jsonify(result)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check app.py tests/test_api_barcode.py
git add app.py tests/test_api_barcode.py
git commit -m "feat(barcode): GET /api/barcode/resolve endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Persist confirmed scans via `upc` on `POST /api/games`

**Files:**
- Modify: `app.py` `api_create_game()` (lines 198-282)
- Test: `tests/test_api_barcode.py`

**Interfaces:**
- Consumes: `barcode.cache_put` (Task 3); existing `api_create_game` flow.
- Produces: `POST /api/games` accepts an optional `"upc"` string. When present, a `barcode_cache` row is upserted linking the UPC → the created game (or the existing game on the 409 path), so confirmed scans grow the cache.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_barcode.py`:

```python
def test_post_game_with_upc_writes_cache(client):
    import models
    resp = client.post("/api/games", json={"title": "Celeste", "upc": "abc123"})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    conn = models.get_db()
    row = barcode.cache_get(conn, "abc123")
    conn.close()
    assert row is not None
    assert row["game_id"] == gid
    assert row["title"] == "Celeste"


def test_post_existing_game_with_upc_links_existing(client):
    import models
    first = client.post("/api/games", json={"title": "Hades"})
    gid = first.get_json()["game_id"]
    # Same title again -> 409 existing, but the UPC should still map to it.
    dup = client.post("/api/games", json={"title": "Hades", "upc": "ean999"})
    assert dup.status_code == 409

    conn = models.get_db()
    row = barcode.cache_get(conn, "ean999")
    conn.close()
    assert row["game_id"] == gid


def test_post_game_without_upc_writes_no_cache_row(client):
    import models
    client.post("/api/games", json={"title": "Tunic"})
    conn = models.get_db()
    count = conn.execute("SELECT COUNT(*) FROM barcode_cache").fetchone()[0]
    conn.close()
    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: FAIL — `assert row is not None` fails (UPC is ignored today).

- [ ] **Step 3: Read the upc once near the top of `api_create_game`**

In `app.py` `api_create_game()`, after `title = clean_title(title)` / `normalized = normalize_title(title)` (~line 211), add:

```python
    upc = (data.get('upc') or '').strip() or None
```

- [ ] **Step 4: Link UPC on the existing-game (409) path**

In `api_create_game()`, in the `if existing:` block (lines 219-221), before returning the 409, write the cache row:

```python
    if existing:
        if upc:
            barcode.cache_put(conn, upc, title=title, game_id=existing['id'])
            conn.commit()
        conn.close()
        return jsonify({'error': 'Game already exists', 'game_id': existing['id']}), 409
```

- [ ] **Step 5: Link UPC on the created-game path**

In `api_create_game()`, after the IGDB-enrichment `try/except` block and before `conn.close()` (~line 280), add:

```python
    if upc:
        platform_short = platforms[0] if platforms else None
        barcode.cache_put(conn, upc, title=title, platform=platform_short, game_id=game_id)
        conn.commit()
```

(`platforms` is the list already read at line 238; `game_id` is set at line 229.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: PASS (6 passed in this file).

- [ ] **Step 7: Full suite + lint + commit**

```bash
uv run python -m pytest -q
uv run ruff check app.py tests/test_api_barcode.py
git add app.py tests/test_api_barcode.py
git commit -m "feat(barcode): persist confirmed scans via upc on POST /api/games

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (spec §3):**
- §3.1 `barcode_cache` table → Task 1 ✅
- §3.2 `GET /api/barcode/resolve` (cache → UPC API → IGDB; ownership flag; graceful failure) → Tasks 3 + 4 ✅
- §3.3 persist confirmations via `upc` on `POST /api/games` (created + already-owned paths; Physical handled by existing `physical` flag) → Task 5 ✅
- §3.4 reused endpoints → unchanged, no task needed ✅
- §6 error handling (resolve never 500s, `logging`) → Task 2/3 graceful degradation + Task 4 token try/except ✅
- §7 testing (temp DB, mocked HTTP) → all tasks use `temp_db`/`client` + monkeypatch ✅

**Placeholder scan:** none — every code step shows complete code; every run step shows the exact command + expected result.

**Type consistency:** `lookup_product_title`, `cache_get`, `cache_put`, `resolve` signatures and the candidate dict shape (`igdb_id/title/platform/cover_url/owned_game_id`) are consistent across Tasks 2–5; `candidates_for` returns `{igdb_id, name, cover_url, platforms}` (verified in `igdb_match.py`) and is mapped accordingly.

**Note:** `physical: true` on barcode adds is the app's responsibility (it sets the existing flag when POSTing) — no backend change needed; the spec's "add as Physical" is satisfied by the existing `physical` handling at `app.py:250-261`.
