# PlayStation DLC ownership via Store pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a PlayStation scrape, detect the user's owned add-ons from PS Store product pages and flip their `dlc` rows to `owned = 1`, backfilling the whole library once then running incrementally.

**Architecture:** While the scrape browser is open, after collecting base games, visit each target game's Store product page (`store.playstation.com/en-us/product/<external_id>`), scroll to load its add-ons, and parse owned add-ons (`price.basePrice == "Purchased"`). Those flow into the existing `dlc_ownership.mark_ownership` engine. A per-game `psn_addons_synced_at` marker makes the first run a full backfill and later runs incremental.

**Tech Stack:** Python, Playwright (sync), SQLite, pytest. Run tests with `uv run python -m pytest`; lint gate `ruff check` (never `ruff format`).

---

## Background facts (verified against current code + recon)

- `_run` (`scrape_service.py:185-225`) owns the browser inside a `with` block; `collect` is called at line 212 and the context closes at 213. **The add-on pass must run inside that block.**
- `_run_pipeline` (`scrape_service.py:77-150`) partitions `kind=="addon"` rows and, for non-steam vendors, calls `dlc_ownership.mark_ownership(addons)` (line 115). PSN add-ons currently never arrive.
- Base scrape stores each game's full Store product id as `external_id` (`playstation.py:54`), e.g. `UP0082-CUSA09377_00-PT00000000000000`; persisted in `game_external_ids` (source `playstation`) by `import_games`.
- Recon (`.recon/psn_store_*`): a game page's GraphQL bodies contain objects with `id` (product id), `name`, `storeDisplayClassification`, and `price.basePrice`. Add-ons have a non-null `basePrice`; base/edition/demo objects have `price == null`. Owned ⇔ `basePrice == "Purchased"`.
- `scrapers/base.py` provides `scroll_until_idle(page, captured)` and the `(page, captured)` pair from `capturing_browser`. Scrapers must not touch the DB.

## File Structure

- **Modify** `models.py` — add nullable `psn_addons_synced_at` column to `games` (+ idempotent migration).
- **Modify** `scrapers/playstation.py` — add `parse_addons` (pure), `store_product_url`, `ADDON_PID_RE`, and a real `collect_addons(page, product_ids, captured)`.
- **Modify** `scrape_service.py` — run the add-on pass in `_run` for PSN; compute targets; stamp `psn_addons_synced_at` in `_run_pipeline`; add a `collect_addons` test seam.
- **Test** `tests/test_parse_playstation.py` — `parse_addons` unit tests.
- **Test** `tests/test_scraper_playstation_addons.py` (new) — `collect_addons` with a fake page.
- **Test** `tests/test_scrape_service.py` — full PSN flow stamps marker + marks owned.

---

## Task 1: DB column `psn_addons_synced_at`

**Files:**
- Modify: `models.py` (the `games` CREATE TABLE near line 116, and the migration section)
- Test: `tests/test_models_psn_marker.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_psn_marker.py
import models


def test_games_has_psn_addons_synced_at(temp_db):
    conn = models.get_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    conn.close()
    assert "psn_addons_synced_at" in cols


def test_psn_marker_defaults_null(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    val = conn.execute("SELECT psn_addons_synced_at FROM games WHERE title='G'").fetchone()[0]
    conn.close()
    assert val is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_models_psn_marker.py -v`
Expected: FAIL — `psn_addons_synced_at` not in columns.

- [ ] **Step 3: Add the column to the schema and migration**

In `models.py`, add the column to the `games` CREATE TABLE (after `updated_at`):

```python
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            psn_addons_synced_at TIMESTAMP,
```

Then find where other idempotent `ALTER TABLE ... ADD COLUMN` migrations live in `init_db` (search `models.py` for `ADD COLUMN`). Add, following the exact surrounding pattern (the codebase wraps each in a try/except on `sqlite3.OperationalError` for "duplicate column"):

```python
    try:
        conn.execute("ALTER TABLE games ADD COLUMN psn_addons_synced_at TIMESTAMP")
    except sqlite3.OperationalError:
        pass  # column already exists
```

(If the existing migrations use a different idempotency helper, use that helper instead — match the established pattern, don't introduce a new one.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_models_psn_marker.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_models_psn_marker.py
git commit -m "feat(dlc): add games.psn_addons_synced_at marker for PSN add-on sync" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `parse_addons` (pure) in playstation.py

**Files:**
- Modify: `scrapers/playstation.py`
- Test: `tests/test_parse_playstation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_parse_playstation.py`:

```python
from scrapers import playstation


def _game_page_bodies():
    """A trimmed game-page GraphQL body: base game + owned/priced/unavailable add-ons.

    Shape mirrors .recon/psn_store_* (objects nested under data; parse walks recursively).
    """
    return [{
        "data": {"productRetrieve": {"relatedItems": [
            {"id": "UP1063-PPSA06812_00-0000000000000000", "name": "Ys VIII",
             "storeDisplayClassification": "FULL_GAME", "price": None,
             "platforms": ["PS4"]},
            {"id": "UP1063-PPSA06812_00-YS08JPDLC00N0060", "name": "Ys VIII - Bait Set",
             "storeDisplayClassification": "ITEM", "platforms": ["PS4"],
             "price": {"basePrice": "Purchased"}},
            {"id": "UP1063-PPSA06812_00-YS08JPDLC00N0099", "name": "Ys VIII - Recipe Pack",
             "storeDisplayClassification": "ITEM", "platforms": ["PS4"],
             "price": {"basePrice": "$0.99"}},
            {"id": "UP4497-PPSA03974_00-EXPANSION1B00000", "name": "Pre-Order Bonus",
             "storeDisplayClassification": "VEHICLE", "platforms": ["PS5"],
             "price": {"basePrice": "Unavailable"}},
        ]}}
    }]


def test_parse_addons_keeps_only_purchased():
    addons = playstation.parse_addons(_game_page_bodies())
    assert [a.external_id for a in addons] == ["UP1063-PPSA06812_00-YS08JPDLC00N0060"]
    a = addons[0]
    assert a.kind == "addon"
    assert a.source == "playstation"
    assert a.title == "Ys VIII - Bait Set"
    assert a.source_title == "Ys VIII - Bait Set"
    assert a.platform == "PS4"


def test_parse_addons_excludes_base_game_even_if_owned():
    bodies = [{"x": [{"id": "UP1063-PPSA06812_00-0000000000000000", "name": "Ys VIII",
                      "storeDisplayClassification": "FULL_GAME",
                      "price": {"basePrice": "Purchased"}}]}]
    assert playstation.parse_addons(bodies) == []


def test_parse_addons_dedupes_by_id():
    bodies = _game_page_bodies() + _game_page_bodies()
    addons = playstation.parse_addons(bodies)
    assert len(addons) == 1


def test_parse_addons_skips_malformed_bodies():
    assert playstation.parse_addons([None, {}, {"data": None}, 42]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_parse_playstation.py -k addons -v`
Expected: FAIL — `playstation.parse_addons` does not exist.

- [ ] **Step 3: Implement `parse_addons` and helpers**

In `scrapers/playstation.py`, add `import re` to the imports, and add near the top-level constants:

```python
# Full PS Store product id, e.g. UP0082-CUSA09377_00-PT00000000000000 (region UP/EP/JP).
ADDON_PID_RE = re.compile(r"[A-Z]{2}\d{4}-[A-Z]{4}\d{5}_00-[A-Z0-9]{16}")
STORE_PRODUCT_URL = "https://store.playstation.com/en-us/product/{pid}"
# storeDisplayClassification values that are NOT add-ons.
NON_ADDON_CLASS = frozenset({"FULL_GAME", "GAME_BUNDLE", "DEMO"})
OWNED_PRICE = "Purchased"
```

Then add the functions:

```python
def _iter_product_objects(node):
    """Yield dicts anywhere in the structure that look like a store product."""
    if isinstance(node, dict):
        pid = node.get("id")
        if isinstance(pid, str) and ADDON_PID_RE.fullmatch(pid):
            yield node
        for v in node.values():
            yield from _iter_product_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_product_objects(v)


def parse_addons(bodies: list[dict]) -> list[ScrapedGame]:
    """Map captured game-page GraphQL bodies to OWNED add-on ScrapedGame records.

    An add-on is a product object with a non-null price.basePrice and a
    classification that is not a base game/bundle/demo. Owned ⇔ basePrice ==
    "Purchased" (the en-us Store label). Dedupes by product id.
    """
    out: list[ScrapedGame] = []
    seen: set[str] = set()
    for body in bodies:
        if not isinstance(body, dict):
            continue
        for obj in _iter_product_objects(body):
            pid = obj["id"]
            if pid in seen:
                continue
            cls = obj.get("storeDisplayClassification")
            if cls in NON_ADDON_CLASS:
                continue
            price = obj.get("price") or {}
            base_price = price.get("basePrice") if isinstance(price, dict) else None
            if not base_price:                 # base/edition/demo objects have price=None
                continue
            if base_price != OWNED_PRICE:       # priced / "Unavailable" -> not owned
                continue
            name = obj.get("name")
            if not name:
                continue
            seen.add(pid)
            platforms = obj.get("platforms") or []
            platform = platforms[0] if platforms else DEFAULT_PLATFORM
            out.append(ScrapedGame(
                title=name, platform=PLATFORM_LABELS.get(platform, DEFAULT_PLATFORM),
                source=SOURCE, external_id=pid, source_title=name, kind="addon",
            ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_parse_playstation.py -k addons -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check scrapers/playstation.py
git add scrapers/playstation.py tests/test_parse_playstation.py
git commit -m "feat(psn): parse_addons reads owned add-ons from store-page GraphQL" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `collect_addons` live shell

**Files:**
- Modify: `scrapers/playstation.py` (replace the stub `collect_addons`, lines 122-132)
- Test: `tests/test_scraper_playstation_addons.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scraper_playstation_addons.py
import json

from scrapers import playstation


class FakePage:
    """Records goto() calls; appends a canned GraphQL body to `captured` per URL."""
    def __init__(self, captured, bodies_by_pid):
        self.captured = captured
        self.bodies_by_pid = bodies_by_pid
        self.visited = []

    def goto(self, url):
        self.visited.append(url)
        pid = url.rsplit("/", 1)[-1]
        for body in self.bodies_by_pid.get(pid, []):
            self.captured.append({"url": url, "body": json.dumps(body)})

    def wait_for_timeout(self, ms):
        pass


def _owned_body(addon_pid, name="Pack", price="Purchased"):
    return {"data": {"items": [
        {"id": addon_pid, "name": name, "storeDisplayClassification": "ITEM",
         "platforms": ["PS5"], "price": {"basePrice": price}}]}}


def test_collect_addons_visits_targets_and_returns_owned(monkeypatch):
    monkeypatch.setattr(playstation, "scroll_until_idle", lambda *a, **k: None)
    base1 = "UP0082-PPSA10664_00-FF16SIEA00000002"
    base2 = "UP4497-PPSA03974_00-0000000000000CP1"
    captured = []
    page = FakePage(captured, {
        base1: [_owned_body("UP0082-PPSA10664_00-ADDCONT000000300", "FF16 DLC")],
        base2: [_owned_body("UP4497-PPSA03974_00-EXPANSION1000000", "PL", price="$29.99")],
    })
    addons = playstation.collect_addons(page, [base1, base2], captured)
    assert page.visited == [
        "https://store.playstation.com/en-us/product/" + base1,
        "https://store.playstation.com/en-us/product/" + base2,
    ]
    assert [a.external_id for a in addons] == ["UP0082-PPSA10664_00-ADDCONT000000300"]


def test_collect_addons_skips_non_product_ids_and_survives_errors(monkeypatch):
    monkeypatch.setattr(playstation, "scroll_until_idle", lambda *a, **k: None)

    class Boom(FakePage):
        def goto(self, url):
            raise RuntimeError("nav failed")

    captured = []
    page = Boom(captured, {})
    # "TITLEID_ONLY" is not a full product id -> skipped; the bad goto is swallowed.
    addons = playstation.collect_addons(page, ["TITLEID_ONLY",
                                               "UP0082-PPSA10664_00-FF16SIEA00000002"],
                                        captured)
    assert addons == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_scraper_playstation_addons.py -v`
Expected: FAIL — current `collect_addons` ignores args and returns `[]` (first test fails on `page.visited`).

- [ ] **Step 3: Replace the stub `collect_addons`**

Add `scroll_until_idle` to the `scrapers.base` import in `playstation.py`, then replace lines 122-132:

```python
def collect_addons(page, product_ids: list[str],
                   captured: list | None = None) -> list[ScrapedGame]:
    """Visit each game's Store product page and return its OWNED add-ons.

    One page load per game: goto the product URL, scroll so the add-ons section
    lazy-loads, then parse the newly-captured GraphQL bodies. Per-game isolation
    so one bad page doesn't abort the batch. `captured` is the shared response
    log from capturing_browser; we parse only the slice each page adds.
    """
    captured = captured if captured is not None else []
    out: list[ScrapedGame] = []
    seen: set[str] = set()
    for pid in product_ids:
        if not (pid and ADDON_PID_RE.fullmatch(pid)):
            continue
        start = len(captured)
        try:
            page.goto(STORE_PRODUCT_URL.format(pid=pid))
            scroll_until_idle(page, captured)
        except Exception as exc:  # one bad page must not sink the run
            logger.warning("playstation: add-on page failed for %s: %s", pid, exc)
            continue
        bodies = []
        for entry in captured[start:]:
            try:
                bodies.append(json.loads(entry.get("body", "")))
            except (json.JSONDecodeError, TypeError):
                continue
        for addon in parse_addons(bodies):
            if addon.external_id not in seen:
                seen.add(addon.external_id)
                out.append(addon)
        page.wait_for_timeout(REQUEST_DELAY_MS)
    logger.info("playstation: %d owned add-ons across %d games", len(out), len(product_ids))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_scraper_playstation_addons.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check scrapers/playstation.py
git add scrapers/playstation.py tests/test_scraper_playstation_addons.py
git commit -m "feat(psn): collect_addons visits store pages for owned add-ons" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire the add-on pass into scrape_service

**Files:**
- Modify: `scrape_service.py` (`_run` 185-225, `_run_pipeline` 77-150, `start` 153-164)
- Test: `tests/test_scrape_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scrape_service.py`:

```python
def test_psn_flow_marks_addons_and_stamps_marker(temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)

    base_pid = "UP0082-PPSA10664_00-FF16SIEA00000002"

    def fake_collect(page, captured):
        return [ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                            source="playstation", external_id=base_pid)]

    def fake_collect_addons(page, product_ids, captured):
        assert product_ids == [base_pid]   # backfill: nothing synced yet
        return [ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone",
                            platform="PS5", source="playstation",
                            external_id="UP0082-PPSA10664_00-ADDCONT000000300",
                            kind="addon")]

    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=fake_collect, collect_addons=fake_collect_addons)
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.signal_continue()
    assert _wait_phase("complete")
    st = scrape_service.status()
    assert st["summary"]["owned_marked"] == 1

    conn = models.get_db()
    synced = conn.execute(
        "SELECT g.psn_addons_synced_at FROM games g "
        "JOIN game_external_ids ge ON ge.game_id = g.id "
        "WHERE ge.source='playstation' AND ge.external_id = ?", (base_pid,)).fetchone()[0]
    conn.close()
    assert synced is not None   # marker stamped so future scrapes skip it
```

Note: `_fake_enrich` (already in this file) inserts a "Hearts of Stone" dlc row under the Witcher game, which the add-on reconciles to → `owned_marked == 1`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_scrape_service.py::test_psn_flow_marks_addons_and_stamps_marker -v`
Expected: FAIL — `start()` has no `collect_addons` kwarg (TypeError).

- [ ] **Step 3: Thread the seam through `start` and `_run`**

In `scrape_service.py`, change `start` (line 153) to accept and forward the seam:

```python
def start(vendor: str, *, browser_factory=None, collect=None,
          collect_addons=None) -> tuple[bool, str]:
    """Begin a scrape in a daemon thread. Rejects if a scrape is active or the
    vendor is unknown. browser_factory/collect/collect_addons are test seams."""
    if vendor not in VENDORS:
        return False, f"unknown vendor: {vendor}"
    if _is_active():
        return False, "a scrape is already running"
    _reset(vendor=vendor)
    thread = threading.Thread(
        target=_run, args=(vendor, browser_factory, collect, collect_addons),
        daemon=True)
    thread.start()
    return True, "started"
```

Change `_run`'s signature (line 185) to `def _run(vendor, browser_factory, collect, collect_addons=None) -> None:`. Inside the `with` block, after `games = collect_fn(page, captured)` (line 212), add the PSN add-on pass and capture the visited targets:

```python
            _set(phase="scraping", message=f"scraping your {vendor} library...")
            games = collect_fn(page, captured)
            visited_pids: list[str] = []
            if vendor == "playstation":
                addon_fn = collect_addons or mod.collect_addons
                visited_pids = _psn_addon_targets(games)
                _set(phase="scraping",
                     message=f"checking add-ons for {len(visited_pids)} games...")
                owned_addons = addon_fn(page, visited_pids, captured)
                games = list(games) + owned_addons
```

Then pass `visited_pids` into the pipeline call (line 216):

```python
            summary = _run_pipeline(conn, vendor, games, visited_pids=visited_pids)
```

- [ ] **Step 4: Add `_psn_addon_targets` and marker stamping**

Add this helper near `_run_pipeline` in `scrape_service.py`:

```python
def _psn_addon_targets(games: list) -> list[str]:
    """PS product ids to visit: scraped ids whose game is not yet add-on-synced.

    New games aren't in the DB yet (import runs later), so they're naturally
    included -> first run backfills all, later runs only hit unsynced/new games.
    """
    from scrapers import playstation
    scraped = [g.external_id for g in games
               if g.external_id and playstation.ADDON_PID_RE.fullmatch(g.external_id)]
    conn = models.get_db()
    try:
        synced = {r[0] for r in conn.execute(
            "SELECT ge.external_id FROM game_external_ids ge "
            "JOIN games g ON g.id = ge.game_id "
            "WHERE ge.source = 'playstation' AND g.psn_addons_synced_at IS NOT NULL")}
    finally:
        conn.close()
    return [pid for pid in scraped if pid not in synced]
```

Change `_run_pipeline`'s signature (line 77) to accept the visited ids:

```python
def _run_pipeline(conn: sqlite3.Connection, vendor: str, games: list,
                  visited_pids: list[str] | None = None) -> dict:
```

After `conn.commit()` following `mark_ownership` (line 116), stamp the marker for visited games (which now exist post-import):

```python
        if visited_pids:
            placeholders = ",".join("?" for _ in visited_pids)
            conn.execute(
                f"UPDATE games SET psn_addons_synced_at = CURRENT_TIMESTAMP "
                f"WHERE id IN (SELECT game_id FROM game_external_ids "
                f"WHERE source = 'playstation' AND external_id IN ({placeholders}))",
                visited_pids)
            conn.commit()
```

- [ ] **Step 5: Run the new test + full suite**

Run: `uv run python -m pytest tests/test_scrape_service.py -v`
Expected: PASS, including the new test and the existing `test_start_runs_full_flow` (which calls `start` without `collect_addons` — the kwarg defaults to None and `mod.collect_addons` is used; that test's vendor is playstation, so ensure its `fake_collect` games have no valid product-id `external_id` — `external_id="G1"` won't match `ADDON_PID_RE`, so `visited_pids == []` and `mod.collect_addons` is never exercised with live nav).

Then the whole suite:

Run: `uv run python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check scrape_service.py
git add scrape_service.py tests/test_scrape_service.py
git commit -m "feat(psn): run add-on ownership pass during PlayStation scrape" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 (fast-follow): per-game "Refresh PSN DLC" button

Ships only if single-game browser plumbing is straightforward; the backfill (Tasks 1-4) is the core deliverable. Keep this task small and behind the same engine.

**Files:**
- Modify: `app.py` (new route near the other `/api/games/<id>/dlc/...` routes)
- Modify: `templates/base.html` (per-game modal: a "Refresh PSN DLC" button)
- Test: `tests/test_api_games.py`

- [ ] **Step 1: Write the failing test**

```python
def test_refresh_psn_nulls_marker_and_starts_scrape(client, temp_db, monkeypatch):
    import app as app_module
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title, psn_addons_synced_at) "
                 "VALUES ('G', 'g', '2020-01-01 00:00:00')")
    gid = conn.execute("SELECT id FROM games WHERE title='G'").fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'playstation', 'UP0082-PPSA10664_00-FF16SIEA00000002')", (gid,))
    conn.commit()
    conn.close()

    started = {}
    monkeypatch.setattr(app_module.scrape_service, "start",
                        lambda vendor, **kw: started.setdefault("vendor", vendor) or (True, "started"))
    resp = client.post(f"/api/games/{gid}/dlc/refresh-psn")
    assert resp.status_code == 200
    conn = models.get_db()
    val = conn.execute("SELECT psn_addons_synced_at FROM games WHERE id=?", (gid,)).fetchone()[0]
    conn.close()
    assert val is None            # marker cleared so the game is re-visited
    assert started["vendor"] == "playstation"


def test_refresh_psn_404_without_psn_id(client, temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    conn.commit()
    conn.close()
    gid = 1
    assert client.post(f"/api/games/{gid}/dlc/refresh-psn").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py -k refresh_psn -v`
Expected: FAIL — route does not exist (404 for the first test too).

- [ ] **Step 3: Add the route**

In `app.py`, near the other dlc routes:

```python
@app.route('/api/games/<int:game_id>/dlc/refresh-psn', methods=['POST'])
def api_refresh_psn_dlc(game_id):
    """Clear the PSN add-on marker for one game and kick a scrape to re-check it."""
    conn = get_db()
    row = conn.execute(
        "SELECT external_id FROM game_external_ids "
        "WHERE game_id = ? AND source = 'playstation'", (game_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "no PlayStation id for this game"}), 404
    conn.execute("UPDATE games SET psn_addons_synced_at = NULL WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    ok, msg = scrape_service.start("playstation")
    return jsonify({"started": ok, "message": msg})
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_api_games.py -k refresh_psn -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the button**

In `templates/base.html`, in the per-game modal's DLC section, add (near the existing DLC refresh control):

```html
<button type="button" onclick="refreshPsnDlc()"
        class="text-accent hover:underline text-sm">Refresh PSN DLC</button>
```

And the JS (near the other modal dlc functions), using the modal's current game id variable (match the existing pattern, e.g. `currentGameId`):

```javascript
async function refreshPsnDlc() {
    const r = await api.post(`/api/games/${currentGameId}/dlc/refresh-psn`, {});
    if (!r.data || r.data.started === false) {
        alert((r.data && r.data.message) || 'Could not start PSN refresh');
        return;
    }
    alert('PSN refresh started — log in if prompted, then watch the scrape status.');
}
```

- [ ] **Step 6: Full suite + lint + commit**

```bash
uv run python -m pytest -q
uv run ruff check app.py
git add app.py templates/base.html tests/test_api_games.py
git commit -m "feat(psn): per-game Refresh PSN DLC button" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the whole suite: `uv run python -m pytest -q` — all green.
- [ ] Lint: `uv run ruff check models.py scrapers/playstation.py scrape_service.py app.py` — clean.
- [ ] Push: `git push origin main`.
- [ ] Manual (owner): run a live PlayStation scrape; confirm the modal shows add-ons being marked owned and "Marked owned this run" is non-zero; spot-check a game with known owned DLC (e.g. DQB2) and one disc-only game (Cyberpunk — base shows owned via library, Phantom Liberty stays unowned).
