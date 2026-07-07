# Collections Display + Series Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the home-rolled series system, make the main library sort alphabetically by title, and add a non-destructive global setting to show a compilation's collection tile, its member games, or both.

**Architecture:** Phase 1 rips out the series layer end-to-end (consumers first, then schema/loaders, then a guarded drop migration) and switches the default sort to title. Phase 2 adds a stable `parent_collection_id` link between member rows and their container row, a `collection_display_mode` preference on `user_profile`, and a pure visibility filter in `/api/games`.

**Tech Stack:** Python 3 / Flask, SQLite, vanilla JS templates, `uv` for env, pytest.

## Global Constraints

- Run tests with `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError: models).
- Lint gate is `ruff check` only. Never run `ruff format` (codebase is hand-aligned).
- Type hints on all new/changed function signatures.
- Named constants over magic strings in conditions; extract literals.
- App runs with `use_reloader=False`; Python route/migration changes require a manual restart to take effect.
- Work on `main`, commit incrementally. Do not touch git working-tree state of unrelated files.
- Migrations must be idempotent and guarded (`IF EXISTS` / column-presence checks), safe to re-run.
- Default host/port are now `0.0.0.0:5150` (env `HOST`/`PORT` override).
- `collection_display_mode` allowed values: `members`, `collection`, `both`. Default and NULL/unknown → `members`.

---

## Phase 1 — Series removal + alphabetical sort

Order is deliberate: remove **consumers** first so the app stays bootable and the
suite stays green between commits, then remove schema/loaders, then drop columns.

### Task 1: Alphabetical sort in `/api/games` + front-end

**Files:**
- Modify: `app.py` (`api_games`, the sort-map + default ORDER BY branch, and the series field selects)
- Modify: `templates/index.html` (`sortGames()` default branch; alphabet-bar key)
- Test: `tests/test_api_games.py`

**Interfaces:**
- Produces: `/api/games` returns rows ordered by `title` (case-insensitive) in the default sort, with no `series_id`/`series_order`/`series_name` fields in the payload.

- [ ] **Step 1: Write/adjust the failing test** — assert default order is alphabetical by title and `series_name` is absent from each row.

```python
def test_api_games_default_sort_is_alphabetical(client):
    r = client.get('/api/games')
    assert r.status_code == 200
    games = r.get_json()
    titles = [g['title'] for g in games]
    assert titles == sorted(titles, key=str.lower)
    assert all('series_name' not in g for g in games)
```

- [ ] **Step 2: Run it, verify it fails**

Run: `uv run python -m pytest tests/test_api_games.py::test_api_games_default_sort_is_alphabetical -v`
Expected: FAIL (series_name present / order not alphabetical).

- [ ] **Step 3: Edit `app.py` `api_games`**
  - Remove `g.series_id`, `ur.series_order`, `s.name AS series_name` from the SELECT and the `LEFT JOIN series s` / series join.
  - In the sort map, delete the `manual` series tiebreaker's series reference if any; change the default (`title`) branch to:

```python
    else:  # title
        order_sql = "ORDER BY g.title COLLATE NOCASE " + order
```

- [ ] **Step 4: Edit `templates/index.html`**
  - `sortGames()` default branch → `arr.sort((a,b) => a.title.localeCompare(b.title))`.
  - Alphabet bar key (letter bucketing + scroll target) → use `g.title` instead of `g.series_name || g.title`.

- [ ] **Step 5: Run tests, verify pass**

Run: `uv run python -m pytest tests/test_api_games.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/index.html tests/test_api_games.py
git commit -m "feat(games): sort library alphabetically by title; drop series sort key"
```

### Task 2: Remove series API routes and pages from `app.py`

**Files:**
- Modify: `app.py` (delete `/series`, `/series/manage`, `/series/<id>` page routes; the entire `/api/series*` block ~lines 1619–2205; `series_role` trait handling; `focus_series_id` in slot create/update; series imports)
- Test: `tests/test_api_games.py` (add a route-absence check)

**Interfaces:**
- Produces: no `/api/series*` or `/series*` routes exist; slot create/update no longer accept `focus_series_id`.

- [ ] **Step 1: Failing test** — assert series routes are gone.

```python
def test_series_routes_removed(client):
    assert client.get('/api/series').status_code == 404
    assert client.get('/series').status_code == 404
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run python -m pytest tests/test_api_games.py::test_series_routes_removed -v`
Expected: FAIL (routes still 200).

- [ ] **Step 3: Delete in `app.py`**
  - Page routes `series_page`, `series_manage`, `series_detail`.
  - The full series API block (`api_series` through `api_remove_game_from_series`).
  - `series_role` handling in the traits update path.
  - `focus_series_id` reads/writes in slot create + update handlers.
  - Imports of `add_series_pattern`, `apply_series_catalog`, and any `series`-only helpers.

- [ ] **Step 4: Run, verify pass + app imports**

Run: `uv run python -m pytest tests/test_api_games.py -v && uv run python -c "import app"`
Expected: PASS and clean import (NameError here means a missed reference — fix it).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "refactor(series): remove series API routes, pages, and slot focus_series wiring"
```

### Task 3: Remove series from `slots.py` and `decider.py`

**Files:**
- Modify: `slots.py` (`SERIES_BOOST`, `FOCUS_SERIES_BOOST`, `_slot_recent_series_id`, focus-series boost + role routing)
- Modify: `decider.py` (series columns in the snapshot query; focus-series prompt line)
- Test: `tests/test_slots_migration.py`, existing slots tests (adjust)

**Interfaces:**
- Produces: `slots.rank_*` scoring no longer references any series concept; decider snapshot has no series columns.

- [ ] **Step 1: Adjust failing tests** — remove series expectations from slots ranking tests; add an assertion that ranking runs without `focus_series_id`.
- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_slots_migration.py -v`
- [ ] **Step 3: Edit `slots.py`** — delete the two boost constants, `_slot_recent_series_id`, and every branch that reads a series id/role; simplify the ranking accordingly.
- [ ] **Step 4: Edit `decider.py`** — drop `series_id`/`series_role`/`series_name` from the snapshot SELECT and remove the focus-series line from the prompt builder.
- [ ] **Step 5: Run, verify pass** — `uv run python -m pytest tests/test_slots_migration.py tests/test_slots_series_ranking.py -v` (the latter file is deleted in Task 7; here just confirm no import errors from the modules).
- [ ] **Step 6: Commit**

```bash
git add slots.py decider.py tests/test_slots_migration.py
git commit -m "refactor(series): drop series boosts from slots ranking and decider snapshot"
```

### Task 4: Remove series from `dedup.py` and `import_scraped.py`

**Files:**
- Modify: `dedup.py` (`infer_series_name` and its callers within the module)
- Modify: `import_scraped.py` (series carry-over in migration merge; `--apply-series-catalog` CLI flag/handler)
- Test: `tests/test_dedup.py`

**Interfaces:**
- Produces: `dedup` exposes no `infer_series_name`; `import_scraped` has no series carry-over and no `--apply-series-catalog` flag.

- [ ] **Step 1: Adjust `tests/test_dedup.py`** — remove/replace assertions that call `infer_series_name`.
- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_dedup.py -v`
- [ ] **Step 3: Edit `dedup.py`** — delete `infer_series_name` and any internal use.
- [ ] **Step 4: Edit `import_scraped.py`** — remove the `series_id`/`series_source` carry-over block and the `--apply-series-catalog` argparse entry + handler.
- [ ] **Step 5: Run, verify pass** — `uv run python -m pytest tests/test_dedup.py -v && uv run python -c "import dedup, import_scraped"`
- [ ] **Step 6: Commit**

```bash
git add dedup.py import_scraped.py tests/test_dedup.py
git commit -m "refactor(series): remove infer_series_name and import series carry-over"
```

### Task 5: Remove series from templates and delete JSON config

**Files:**
- Delete: `templates/series.html`, `templates/series_overview.html`, `series_catalog.default.json`, `series_patterns.default.json`, `series_patterns.json`
- Modify: `templates/base.html` (nav link, series-role selects, create/add-series dialogs, `inferSeriesName`, series display in modal)
- Test: `tests/test_shared_hero_render.py`, `tests/test_recommendations_render.py` (adjust)

**Interfaces:**
- Produces: no template renders any series UI; `base.html` has no `series`-keyed markup or JS.

- [ ] **Step 1: Adjust render tests** — remove series-markup assertions.
- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_shared_hero_render.py tests/test_recommendations_render.py -v`
- [ ] **Step 3: Delete the five files; edit `base.html`** — remove the series nav `<a>`, the series-role `<select>` blocks, the create/add-series `<dialog>`s, `inferSeriesName`, and series fields shown in the game modal.
- [ ] **Step 4: Run, verify pass** — `uv run python -m pytest tests/test_shared_hero_render.py tests/test_recommendations_render.py -v`
- [ ] **Step 5: Commit**

```bash
git add -A templates series_catalog.default.json series_patterns.default.json series_patterns.json tests/test_shared_hero_render.py tests/test_recommendations_render.py
git commit -m "refactor(series): delete series templates, base.html UI, and JSON catalogs"
```

### Task 6: Remove series from `models.py` + guarded drop migration

**Files:**
- Modify: `models.py` (delete loaders/appliers, stop creating series table + columns, add drop migration, path constants)
- Test: `tests/test_fresh_install.py`, new `tests/test_series_drop_migration.py`

**Interfaces:**
- Produces: `models` exposes none of `load_series_patterns`, `match_series_prefix`, `load_series_catalog`, `add_series_pattern`, `auto_populate_series`, `backfill_series_source`, `apply_series_catalog`, `migrate_series_columns`, `migrate_series_source`, `SERIES_ROLE_VALUES`. `migrate_db` runs `migrate_drop_series` which is idempotent.

- [ ] **Step 1: Write failing test** for the drop migration on a seeded copy.

```python
def test_migrate_drop_series_is_idempotent(tmp_path):
    import sqlite3, models
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE series(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT, series_role TEXT, series_role_source TEXT);"
    )
    conn.commit()
    models.migrate_drop_series(conn)
    models.migrate_drop_series(conn)  # idempotent
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'series' not in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    assert 'series_role' not in cols and 'series_role_source' not in cols
```

- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_series_drop_migration.py -v` (AttributeError: migrate_drop_series).

- [ ] **Step 3: Implement in `models.py`**
  - Delete the listed loaders/appliers, `SERIES_ROLE_VALUES`, and the series path constants.
  - Remove series-table creation and the `user_ratings`/`games`/`slots` series column additions from the schema/migration wiring.
  - Add:

```python
_SERIES_GAME_COLS = ("series_role", "series_role_source")
_SERIES_RATING_COLS = ("series_id", "series_order", "series_source")


def migrate_drop_series(conn: sqlite3.Connection) -> None:
    """Idempotently drop the retired home-rolled series schema (SQLite >= 3.35)."""
    conn.execute("DROP TABLE IF EXISTS series")
    for table, cols in (("games", _SERIES_GAME_COLS), ("user_ratings", _SERIES_RATING_COLS)):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                except sqlite3.OperationalError as exc:  # older SQLite: leave unused
                    logging.warning("could not drop %s.%s: %s", table, col, exc)
    slot_cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)")}
    if "focus_series_id" in slot_cols:
        try:
            conn.execute("ALTER TABLE slots DROP COLUMN focus_series_id")
        except sqlite3.OperationalError as exc:
            logging.warning("could not drop slots.focus_series_id: %s", exc)
    conn.commit()
```
  - Call `migrate_drop_series(conn)` from `migrate_db` where `apply_series_catalog`/`migrate_series_columns` used to be wired.

- [ ] **Step 4: Run, verify pass** — `uv run python -m pytest tests/test_series_drop_migration.py tests/test_fresh_install.py -v && uv run python -c "import app"`

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_series_drop_migration.py tests/test_fresh_install.py
git commit -m "refactor(series): remove series schema/loaders; add guarded drop migration"
```

### Task 7: Delete series-only tests and excise embedded series assertions

**Files:**
- Delete: `tests/test_series_catalog.py`, `tests/test_series_catalog_ff.py`, `tests/test_series_patterns.py`, `tests/test_series_sort.py`, `tests/test_focus_series_migration.py`, `tests/test_slots_series_ranking.py`, `tests/test_slots_focus_series_ranking.py`, `tests/test_slots_rank_lock.py`, `tests/test_api_slots_focus.py`
- Modify: `tests/test_api_games_traits.py`, `tests/test_bundles.py`, `tests/test_game_traits_catalog.py`, `tests/test_nintendo_catalog.py` (remove series assertions/fields)

**Interfaces:**
- Produces: the full suite is green with no series references.

- [ ] **Step 1: Delete the series-only test files.**
- [ ] **Step 2: Grep for stragglers** — `uv run python -m pytest -q 2>&1 | tail -20`; also `grep -rIn "series" tests/ | grep -v "collections"`.
- [ ] **Step 3: Excise remaining series assertions/fields** in the shared test files listed.
- [ ] **Step 4: Run full suite, verify green** — `uv run python -m pytest -q` and `uv run ruff check .`
- [ ] **Step 5: Commit**

```bash
git add -A tests
git commit -m "test(series): delete series-only tests and excise embedded series assertions"
```

---

## Phase 2 — Collection display setting

### Task 8: `parent_collection_id` column + linking migration

**Files:**
- Modify: `models.py` (add `migrate_parent_collection` + a normalized-title linker)
- Test: new `tests/test_collection_linking.py`

**Interfaces:**
- Produces: `models.migrate_parent_collection(conn)` adds `games.parent_collection_id INTEGER` and links members to containers idempotently. Helper `models._normalize_collection_key(s: str) -> str` collapses case/whitespace/punctuation.

- [ ] **Step 1: Write failing tests.**

```python
def test_links_member_to_container(tmp_path):
    import sqlite3, models
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(
        "CREATE TABLE games(id INTEGER PRIMARY KEY, title TEXT, collection_name TEXT);"
        "INSERT INTO games(id,title,collection_name) VALUES"
        " (1,'Mega Man Battle Network Legacy Collection Vol. 1', NULL),"
        " (2,'Mega Man Battle Network', 'Megaman Battle Network Legacy Collection Vol.1'),"
        " (3,'Mega Man', 'Mega Man Legacy Collection');"  # container deleted -> stays NULL
    )
    conn.commit()
    models.migrate_parent_collection(conn)
    models.migrate_parent_collection(conn)  # idempotent
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=2").fetchone()[0] == 1
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=3").fetchone()[0] is None
    assert conn.execute("SELECT parent_collection_id FROM games WHERE id=1").fetchone()[0] is None
```

- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_collection_linking.py -v`

- [ ] **Step 3: Implement in `models.py`.**

```python
import re

def _normalize_collection_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def migrate_parent_collection(conn: sqlite3.Connection) -> None:
    """Add games.parent_collection_id and link members to their container row."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    if "parent_collection_id" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN parent_collection_id INTEGER")
    # Build title -> id map for potential container rows.
    by_key: dict[str, int] = {}
    for gid, title in conn.execute("SELECT id, title FROM games"):
        by_key.setdefault(_normalize_collection_key(title), gid)
    for gid, cname in conn.execute(
        "SELECT id, collection_name FROM games WHERE collection_name IS NOT NULL"
    ):
        container = by_key.get(_normalize_collection_key(cname))
        # Never link a row to itself.
        conn.execute(
            "UPDATE games SET parent_collection_id=? WHERE id=?",
            (container if container and container != gid else None, gid),
        )
    conn.commit()
```
  Wire `migrate_parent_collection(conn)` into `migrate_db`.

- [ ] **Step 4: Run, verify pass** — `uv run python -m pytest tests/test_collection_linking.py -v`

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_collection_linking.py
git commit -m "feat(collections): add parent_collection_id link + linking migration"
```

### Task 9: `collection_display_mode` on `user_profile` + `/api/profile`

**Files:**
- Modify: `models.py` (`migrate_user_profile`: add `collection_display_mode TEXT`)
- Modify: `app.py` (`GET/PUT /api/profile`: read/write + validate the mode)
- Test: `tests/test_api_profile.py`

**Interfaces:**
- Produces: `GET /api/profile` returns `collection_display_mode` (default `'members'`); `PUT` accepts it, rejects/normalizes invalid values. Constant `COLLECTION_DISPLAY_MODES = ("members", "collection", "both")` in `app.py`.

- [ ] **Step 1: Write failing tests.**

```python
def test_profile_display_mode_roundtrip(client):
    assert client.get('/api/profile').get_json()['collection_display_mode'] == 'members'
    client.put('/api/profile', json={'collection_display_mode': 'both'})
    assert client.get('/api/profile').get_json()['collection_display_mode'] == 'both'

def test_profile_display_mode_invalid_normalized(client):
    client.put('/api/profile', json={'collection_display_mode': 'bogus'})
    assert client.get('/api/profile').get_json()['collection_display_mode'] == 'members'
```

- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_api_profile.py -v`
- [ ] **Step 3: Implement** — add the column in `migrate_user_profile`; add `COLLECTION_DISPLAY_MODES`; in `GET` coalesce NULL→`members`; in `PUT` accept the key and coerce anything not in the tuple to `members`.
- [ ] **Step 4: Run, verify pass** — `uv run python -m pytest tests/test_api_profile.py -v`
- [ ] **Step 5: Commit**

```bash
git add models.py app.py tests/test_api_profile.py
git commit -m "feat(collections): add collection_display_mode preference to /api/profile"
```

### Task 10: Apply the display filter in `/api/games`

**Files:**
- Modify: `app.py` (`api_games`: filter rows by the active mode)
- Test: `tests/test_api_games.py`

**Interfaces:**
- Consumes: `parent_collection_id` (Task 8), `collection_display_mode` (Task 9).
- Produces: `/api/games` visible-row set obeys the mode. A container = a row whose id appears as some row's `parent_collection_id`; a member = a row with non-NULL `parent_collection_id`.

- [ ] **Step 1: Write failing tests** against a fixture DB with a container (id=1) + member (id=2) and a standalone (id=9).

```python
def test_games_mode_members_hides_container(client, seed_compilation):
    set_mode(client, 'members')
    ids = {g['id'] for g in client.get('/api/games').get_json()}
    assert 1 not in ids and 2 in ids and 9 in ids

def test_games_mode_collection_hides_members(client, seed_compilation):
    set_mode(client, 'collection')
    ids = {g['id'] for g in client.get('/api/games').get_json()}
    assert 1 in ids and 2 not in ids and 9 in ids

def test_games_mode_both_shows_all(client, seed_compilation):
    set_mode(client, 'both')
    ids = {g['id'] for g in client.get('/api/games').get_json()}
    assert {1, 2, 9} <= ids
```

- [ ] **Step 2: Run, verify fail** — `uv run python -m pytest tests/test_api_games.py -k mode -v`
- [ ] **Step 3: Implement** — after fetching rows, read the mode; compute the container-id set (`{parent_collection_id for rows where it is not NULL}`); filter:
  - `members`: drop rows whose id is in the container set.
  - `collection`: drop rows whose `parent_collection_id` is not NULL.
  - `both`: no filtering.
  Do this in SQL where practical, else in Python post-fetch. Keep the alphabetical order from Task 1.
- [ ] **Step 4: Run, verify pass** — `uv run python -m pytest tests/test_api_games.py -v`
- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games.py
git commit -m "feat(collections): filter library by collection_display_mode"
```

### Task 11: Settings UI control

**Files:**
- Modify: `templates/settings.html` (a "Collection display" `<select>` bound to `/api/profile`)
- Test: manual browser verification (documented), plus `tests/test_recommendations_render.py`-style render assertion if a settings render test exists.

**Interfaces:**
- Consumes: `GET/PUT /api/profile` `collection_display_mode`.

- [ ] **Step 1: Add the control** — a labeled `<select>` with options Member games (`members`) / Collection (`collection`) / Both (`both`); on load set from `GET /api/profile`; on change `PUT /api/profile`.
- [ ] **Step 2: Verify in a browser** on a DB **copy** at an isolated port (per the verify-UI-changes convention): switch each mode, reload `/`, confirm the Battle Network / Legacy of Thieves containers appear/disappear as expected.
- [ ] **Step 3: Run full suite + lint** — `uv run python -m pytest -q && uv run ruff check .`
- [ ] **Step 4: Commit**

```bash
git add templates/settings.html
git commit -m "feat(collections): add collection display mode selector to Settings"
```

---

## Self-review notes

- **Spec coverage:** Part A → Tasks 2–7; Part B (sort) → Task 1; Part C (link, setting, filter, UI) → Tasks 8–11. Column-drop safety (guarded/idempotent) → Task 6. Non-destructive filter → Task 10 (visibility only). All spec sections mapped.
- **Live-DB rollout** (backup + copy-verify + restart) is operational, handled by the controller after Task 11, not a code task.
- **Consistency:** `parent_collection_id`, `collection_display_mode`, `COLLECTION_DISPLAY_MODES`, `migrate_drop_series`, `migrate_parent_collection`, `_normalize_collection_key` are used with identical names across tasks.
