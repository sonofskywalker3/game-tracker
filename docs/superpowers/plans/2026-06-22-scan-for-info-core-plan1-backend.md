# Scan-for-Info Core — Plan 1: Backend Data + Resolution Foundations

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend foundations for scan-for-info: a permanent per-platform UPC registry, per-platform physical/digital format, `mobile`/`subscription` platform categories, platform-aware scan matching, and an enriched `resolve()` that records every scan and reports cross-platform + multi-pack ownership.

**Architecture:** Pure Python/SQLite/Flask, no frontend. Reuse `barcode_cache` (renamed `barcode_registry`) as the per-platform UPC store. Extend `models.py` migrations idempotently, `barcode.py` resolution, and `igdb_match.py` candidate filtering. Web editor UI and the Android scan-for-info screen are separate plans (Plan 2, Plan 3).

**Tech Stack:** Python 3.11, SQLite (`sqlite3`), Flask, pytest. Package/env via `uv`.

## Global Constraints

- Tests: `uv run python -m pytest` (NOT plain `pytest`). Lint: `ruff check` ONLY (never `ruff format`).
- Tests use the pytest temp DB (`temp_db`/`client` fixtures); NEVER touch the live `games.db`.
- Type hints on all new function signatures. Use `logging`, not `print()` (existing migrations use `print`; new code uses the module pattern already present in `barcode.py`: `log = logging.getLogger(__name__)`).
- Named constants for enums/lookups at module scope (`frozenset`/tuple).
- Every new migration is idempotent (guarded by `PRAGMA table_info` / `INSERT OR IGNORE`) and registered in BOTH `models.migrate_db()` and `tests/conftest.py::temp_db`.
- Reuse `barcode_registry` as the UPC store. Semantic rule: `barcode_registry` = "what game is this UPC" (knowledge); `game_platforms` = ownership. Recording a scan never implies ownership.
- One error pattern: these modules raise/return as the surrounding code does (`barcode.py` degrades external lookups to safe values, never raises out of `resolve`).

---

## File Structure

- `models.py` — add migrations: `migrate_barcode_registry` (rename), `migrate_platform_digital_market`, `migrate_seed_extra_platforms` (mobile+subscription), `migrate_game_platform_format`. Add seed tuples + `has_digital_market` override constants. Register all in `migrate_db()`.
- `barcode.py` — rename `cache_get/cache_put` → `registry_get/registry_put` + table name; add `registry_upcs_for_game`, `parse_retail_platform`, `owned_platforms_for`, enriched `resolve()`.
- `igdb_match.py` — add `REAL_GAME_TYPES` set + game-type/platform filtering params on `candidates_for`; add `RETAIL_PLATFORM_TO_SHORT` map (or keep in `barcode.py`).
- `app.py` — `resolve` route unchanged call-wise (resolve handles internals); extend `PUT /api/games/<id>` to accept a single `{platform, format, upc}` add; extend `POST /api/games` to set `game_platforms.format` for the scanned platform.
- `tests/conftest.py` — register the 4 new migrations.
- `tests/test_api_barcode.py`, `tests/test_barcode_registry.py` (rename of `test_barcode_cache.py`), `tests/test_platform_format.py`, `tests/test_igdb_match.py` — coverage.

---

## Task 1: Rename `barcode_cache` → `barcode_registry`

**Files:**
- Modify: `models.py` (`migrate_barcode_cache` → `migrate_barcode_registry`, ~847-862; register in `migrate_db` ~983)
- Modify: `barcode.py` (`cache_get`/`cache_put` → `registry_get`/`registry_put`, table name in SQL, ~40-58, 78-86)
- Modify: `app.py` (any `barcode.cache_get`/`cache_put` references)
- Modify: `tests/conftest.py:28` (`migrate_barcode_cache` → `migrate_barcode_registry`)
- Rename: `tests/test_barcode_cache.py` → `tests/test_barcode_registry.py`; update references in `tests/test_api_barcode.py`

**Interfaces:**
- Produces: `models.migrate_barcode_registry(conn)`, `barcode.registry_get(conn, upc) -> dict | None`, `barcode.registry_put(conn, upc, *, igdb_id=None, title=None, platform=None, game_id=None) -> None`. Table `barcode_registry(upc PK, igdb_id, title, platform, game_id, confirmed_at)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_barcode_registry.py`:
```python
import models


def test_registry_table_exists(temp_db):
    conn = models.get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(barcode_registry)")}
    conn.close()
    assert {"upc", "igdb_id", "title", "platform", "game_id", "confirmed_at"} <= cols


def test_migration_renames_old_cache_preserving_rows(tmp_path, monkeypatch):
    db = tmp_path / "rename.db"
    monkeypatch.setattr(models, "DB_PATH", db)
    conn = models.get_db()
    # Simulate a pre-rename DB with the OLD table holding a row.
    conn.execute(
        "CREATE TABLE barcode_cache (upc TEXT PRIMARY KEY, igdb_id INTEGER, "
        "title TEXT, platform TEXT, game_id INTEGER, confirmed_at TEXT)"
    )
    conn.execute("INSERT INTO barcode_cache (upc, title) VALUES ('111', 'Halo')")
    conn.commit()
    models.migrate_barcode_registry(conn)
    row = conn.execute("SELECT title FROM barcode_registry WHERE upc='111'").fetchone()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert row[0] == "Halo"
    assert "barcode_cache" not in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode_registry.py -v`
Expected: FAIL — `migrate_barcode_registry` does not exist / table `barcode_registry` missing.

- [ ] **Step 3: Write minimal implementation**

In `models.py`, replace `migrate_barcode_cache` with:
```python
def migrate_barcode_registry(conn: sqlite3.Connection) -> None:
    """Permanent UPC -> game registry for mobile barcode scanning. Every confirmed
    scan writes a row, so repeat scans are instant, free, and human-accurate.

    Renamed from barcode_cache: if the old table exists and the new one does not,
    rename it in place (preserving all rows); otherwise create barcode_registry.
    Idempotent."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "barcode_cache" in tables and "barcode_registry" not in tables:
        conn.execute("ALTER TABLE barcode_cache RENAME TO barcode_registry")
        conn.commit()
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barcode_registry (
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
In `models.migrate_db()` replace `migrate_barcode_cache(conn)` with `migrate_barcode_registry(conn)`.

In `barcode.py`, rename helpers and the table name:
```python
def registry_get(conn: sqlite3.Connection, upc: str) -> dict | None:
    """Return the cached mapping for a UPC, or None."""
    row = conn.execute(
        "SELECT upc, igdb_id, title, platform, game_id FROM barcode_registry WHERE upc = ?",
        (upc,),
    ).fetchone()
    return dict(row) if row else None


def registry_put(conn: sqlite3.Connection, upc: str, *, igdb_id: int | None = None,
                 title: str | None = None, platform: str | None = None,
                 game_id: int | None = None) -> None:
    """Upsert a UPC -> game mapping (stamps confirmed_at)."""
    conn.execute(
        "INSERT INTO barcode_registry (upc, igdb_id, title, platform, game_id, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(upc) DO UPDATE SET igdb_id=excluded.igdb_id, title=excluded.title, "
        "platform=excluded.platform, game_id=excluded.game_id, confirmed_at=datetime('now')",
        (upc, igdb_id, title, platform, game_id),
    )
```
Update `resolve()`'s internal `cache_get` call to `registry_get`. Update `tests/conftest.py:28` to `models.migrate_barcode_registry(conn)`. Grep `app.py` for `cache_get`/`cache_put`/`barcode_cache` and rename to `registry_get`/`registry_put`/`barcode_registry`. In `tests/test_api_barcode.py` replace `barcode.cache_put`/`cache_get` with `registry_put`/`registry_get`. Delete `tests/test_barcode_cache.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_barcode_registry.py tests/test_api_barcode.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py barcode.py app.py tests/conftest.py tests/test_barcode_registry.py tests/test_api_barcode.py
git rm tests/test_barcode_cache.py
git commit -m "refactor(barcode): rename barcode_cache -> barcode_registry (permanent UPC store)"
```

---

## Task 2: `registry_upcs_for_game()` helper

**Files:**
- Modify: `barcode.py`
- Test: `tests/test_barcode_registry.py`

**Interfaces:**
- Produces: `barcode.registry_upcs_for_game(conn, game_id) -> list[dict]` → `[{"upc": str, "platform": str | None}]`, ordered by platform.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_barcode_registry.py`:
```python
import barcode


def test_registry_upcs_for_game_lists_per_platform(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (7, 'Z', 'z')")
    barcode.registry_put(conn, "AAA", platform="Switch", game_id=7)
    barcode.registry_put(conn, "BBB", platform="PS5", game_id=7)
    barcode.registry_put(conn, "CCC", platform="Switch", game_id=99)  # other game
    conn.commit()
    rows = barcode.registry_upcs_for_game(conn, 7)
    conn.close()
    assert rows == [{"upc": "BBB", "platform": "PS5"}, {"upc": "AAA", "platform": "Switch"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_barcode_registry.py::test_registry_upcs_for_game_lists_per_platform -v`
Expected: FAIL — `registry_upcs_for_game` not defined.

- [ ] **Step 3: Write minimal implementation**

In `barcode.py`:
```python
def registry_upcs_for_game(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """All known UPC -> platform rows for a game (the per-platform UPC set)."""
    rows = conn.execute(
        "SELECT upc, platform FROM barcode_registry WHERE game_id = ? ORDER BY platform",
        (game_id,),
    ).fetchall()
    return [{"upc": r["upc"], "platform": r["platform"]} for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_barcode_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add barcode.py tests/test_barcode_registry.py
git commit -m "feat(barcode): registry_upcs_for_game helper (per-platform UPCs)"
```

---

## Task 3: `platforms.has_digital_market` column + seed/overrides

**Files:**
- Modify: `models.py` (add constants near `LEGACY_PLATFORM_SEED`; add `migrate_platform_digital_market`; register in `migrate_db`)
- Modify: `tests/conftest.py` (register migration)
- Test: `tests/test_platform_format.py`

**Interfaces:**
- Produces: `models.migrate_platform_digital_market(conn)`; column `platforms.has_digital_market INTEGER NOT NULL DEFAULT 0`; constant `models.DIGITAL_MARKET_LEGACY_OVERRIDES: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_platform_format.py`:
```python
import models


def _add_platform(conn, name, short, category):
    conn.execute(
        "INSERT INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category),
    )


def test_has_digital_market_seeded_by_category_and_overrides(temp_db):
    conn = models.get_db()
    _add_platform(conn, "PlayStation 5", "PS5", "modern_console")
    _add_platform(conn, "Super Nintendo", "SNES", "legacy_console")
    _add_platform(conn, "Nintendo 3DS", "3DS", "legacy_console")
    conn.commit()
    models.migrate_platform_digital_market(conn)
    got = dict(conn.execute(
        "SELECT short_name, has_digital_market FROM platforms").fetchall())
    conn.close()
    assert got["PS5"] == 1      # modern -> digital market
    assert got["SNES"] == 0     # pure cartridge legacy
    assert got["3DS"] == 1      # legacy-with-eShop override
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_platform_format.py -v`
Expected: FAIL — `migrate_platform_digital_market` not defined.

- [ ] **Step 3: Write minimal implementation**

In `models.py`, near `LEGACY_PLATFORM_SEED`:
```python
# Legacy platforms that DID have a digital storefront (eShop/PSN/XBLA), so their
# games still need a physical/digital qualifier. Pure cartridge/disc legacy do not.
DIGITAL_MARKET_LEGACY_OVERRIDES = frozenset({"3DS", "WiiU", "PS3", "X360", "Vita", "PSP"})
```
Add the migration:
```python
def migrate_platform_digital_market(conn: sqlite3.Connection) -> None:
    """Add platforms.has_digital_market and (re)seed it. Idempotent.

    Default by category: modern_console / pc / mobile / subscription have a digital
    market (1); legacy_console does not (0), except the eShop/PSN-era overrides."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(platforms)").fetchall()]
    if "has_digital_market" not in cols:
        conn.execute(
            "ALTER TABLE platforms ADD COLUMN has_digital_market INTEGER NOT NULL DEFAULT 0"
        )
    for row in conn.execute("SELECT id, short_name, category FROM platforms").fetchall():
        has_market = (
            row[2] in ("modern_console", "pc", "mobile", "subscription")
            or row[1] in DIGITAL_MARKET_LEGACY_OVERRIDES
        )
        conn.execute(
            "UPDATE platforms SET has_digital_market = ? WHERE id = ?",
            (1 if has_market else 0, row[0]),
        )
    conn.commit()
```
Register in `migrate_db()` after `migrate_platform_category(conn)`: `migrate_platform_digital_market(conn)`. Also add `models.migrate_platform_digital_market(conn)` to `tests/conftest.py::temp_db` (before `conn.close()`) so the `client` fixture's DB has the column. Note: tests that insert their own platforms then call the migration directly (as `test_platform_format.py` does) still work, because the migration is idempotent and re-seeds every row.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_platform_format.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/conftest.py tests/test_platform_format.py
git commit -m "feat(platforms): has_digital_market column + category seed + eShop overrides"
```

---

## Task 4: Seed `mobile` + `subscription` platform categories

**Files:**
- Modify: `models.py` (seed tuples + `migrate_seed_extra_platforms`; register in `migrate_db`)
- Modify: `tests/conftest.py`
- Test: `tests/test_platform_format.py`

**Interfaces:**
- Produces: `models.MOBILE_PLATFORM_SEED`, `models.SUBSCRIPTION_PLATFORM_SEED` (tuples of `(name, short_name)`); `models.migrate_seed_extra_platforms(conn)`. New categories: `"mobile"`, `"subscription"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_platform_format.py`:
```python
def test_mobile_and_subscription_categories_seeded(temp_db):
    conn = models.get_db()
    models.migrate_seed_extra_platforms(conn)
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT short_name, category FROM platforms").fetchall()}
    conn.close()
    assert rows.get("iOS") == "mobile"
    assert rows.get("Android") == "mobile"
    assert rows.get("GamePass") == "subscription"
    assert rows.get("PSPlus") == "subscription"


def test_seed_extra_platforms_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_seed_extra_platforms(conn)
    models.migrate_seed_extra_platforms(conn)  # second run = no error, no dupes
    n = conn.execute("SELECT COUNT(*) FROM platforms WHERE short_name='iOS'").fetchone()[0]
    conn.close()
    assert n == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_platform_format.py -k extra -v`
Expected: FAIL — `migrate_seed_extra_platforms` not defined.

- [ ] **Step 3: Write minimal implementation**

In `models.py`:
```python
MOBILE_CATEGORY = "mobile"
SUBSCRIPTION_CATEGORY = "subscription"

MOBILE_PLATFORM_SEED = (
    ("iOS", "iOS"),
    ("Android", "Android"),
)
SUBSCRIPTION_PLATFORM_SEED = (
    ("Xbox Game Pass", "GamePass"),
    ("PlayStation Plus", "PSPlus"),
    ("Nintendo Switch Online", "NSO"),
    ("EA Play", "EAPlay"),
    ("Ubisoft+", "UbisoftPlus"),
    ("Amazon Luna", "Luna"),
)


def migrate_seed_extra_platforms(conn: sqlite3.Connection) -> None:
    """Seed the mobile + subscription platform categories (stub for later catalogs).
    Idempotent: INSERT OR IGNORE keys on the unique short_name."""
    conn.executemany(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        [(name, short, MOBILE_CATEGORY) for name, short in MOBILE_PLATFORM_SEED]
        + [(name, short, SUBSCRIPTION_CATEGORY) for name, short in SUBSCRIPTION_PLATFORM_SEED],
    )
    conn.commit()
```
Register in `migrate_db()` AFTER `migrate_seed_legacy_platforms(conn)` and BEFORE `migrate_platform_digital_market(conn)` (so the new rows get a digital-market value): order = `migrate_seed_legacy_platforms`, `migrate_seed_extra_platforms`, `migrate_platform_digital_market`. Add `migrate_seed_extra_platforms(conn)` to `tests/conftest.py::temp_db`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_platform_format.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add models.py tests/conftest.py tests/test_platform_format.py
git commit -m "feat(platforms): seed mobile + subscription categories (stub)"
```

---

## Task 5: `game_platforms.format` column + backfill

**Files:**
- Modify: `models.py` (`migrate_game_platform_format`; register in `migrate_db`)
- Modify: `tests/conftest.py`
- Test: `tests/test_platform_format.py`

**Interfaces:**
- Produces: `models.migrate_game_platform_format(conn)`; column `game_platforms.format TEXT` ('physical'|'digital'|NULL). Backfill: 'physical' where the game's current per-game `physical` flag is set, else 'digital'.

**Note on the source `physical` flag:** the legacy-games feature stores per-game `physical` — confirm its location with `PRAGMA table_info(games)` and `PRAGMA table_info(user_ratings)`. The backfill below reads `games.physical`; if the column lives on `user_ratings`, adjust the SELECT accordingly (the test pins the expected behavior).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_platform_format.py`:
```python
def test_game_platform_format_column_and_backfill(temp_db):
    conn = models.get_db()
    _add_platform(conn, "PlayStation 5", "PS5", "modern_console")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO games (id, title, normalized_title, physical) "
                 "VALUES (1, 'A', 'a', 1)")
    conn.execute("INSERT INTO games (id, title, normalized_title, physical) "
                 "VALUES (2, 'B', 'b', 0)")
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (1, ?)", (pid,))
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (2, ?)", (pid,))
    conn.commit()
    models.migrate_game_platform_format(conn)
    fmts = dict(conn.execute(
        "SELECT game_id, format FROM game_platforms").fetchall())
    conn.close()
    assert fmts[1] == "physical"
    assert fmts[2] == "digital"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_platform_format.py -k format_column -v`
Expected: FAIL — `migrate_game_platform_format` not defined (or `games.physical` missing → confirms where the flag lives; adjust Step 3 + test accordingly).

- [ ] **Step 3: Write minimal implementation**

In `models.py`:
```python
def migrate_game_platform_format(conn: sqlite3.Connection) -> None:
    """Add game_platforms.format ('physical'|'digital') and backfill from the
    per-game `physical` flag (physical if set, else digital). Only fills NULLs, so
    re-running never overrides a value the user later set per-platform. Idempotent."""
    cols = [c[1] for c in conn.execute("PRAGMA table_info(game_platforms)").fetchall()]
    if "format" not in cols:
        conn.execute("ALTER TABLE game_platforms ADD COLUMN format TEXT")
    conn.execute("""
        UPDATE game_platforms SET format =
            CASE WHEN (SELECT COALESCE(physical, 0) FROM games WHERE games.id = game_platforms.game_id) = 1
                 THEN 'physical' ELSE 'digital' END
        WHERE format IS NULL
    """)
    conn.commit()
```
Register `migrate_game_platform_format(conn)` in `migrate_db()` and `tests/conftest.py::temp_db`. (If `physical` lives on `user_ratings`, change the subselect to read `user_ratings`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_platform_format.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/conftest.py tests/test_platform_format.py
git commit -m "feat(platforms): per-platform game_platforms.format + backfill"
```

---

## Task 6: Parse the platform from a retail title

**Files:**
- Modify: `barcode.py` (add `RETAIL_PLATFORM_TO_SHORT` + `parse_retail_platform`)
- Test: `tests/test_api_barcode.py`

**Interfaces:**
- Produces: `barcode.parse_retail_platform(raw: str | None) -> str | None` → app `short_name` (e.g. `"Switch"`) or `None`. Reuses the platform phrases already in `_RETAIL_NOISE_WORDS`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_barcode.py`:
```python
def test_parse_retail_platform():
    assert barcode.parse_retail_platform(
        "Mario Kart 8 Deluxe (Nintendo Switch)") == "Switch"
    assert barcode.parse_retail_platform(
        "God of War Ragnarok - PlayStation 5") == "PS5"
    assert barcode.parse_retail_platform("Some PC Game") == "PC"
    assert barcode.parse_retail_platform("No platform here") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_barcode.py::test_parse_retail_platform -v`
Expected: FAIL — `parse_retail_platform` not defined.

- [ ] **Step 3: Write minimal implementation**

In `barcode.py`, after `_RETAIL_NOISE_WORDS`:
```python
# Retail platform phrase (as it appears in UPC titles) -> app short_name. Longest
# phrases first so "nintendo switch" wins over "switch". Extensible.
RETAIL_PLATFORM_TO_SHORT: tuple[tuple[str, str], ...] = (
    ("nintendo switch 2", "Switch"), ("nintendo switch", "Switch"), ("switch", "Switch"),
    ("playstation 5", "PS5"), ("ps5", "PS5"),
    ("playstation 4", "PS4"), ("ps4", "PS4"),
    ("playstation 3", "PS3"), ("ps3", "PS3"),
    ("xbox series x|s", "Xbox"), ("xbox series x", "Xbox"), ("xbox series s", "Xbox"),
    ("xbox one", "Xbox"), ("xbox 360", "X360"), ("xbox", "Xbox"),
    ("wii u", "WiiU"), ("wii", "Wii"),
    ("nintendo 3ds", "3DS"), ("3ds", "3DS"), ("nintendo ds", "NDS"),
    ("gamecube", "GC"), ("nintendo 64", "N64"),
    ("pc", "PC"), ("windows", "PC"),
)


def parse_retail_platform(raw: str | None) -> str | None:
    """First platform named in a retail product title, mapped to an app short_name."""
    if not raw:
        return None
    low = raw.lower()
    for phrase, short in RETAIL_PLATFORM_TO_SHORT:
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            return short
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_barcode.py::test_parse_retail_platform -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add barcode.py tests/test_api_barcode.py
git commit -m "feat(barcode): parse platform short_name from retail UPC titles"
```

---

## Task 7: Game-type + platform filtering in `igdb_match.candidates_for`

**Files:**
- Modify: `igdb_match.py` (add `REAL_GAME_TYPES`; add `drop_fan_types` + `restrict_to_platform` kwargs to `candidates_for`)
- Test: `tests/test_igdb_match.py`

**Interfaces:**
- Consumes: existing `candidates_for(title, game_platform_ids, collection_name, client_id, token)`.
- Produces: `candidates_for(..., *, drop_fan_types: bool = False, restrict_to_platform: bool = False)`. `igdb_match.REAL_GAME_TYPES: frozenset[int]`. When `drop_fan_types`, candidates whose `game_type` is not in `REAL_GAME_TYPES` are dropped. When `restrict_to_platform` and `game_platform_ids` is non-empty, candidates whose platforms don't overlap `game_platform_ids` are dropped.

**IGDB `game_type` enum (verify against live values; already fetched in `fetch_candidates`):** 0 main_game, 3 bundle, 8 remake, 9 remaster, 10 expanded_game, 11 port are "real games" to KEEP. 5 mod, 12 fork (fan games/ROM hacks), plus dlc/expansion/episode/season/pack/update are DROPPED.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_igdb_match.py` (this file already monkeypatches `igdb_dlc._igdb_query`; mirror its existing pattern):
```python
import igdb_dlc
import igdb_match


def test_candidates_for_drops_fan_types_and_wrong_platform(monkeypatch):
    SWITCH = 130
    rows = [
        {"id": 1, "name": "Paper Mario: TTYD", "platforms": [130], "game_type": 8,
         "cover": {"url": "//x/t_thumb/a.jpg"}, "total_rating_count": 50},   # remake, Switch
        {"id": 2, "name": "Paper Mario: TTYD", "platforms": [21], "game_type": 0,
         "cover": {"url": "//x/t_thumb/b.jpg"}, "total_rating_count": 80},   # GameCube original
        {"id": 3, "name": "Paper Mario: TTYD", "platforms": [130], "game_type": 5,
         "cover": {"url": "//x/t_thumb/c.jpg"}, "total_rating_count": 10},   # mod/ROM hack
    ]
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda *a, **k: rows)
    out = igdb_match.candidates_for(
        "Paper Mario: TTYD", {SWITCH}, None, "cid", "tok",
        drop_fan_types=True, restrict_to_platform=True)
    ids = [c["igdb_id"] for c in out]
    assert ids == [1]   # only the Switch remake survives
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_igdb_match.py::test_candidates_for_drops_fan_types_and_wrong_platform -v`
Expected: FAIL — `candidates_for` rejects unexpected kwargs.

- [ ] **Step 3: Write minimal implementation**

In `igdb_match.py`, add near the score constants:
```python
# IGDB game_type values that represent a real, standalone game worth matching a
# physical scan to. Excludes mods/forks (fan games, ROM hacks) and add-on types.
REAL_GAME_TYPES = frozenset({0, 3, 8, 9, 10, 11})  # main, bundle, remake, remaster, expanded, port
```
Change `candidates_for` signature and the search loop:
```python
def candidates_for(title: str, game_platform_ids: set[int],
                   collection_name: str | None, client_id: str, token: str,
                   *, drop_fan_types: bool = False,
                   restrict_to_platform: bool = False) -> list[dict]:
    """Ranked identity candidates, bundle-derived first then scored search.
    Each: {igdb_id, name, cover_url, platforms, source, score?}.

    drop_fan_types: drop candidates whose game_type is not a REAL_GAME_TYPE.
    restrict_to_platform: when game_platform_ids is non-empty, drop candidates
    whose platforms don't overlap it."""
    out: list[dict] = []
    seen: set[int] = set()
    target = normalize_title(title)
    if collection_name:
        bid = resolve_bundle(collection_name, game_platform_ids, client_id, token)
        if bid:
            for c in bundle_constituents(bid, client_id, token):
                if normalize_title(c["name"] or "") == target and c["igdb_id"] not in seen:
                    seen.add(c["igdb_id"])
                    out.append({**c, "source": "bundle"})
    for c in score_candidates(fetch_candidates(title, client_id, token),
                              game_platform_ids=game_platform_ids, title=title):
        if c.get("id") in seen:
            continue
        if drop_fan_types and c.get("game_type") not in REAL_GAME_TYPES:
            continue
        plats = set(c.get("platforms") or [])
        if restrict_to_platform and game_platform_ids and not (plats & game_platform_ids):
            continue
        out.append({"igdb_id": c.get("id"), "name": c.get("name"),
                    "cover_url": cover_url_of(c), "platforms": c.get("platforms") or [],
                    "source": "search", "score": c["_score"]})
    return out
```
(`score_candidates` already preserves `game_type` via `out = dict(c)`, so `c.get("game_type")` is available here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_igdb_match.py -v`
Expected: PASS (new test + existing tests still green — defaults keep old behavior).

- [ ] **Step 5: Commit**

```bash
git add igdb_match.py tests/test_igdb_match.py
git commit -m "feat(igdb): platform + game-type filtering for barcode scan matching"
```

---

## Task 8: Platform-aware `resolve()` + record-every-scan + ownership

**Files:**
- Modify: `barcode.py` (`resolve()`, add `owned_platforms_for`)
- Test: `tests/test_api_barcode.py`

**Interfaces:**
- Consumes: `parse_retail_platform`, `clean_product_title`, `igdb_match.candidates_for(..., drop_fan_types=True, restrict_to_platform=True)`, `igdb_match.platform_ids_for`, `registry_put`.
- Produces: `barcode.owned_platforms_for(conn, game_id) -> list[dict]` → `[{"short_name": str, "format": str | None, "has_digital_market": int}]`. `resolve()` return gains top-level `"scanned_platform": str | None` and each candidate gains `"owned_platforms": list`. Every non-cache resolve writes a `barcode_registry` row (game_id stays NULL — knowledge only, no ownership).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_barcode.py`:
```python
def test_owned_platforms_for_includes_format_and_market(temp_db):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (3, 'Q', 'q')")
    conn.execute("INSERT INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('PlayStation 5','PS5','modern_console',1)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='PS5'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (3, ?, 'physical')", (pid,))
    conn.commit()
    got = barcode.owned_platforms_for(conn, 3)
    conn.close()
    assert got == [{"short_name": "PS5", "format": "physical", "has_digital_market": 1}]


def test_resolve_records_unmatched_scan(client, monkeypatch):
    import models
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Totally Unknown Game (Nintendo Switch)")
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [])
    resp = client.get("/api/barcode/resolve?upc=NEW123")
    body = resp.get_json()
    assert body["scanned_platform"] == "Switch"
    conn = models.get_db()
    row = barcode.registry_get(conn, "NEW123")
    conn.close()
    assert row is not None and row["game_id"] is None   # recorded, not owned
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_barcode.py -k "owned_platforms_for or records_unmatched" -v`
Expected: FAIL — `owned_platforms_for` not defined; `scanned_platform` missing.

- [ ] **Step 3: Write minimal implementation**

In `barcode.py`:
```python
def owned_platforms_for(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """Platforms the user owns this game on, with per-platform format + whether the
    platform has a digital market (drives the (Physical/Digital) display qualifier)."""
    rows = conn.execute(
        "SELECT p.short_name, gp.format, p.has_digital_market "
        "FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
        "WHERE gp.game_id = ? ORDER BY p.short_name",
        (game_id,),
    ).fetchall()
    return [{"short_name": r["short_name"], "format": r["format"],
             "has_digital_market": r["has_digital_market"]} for r in rows]
```
Rewrite `resolve()` after the cache-hit branch:
```python
    product = lookup_product_title(upc)
    if not product:
        return {"upc": upc, "source": "none", "candidates": [], "scanned_platform": None}

    scanned_platform = parse_retail_platform(product)
    search_title = clean_product_title(product) or product
    platform_ids = igdb_match.platform_ids_for([scanned_platform]) if scanned_platform else set()

    candidates: list[dict] = []
    if client_id and token:
        for c in igdb_match.candidates_for(
                search_title, platform_ids, None, client_id, token,
                drop_fan_types=True, restrict_to_platform=bool(platform_ids))[:MAX_CANDIDATES]:
            owned_id = _owned_game_id(conn, c.get("name") or "")
            shorts = igdb_match.short_names_for(c.get("platforms") or [])
            candidates.append({
                "igdb_id": c.get("igdb_id"),
                "title": c.get("name"),
                "platform": scanned_platform or (shorts[0] if shorts else None),
                "cover_url": c.get("cover_url"),
                "owned_game_id": owned_id,
                "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
            })

    # Record EVERY scan (knowledge, not ownership): upc -> best-guess title/igdb,
    # game_id stays NULL until a confirmed add links it.
    top = candidates[0] if candidates else None
    registry_put(conn, upc,
                 igdb_id=top["igdb_id"] if top else None,
                 title=top["title"] if top else search_title,
                 platform=scanned_platform, game_id=None)
    conn.commit()

    if not candidates:
        return {"upc": upc, "source": "upc_api", "candidates": [],
                "product_title": search_title, "scanned_platform": scanned_platform}
    return {"upc": upc, "source": "upc_api", "candidates": candidates,
            "scanned_platform": scanned_platform}
```
Also add `"scanned_platform": None` to the cache-hit return dict at the top of `resolve()` for shape consistency, and a `"scanned_platform"` key is not needed on cache hits beyond None.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: PASS (new tests + existing; the existing `test_resolve_uses_cleaned_title_for_prefill` still passes — it stubs `candidates_for` to `[]` and asserts `product_title`).

- [ ] **Step 5: Commit**

```bash
git add barcode.py tests/test_api_barcode.py
git commit -m "feat(barcode): platform-aware resolve + record-every-scan + ownership detail"
```

---

## Task 9: Multi-pack / collection constituent ownership in `resolve()`

**Files:**
- Modify: `barcode.py` (`resolve()` adds `constituents` for bundles)
- Test: `tests/test_api_barcode.py`

**Interfaces:**
- Consumes: `igdb_match.bundle_constituents`, `_owned_game_id`, `owned_platforms_for`.
- Produces: `resolve()` candidates of a bundle gain `"constituents": list[{title, owned_game_id, owned_platforms}]`. Detection: the top candidate's `game_type == 3` (bundle). The resolver fetches the bundle's constituents from IGDB and reports which the user owns.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_barcode.py`:
```python
def test_resolve_reports_owned_bundle_constituents(client, monkeypatch):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES "
                 "(10, 'Mega Man X', ?)", (models.normalize_title("Mega Man X"),))
    conn.execute("INSERT INTO platforms (name, short_name, category, has_digital_market) "
                 "VALUES ('Super Nintendo','SNES','legacy_console',0)")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='SNES'").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, format) "
                 "VALUES (10, ?, 'physical')", (pid,))
    conn.commit(); conn.close()

    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Mega Man X Legacy Collection (Nintendo Switch)")
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 500, "name": "Mega Man X Legacy Collection", "platforms": [130],
         "cover_url": "c", "source": "search", "score": 100, "game_type": 3}])
    monkeypatch.setattr(barcode.igdb_match, "bundle_constituents", lambda *a, **k: [
        {"igdb_id": 1, "name": "Mega Man X", "normalized_title": "mega man x",
         "cover_url": "x", "platforms": [19]},
        {"igdb_id": 2, "name": "Mega Man X2", "normalized_title": "mega man x2",
         "cover_url": "y", "platforms": [19]}])

    body = client.get("/api/barcode/resolve?upc=MMX").get_json()
    cons = body["candidates"][0]["constituents"]
    owned = {c["title"]: c["owned_platforms"] for c in cons}
    assert owned["Mega Man X"] == [{"short_name": "SNES", "format": "physical",
                                    "has_digital_market": 0}]
    assert owned["Mega Man X2"] == []
```
Note: this test requires the candidate dict from `candidates_for` to carry `game_type`. Update the Task 8 candidate-building loop to also copy `"game_type": c.get("game_type")` into each candidate (add that key), OR fetch bundle status separately. Add `"game_type": c.get("game_type")` in the Task 8 loop now (small amend) so this task can read it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_barcode.py::test_resolve_reports_owned_bundle_constituents -v`
Expected: FAIL — `constituents` key missing.

- [ ] **Step 3: Write minimal implementation**

In the Task 8 candidate loop, ensure each candidate carries `"game_type": c.get("game_type")`. Then, after building `candidates` and before the registry write, enrich bundle candidates:
```python
    BUNDLE_GAME_TYPE = 3
    if client_id and token:
        for cand in candidates:
            if cand.get("game_type") != BUNDLE_GAME_TYPE or not cand.get("igdb_id"):
                continue
            cons = []
            for k in igdb_match.bundle_constituents(cand["igdb_id"], client_id, token):
                owned_id = _owned_game_id(conn, k.get("name") or "")
                cons.append({
                    "title": k.get("name"),
                    "owned_game_id": owned_id,
                    "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
                })
            cand["constituents"] = cons
```
(For non-bundle candidates `constituents` is simply absent.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add barcode.py tests/test_api_barcode.py
git commit -m "feat(barcode): report owned bundle constituents on scan (multi-pack)"
```

---

## Task 10: Add-a-platform endpoint + format on create

**Files:**
- Modify: `app.py` (`PUT /api/games/<id>` single-platform add branch; `POST /api/games` writes `game_platforms.format`)
- Test: `tests/test_api_games.py`

**Interfaces:**
- Consumes: `barcode.registry_put`.
- Produces: `PUT /api/games/<id>` accepts `{"add_platform": {"short_name": str, "format": "physical"|"digital", "upc": str | null}}` — appends the platform to `game_platforms` (with `format`) without disturbing the existing full-`platforms`-replace path, and writes the UPC into `barcode_registry` linked to this game. `POST /api/games` with `platforms` + `physical` sets `format` on the created rows ('physical' if `physical` else 'digital').

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_games.py`:
```python
def test_add_platform_to_existing_game(client):
    import models, barcode
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (1,'A','a')")
    conn.execute("INSERT INTO platforms (name, short_name, category) "
                 "VALUES ('Nintendo Switch','Switch','modern_console')")
    conn.commit(); conn.close()

    resp = client.put("/api/games/1", json={
        "add_platform": {"short_name": "Switch", "format": "physical", "upc": "U1"}})
    assert resp.status_code == 200

    conn = models.get_db()
    fmt = conn.execute(
        "SELECT gp.format FROM game_platforms gp JOIN platforms p "
        "ON p.id=gp.platform_id WHERE gp.game_id=1 AND p.short_name='Switch'"
    ).fetchone()[0]
    reg = barcode.registry_get(conn, "U1")
    conn.close()
    assert fmt == "physical"
    assert reg["game_id"] == 1 and reg["platform"] == "Switch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_add_platform_to_existing_game -v`
Expected: FAIL — `format` is NULL / registry row absent (branch not implemented).

- [ ] **Step 3: Write minimal implementation**

In `app.py` `api_update_game`, inside the `try:` before `conn.commit()`, add:
```python
        # Single-platform add (mobile scan "I bought the <platform> copy"). Appends
        # one platform with its format + records the UPC, without touching the
        # existing full-`platforms` replace path above.
        add = data.get('add_platform')
        if add and add.get('short_name'):
            prow = conn.execute(
                "SELECT id FROM platforms WHERE short_name = ?", (add['short_name'],)
            ).fetchone()
            if prow:
                conn.execute(
                    "INSERT OR IGNORE INTO game_platforms (game_id, platform_id, format) "
                    "VALUES (?, ?, ?)", (game_id, prow['id'], add.get('format')))
                conn.execute(
                    "UPDATE game_platforms SET format = ? WHERE game_id = ? AND platform_id = ?",
                    (add.get('format'), game_id, prow['id']))
            if add.get('upc'):
                barcode.registry_put(conn, add['upc'], title=None,
                                     platform=add['short_name'], game_id=game_id)
```
In `POST /api/games` (the create path, ~246-257 where platforms are inserted), set `format` on insert:
```python
    platforms = data.get('platforms', [])
    fmt = 'physical' if data.get('physical') else 'digital'
    for platform_short_name in platforms:
        platform = conn.execute(
            "SELECT id FROM platforms WHERE short_name = ?", (platform_short_name,)
        ).fetchone()
        if platform:
            conn.execute(
                "INSERT INTO game_platforms (game_id, platform_id, format) VALUES (?, ?, ?)",
                (game_id, platform['id'], fmt))
```
Ensure `import barcode` is present in `app.py` (it is, used by resolve).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games.py::test_add_platform_to_existing_game tests/test_api_barcode.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(api): add-a-platform path with format + UPC; format on create"
```

---

## Task 11: Full suite + lint gate

**Files:** none (verification task)

- [ ] **Step 1: Run the full backend suite**

Run: `uv run python -m pytest -q`
Expected: PASS (all green, including pre-existing tests).

- [ ] **Step 2: Lint**

Run: `uv run ruff check`
Expected: `All checks passed!` (fix any new findings; do NOT run `ruff format`).

- [ ] **Step 3: Sanity-check migrations apply to a fresh DB**

Run: `uv run python -c "import models, tempfile, pathlib; models.DB_PATH = pathlib.Path(tempfile.mkdtemp())/'g.db'; models.init_db(); models.migrate_db(); print('migrations OK')"`
Expected: prints `migrations OK` with no exception.

- [ ] **Step 4: Commit (if any lint fixes were needed)**

```bash
git add -A
git commit -m "chore(barcode): lint + full-suite green for scan-for-info backend"
```

---

## Self-Review

- **Spec coverage:** §2.1 registry rename → Task 1. §2.2 format → Task 5. §2.3 has_digital_market + overrides → Task 3. §2.4 mobile/subscription seeds → Task 4. §3 platform-aware matching → Tasks 6+7+8. §4 scan-for-info ownership data (owned_platforms + format) → Task 8. §5 multi-pack → Task 9. §6 endpoints (add-platform, format on create) → Task 10; resolve enrichment → Task 8. `registry_upcs_for_game` → Task 2. Web editors (§6 web) and mobile UI (§7) are **Plan 2 / Plan 3** (out of scope here, by design).
- **Deferred to later plans:** the per-platform format *editor UI* and subscription/mobile *membership UI* live on the web (Plan 2); the scan-for-info *screen* + auto-rearm/FAB UX live on Android (Plan 3). This plan delivers the data + API they consume.
- **Type consistency:** `registry_put`/`registry_get`/`registry_upcs_for_game`/`owned_platforms_for`/`parse_retail_platform` signatures are used identically across tasks; `candidates_for` kwargs (`drop_fan_types`, `restrict_to_platform`) defined in Task 7 and consumed in Task 8; candidate `game_type` key added in Task 8 and read in Task 9.
- **Open verification at implementation:** (a) where the `physical` flag lives (`games` vs `user_ratings`) — Task 5 test pins it; (b) exact IGDB `game_type` ints — Task 7 keep-set, verify against a live candidate.
```
