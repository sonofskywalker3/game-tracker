# Catalog-Driven Series Defaulting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-title series catalog (`normalized_title → {series, order, role}`) that defaults games into series — including titles prefix-matching can't reach — owns within-series order and `series_role`, and never clobbers a manual assignment.

**Architecture:** Mirror the existing `game_traits` / `bundle_catalog` pattern: a committed `series_catalog.default.json` seed (+ gitignored per-user override), a `load_series_catalog()` loader, and an idempotent, fill-only `apply_series_catalog(conn, game_id=None, *, dry_run=False)` in `models.py` (next to `apply_traits_catalog`, so it can be called from `migrate_db` and on-add without circular imports). A new `user_ratings.series_source` (`auto`/`catalog`/`manual`) column tracks provenance so the catalog may override prefix-`auto` assignments but never `manual` ones. The bulk operation is controller-run (AI-draft Workflow → dry-run → backup → apply) via a new `--apply-series-catalog` CLI flag in `import_scraped.py`.

**Tech Stack:** Python 3 (stdlib `sqlite3`, `json`, `argparse`), Flask, pytest. Package mgmt `uv`. Tests: `uv run python -m pytest`. Lint: `ruff check` only (never `ruff format`).

**Conventions (non-negotiable, from project memory):**
- Run tests with `uv run python -m pytest` (plain `uv run pytest` fails: ModuleNotFoundError).
- Lint with `ruff check` only. Never `ruff format` (codebase is hand-aligned).
- Match write-backs by `normalized_title`, never a returned id.
- Impl subagents: pytest temp-DB only; never touch the live `games.db` or run the app.
- Do NOT `git push` (owner is holding pushes).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

**Module-placement decision (refines the spec's "e.g. series_pipeline.py"):** `apply_series_catalog` lives in `models.py` (it must be reachable from `migrate_db` and the on-add hook; `import_scraped` already imports `models`, so putting it elsewhere risks a circular import). The CLI runner lives in `import_scraped.main()` beside the existing `--apply-bundle-catalog` flag — the established home for catalog-apply CLIs. No new `series_pipeline.py` is created.

**Create-threshold simplification (refines the spec):** the spec said count "catalog-matched plus prefix-assignable" games when deciding whether to create a new series. Because the AI-draft step classifies the **full library**, every real multi-game series gets ≥2 catalog entries, so counting **catalog-matched** games alone is sufficient (and series that prefix-matching can create already exist → "join always" covers them). Implementation counts catalog-matched games per series.

---

## File Structure

- `series_catalog.default.json` — **Create.** Committed seed, initially `{}`. Per-title series catalog.
- `series_catalog.json` — per-user override (gitignored; not created here, referenced by the loader).
- `models.py` — **Modify.** Add path constants, `load_series_catalog()`, `match_series_prefix()` helper, `migrate_series_source()`, `backfill_series_source()`, `apply_series_catalog()`, `SERIES_ROLE_VALUES`; stamp `series_source='auto'` in `auto_populate_series`; drop `series_role` from `TRAIT_FIELDS`; wire new calls into `migrate_db` + `init_db` schema.
- `app.py` — **Modify.** Call `apply_series_catalog(conn, game_id)` on add; stamp `series_source='manual'` on the series UI write paths; clear it on removal.
- `import_scraped.py` — **Modify.** Add `--apply-series-catalog` CLI flag + dry-run report printing in `main()`.
- `tests/test_series_catalog.py` — **Create.** Tests for loader, migration, backfill, apply, dry-run.
- `tests/test_series_patterns.py` — **Modify.** Add a regression test that `auto_populate_series` stamps `series_source='auto'`.
- `tests/test_game_traits_catalog.py` — **Modify.** Update trait tests so they no longer expect `apply_traits_catalog` to set `series_role` (role ownership moves to the series catalog).
- `tests/conftest.py` — **Modify.** Add `models.migrate_series_source(conn)` to the `temp_db` fixture.
- `.gitignore` — **Verify/Modify.** Ensure `series_catalog.json` (per-user) is ignored.

---

## Task 1: Loader + committed seed file

**Files:**
- Create: `series_catalog.default.json`
- Modify: `models.py` (path constants after line 11; `load_series_catalog` after `load_bundle_catalog`, ~line 42)
- Create: `tests/test_series_catalog.py`
- Verify: `.gitignore`

- [ ] **Step 1: Create the committed seed file**

Create `series_catalog.default.json` with an empty object (the AI-draft step fills it later):

```json
{}
```

- [ ] **Step 2: Verify the per-user override is gitignored**

Run: `git check-ignore series_catalog.json`
Expected: prints `series_catalog.json` (already ignored). If it prints nothing, append a line `series_catalog.json` to `.gitignore` next to the existing `series_patterns.json` / `game_traits.json` / `bundle_catalog.json` entries.

- [ ] **Step 3: Write the failing loader tests**

Create `tests/test_series_catalog.py`:

```python
import json

import models


def test_load_series_catalog_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "series_catalog.default.json"
    default.write_text(json.dumps({"halo": {"series": "Halo", "order": 1, "role": "mainline"}}),
                       encoding="utf-8")
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", tmp_path / "series_catalog.json")
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", default)
    assert models.load_series_catalog() == {"halo": {"series": "Halo", "order": 1, "role": "mainline"}}


def test_load_series_catalog_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "series_catalog.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "series_catalog.json"
    per_user.write_text(json.dumps({"doom": {"series": "DOOM"}}), encoding="utf-8")
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", per_user)
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", tmp_path / "series_catalog.default.json")
    assert models.load_series_catalog() == {"doom": {"series": "DOOM"}}


def test_load_series_catalog_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_series_catalog() == {}


def test_load_series_catalog_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "series_catalog.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", tmp_path / "series_catalog.json")
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", bad)
    assert models.load_series_catalog() == {}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_series_catalog.py -v`
Expected: FAIL — `AttributeError: module 'models' has no attribute 'SERIES_CATALOG_PATH'` (and `load_series_catalog`).

- [ ] **Step 5: Add the path constants**

In `models.py`, after line 11 (the `BUNDLE_CATALOG_DEFAULT_PATH` line), add:

```python
SERIES_CATALOG_PATH = Path(__file__).parent / "series_catalog.json"            # per-user (gitignored)
SERIES_CATALOG_DEFAULT_PATH = Path(__file__).parent / "series_catalog.default.json"  # committed seed
```

- [ ] **Step 6: Add the loader**

In `models.py`, immediately after `load_bundle_catalog` (ends ~line 41), add:

```python
def load_series_catalog() -> dict:
    """Load the normalized_title->series-entry catalog (per-user file, else committed seed)."""
    path = SERIES_CATALOG_PATH if SERIES_CATALOG_PATH.exists() else SERIES_CATALOG_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_series_catalog.py -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Lint**

Run: `uv run ruff check models.py tests/test_series_catalog.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add series_catalog.default.json models.py tests/test_series_catalog.py .gitignore
git commit -m "$(cat <<'EOF'
feat(series): series_catalog loader + committed empty default seed

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `migrate_series_source` column + fixture wiring

**Files:**
- Modify: `models.py` (`init_db` user_ratings schema ~line 182; new `migrate_series_source` near `migrate_collection_name` ~line 704)
- Modify: `tests/conftest.py` (add the migration to `temp_db`)
- Modify: `tests/test_series_catalog.py`

- [ ] **Step 1: Write the failing migration tests**

Append to `tests/test_series_catalog.py`:

```python
def test_migrate_series_source_adds_column(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(user_ratings)").fetchall()}
    assert "series_source" in cols
    conn.close()


def test_migrate_series_source_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_series_source(conn)
    models.migrate_series_source(conn)  # second run must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(user_ratings)").fetchall()}
    assert "series_source" in cols
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_series_catalog.py::test_migrate_series_source_adds_column -v`
Expected: FAIL — `AttributeError: module 'models' has no attribute 'migrate_series_source'` (the fixture references it).

- [ ] **Step 3: Add the migration function**

In `models.py`, after `migrate_collection_name` (ends ~line 712), add:

```python
def migrate_series_source(conn: sqlite3.Connection) -> None:
    """Add user_ratings.series_source (auto/catalog/manual) so the series catalog can
    override prefix-auto assignments while never clobbering a manual one. Idempotent.
    Null is treated as unset (overwritable). See backfill_series_source for existing rows.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(user_ratings)").fetchall()]
    if "series_source" not in cols:
        conn.execute("ALTER TABLE user_ratings ADD COLUMN series_source TEXT")
    conn.commit()
```

- [ ] **Step 4: Add the column to the fresh-DB schema**

In `models.py` `init_db`, in the `CREATE TABLE IF NOT EXISTS user_ratings` block, add a `series_source` column next to `series_order` (line 183). Change:

```python
            series_id INTEGER,  -- which series this game belongs to
            series_order INTEGER,  -- order within the series
```

to:

```python
            series_id INTEGER,  -- which series this game belongs to
            series_order INTEGER,  -- order within the series
            series_source TEXT,  -- provenance of series_id: auto / catalog / manual
```

- [ ] **Step 5: Wire the migration into the temp_db fixture**

In `tests/conftest.py`, after the `models.migrate_collection_name(conn)` line (line 23), add:

```python
    models.migrate_series_source(conn)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_series_catalog.py -v`
Expected: PASS (6 passed total).

- [ ] **Step 7: Lint**

Run: `uv run ruff check models.py tests/conftest.py tests/test_series_catalog.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add models.py tests/conftest.py tests/test_series_catalog.py
git commit -m "$(cat <<'EOF'
feat(series): migrate_series_source column on user_ratings (auto/catalog/manual)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Shared prefix-match helper + `auto_populate_series` stamps `'auto'`

**Files:**
- Modify: `models.py` (`match_series_prefix` near `load_series_patterns`; `auto_populate_series` ~lines 860-913)
- Modify: `tests/test_series_patterns.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_series_patterns.py`:

```python
def test_match_series_prefix_longest_wins():
    known = {"Cyberpunk": "Cyberpunk", "Cyberpunk 2077": "Cyberpunk 2077"}
    assert models.match_series_prefix("Cyberpunk 2077: Phantom Liberty", known) == "Cyberpunk 2077"
    assert models.match_series_prefix("Halo", known) is None


def test_auto_populate_stamps_auto_source(temp_db):
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Halo: Combat Evolved", "halo combat evolved"),
         ("Halo 2", "halo 2")],
    )
    conn.commit()
    conn.close()

    models.auto_populate_series()

    conn = models.get_db()
    sources = [r["series_source"] for r in conn.execute(
        "SELECT ur.series_source FROM games g JOIN user_ratings ur ON ur.game_id = g.id "
        "WHERE g.title LIKE 'Halo%'")]
    conn.close()
    assert sources and all(s == "auto" for s in sources)
```

Note: `test_auto_populate_stamps_auto_source` requires `series_patterns.default.json` to contain `"Halo": "Halo"` (it does — verified in the committed seed). It uses the real loader (no monkeypatch), consistent with the existing `test_auto_populate_prefers_longest_prefix`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_series_patterns.py::test_match_series_prefix_longest_wins tests/test_series_patterns.py::test_auto_populate_stamps_auto_source -v`
Expected: FAIL — `AttributeError: ... 'match_series_prefix'` and (after the helper exists) the source assertion fails because `auto_populate_series` doesn't write `series_source`.

- [ ] **Step 3: Add the shared helper**

In `models.py`, immediately after `load_series_patterns` (ends ~line 21), add:

```python
def match_series_prefix(title: str, known_series: dict) -> str | None:
    """Return the series name for the longest matching title prefix, else None.
    Longest-first so a specific prefix beats a shorter one ('Cyberpunk 2077' > 'Cyberpunk')."""
    for prefix, series_name in sorted(known_series.items(), key=lambda kv: -len(kv[0])):
        if title.upper().startswith(prefix.upper()):
            return series_name
    return None
```

- [ ] **Step 4: Use the helper in `auto_populate_series` and stamp `'auto'`**

In `models.py` `auto_populate_series`, replace the inline longest-prefix loop (lines 860-864):

```python
        matched_series = None
        for prefix, series_name in sorted(known_series.items(), key=lambda kv: -len(kv[0])):
            if title.upper().startswith(prefix.upper()):
                matched_series = series_name
                break
```

with:

```python
        matched_series = match_series_prefix(title, known_series)
```

Then change the assignment INSERT (lines 905-912) to stamp `series_source='auto'`:

```python
            conn.execute("""
                INSERT INTO user_ratings (game_id, series_id, series_order, series_source)
                VALUES (?, ?, ?, 'auto')
                ON CONFLICT(game_id) DO UPDATE SET
                    series_id = excluded.series_id,
                    series_order = excluded.series_order,
                    series_source = excluded.series_source,
                    updated_at = CURRENT_TIMESTAMP
            """, (game_id, series_id, order))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_series_patterns.py -v`
Expected: PASS (all, including the pre-existing `test_auto_populate_prefers_longest_prefix`).

- [ ] **Step 6: Lint**

Run: `uv run ruff check models.py tests/test_series_patterns.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add models.py tests/test_series_patterns.py
git commit -m "$(cat <<'EOF'
refactor(series): share match_series_prefix; auto_populate_series stamps source=auto

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `backfill_series_source` reconstruction

**Files:**
- Modify: `models.py` (new `backfill_series_source` after `auto_populate_series`)
- Modify: `tests/test_series_catalog.py`

Reconstruction rule: for each existing assignment whose `series_source IS NULL`, stamp `'auto'` if the current series name equals what prefix-matching would produce for the title, else `'manual'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_series_catalog.py`:

```python
def _seed_series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return sid


def _add_game(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def _assign(conn, gid, sid, order=0, source=None):
    conn.execute(
        "INSERT INTO user_ratings (game_id, series_id, series_order, series_source) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(game_id) DO UPDATE SET series_id = excluded.series_id, "
        "series_order = excluded.series_order, series_source = excluded.series_source",
        (gid, sid, order, source))
    conn.commit()


def test_backfill_marks_prefix_match_as_auto(monkeypatch, temp_db):
    monkeypatch.setattr(models, "load_series_patterns", lambda: {"Halo": "Halo"})
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    gid = _add_game(conn, "Halo 2")
    _assign(conn, gid, sid, source=None)  # pre-existing, unstamped

    models.backfill_series_source(conn)

    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    assert src == "auto"  # current series == prefix result
    conn.close()


def test_backfill_marks_nonprefix_as_manual(monkeypatch, temp_db):
    monkeypatch.setattr(models, "load_series_patterns", lambda: {"Halo": "Halo"})
    conn = models.get_db()
    sid = _seed_series(conn, "Assassin's Creed")
    gid = _add_game(conn, "Brotherhood")  # does not start with "Assassin's Creed"
    _assign(conn, gid, sid, source=None)

    models.backfill_series_source(conn)

    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    assert src == "manual"  # human must have set it
    conn.close()


def test_backfill_leaves_already_stamped_rows(monkeypatch, temp_db):
    monkeypatch.setattr(models, "load_series_patterns", lambda: {"Halo": "Halo"})
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    gid = _add_game(conn, "Halo 2")
    _assign(conn, gid, sid, source="manual")  # already stamped manual

    models.backfill_series_source(conn)

    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    assert src == "manual"  # untouched
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_series_catalog.py -k backfill -v`
Expected: FAIL — `AttributeError: ... 'backfill_series_source'`.

- [ ] **Step 3: Add the function**

In `models.py`, after `auto_populate_series` (ends ~line 919), add:

```python
def backfill_series_source(conn: sqlite3.Connection) -> None:
    """Stamp series_source on pre-existing assignments that predate the column.
    'auto' if the current series equals what prefix-matching would produce for the
    title, else 'manual' (a human must have set it). Only fills NULL source rows."""
    known = load_series_patterns()
    rows = conn.execute("""
        SELECT ur.game_id, g.title, s.name AS series_name
        FROM user_ratings ur
        JOIN games g ON g.id = ur.game_id
        JOIN series s ON s.id = ur.series_id
        WHERE ur.series_id IS NOT NULL AND ur.series_source IS NULL
    """).fetchall()
    for r in rows:
        source = "auto" if match_series_prefix(r["title"], known) == r["series_name"] else "manual"
        conn.execute("UPDATE user_ratings SET series_source = ? WHERE game_id = ?",
                     (source, r["game_id"]))
    conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_series_catalog.py -k backfill -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint**

Run: `uv run ruff check models.py tests/test_series_catalog.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add models.py tests/test_series_catalog.py
git commit -m "$(cat <<'EOF'
feat(series): backfill_series_source reconstructs auto vs manual on existing rows

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `apply_series_catalog` (membership + order + role)

**Files:**
- Modify: `models.py` (`SERIES_ROLE_VALUES` constant + `apply_series_catalog` after `backfill_series_source`)
- Modify: `tests/test_series_catalog.py`

Behavior (from the spec):
- **Pass A (membership + order):** bucket catalog-matched games by target `series`. If the series exists → **join always**. If not → **create only when ≥2** catalog-matched games map to it (else `skipped_singleton`). For each game, write `series_id` + `series_order = entry.order` + `series_source='catalog'` **unless** the current `series_source == 'manual'` (locked). Absent `order` leaves the existing `series_order` unchanged (COALESCE).
- **Pass B (role, independent):** write `games.series_role = entry.role` + `series_role_source='catalog'` unless `series_role_source == 'manual'`. Only `mainline`/`spinoff` accepted.
- `game_id` scopes both passes to one game. `dry_run=True` writes nothing and returns the report. Missing catalog/entry/file → no-op (`[]`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_series_catalog.py` (reuses `_add_game`, `_seed_series`, `_assign` from Task 4):

```python
def _assignment(conn, gid):
    r = conn.execute(
        "SELECT s.name AS series, ur.series_order, ur.series_source "
        "FROM user_ratings ur LEFT JOIN series s ON s.id = ur.series_id WHERE ur.game_id = ?",
        (gid,)).fetchone()
    return dict(r) if r else None


def _role(conn, gid):
    r = conn.execute("SELECT series_role, series_role_source FROM games WHERE id = ?",
                     (gid,)).fetchone()
    return dict(r)


def test_apply_creates_series_at_two_and_assigns(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man")
    b = _add_game(conn, "Mega Man 2")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man":   {"series": "Mega Man", "order": 1, "role": "mainline"},
        "mega man 2": {"series": "Mega Man", "order": 2, "role": "mainline"},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Mega Man"
    assert _assignment(conn, a)["series_order"] == 1
    assert _assignment(conn, a)["series_source"] == "catalog"
    assert _assignment(conn, b)["series_order"] == 2
    conn.close()


def test_apply_skips_singleton_new_series(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Yakuza Kiwami")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "yakuza kiwami": {"series": "Like a Dragon", "order": 1},
    })
    report = models.apply_series_catalog(conn)
    assert _assignment(conn, a) is None  # not assigned; no series created
    assert conn.execute("SELECT COUNT(*) FROM series WHERE name = 'Like a Dragon'").fetchone()[0] == 0
    assert any(r["action"] == "skipped_singleton" for r in report)
    conn.close()


def test_apply_joins_existing_series_even_singleton(monkeypatch, temp_db):
    conn = models.get_db()
    sid = _seed_series(conn, "Assassin's Creed")
    a = _add_game(conn, "Brotherhood")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "brotherhood": {"series": "Assassin's Creed", "order": 3, "role": "spinoff"},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Assassin's Creed"
    assert _assignment(conn, a)["series_source"] == "catalog"
    conn.close()


def test_apply_fill_only_skips_manual_membership(monkeypatch, temp_db):
    conn = models.get_db()
    keep = _seed_series(conn, "Mario")
    a = _add_game(conn, "Brotherhood")
    _assign(conn, a, keep, order=9, source="manual")  # user pinned it elsewhere
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "brotherhood": {"series": "Assassin's Creed", "order": 3},
    })
    # AC doesn't exist + only 1 catalog game -> would be singleton anyway; seed AC so it could join:
    _seed_series(conn, "Assassin's Creed")
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Mario"  # manual lock respected
    assert _assignment(conn, a)["series_source"] == "manual"
    conn.close()


def test_apply_overrides_auto_membership(monkeypatch, temp_db):
    conn = models.get_db()
    wrong = _seed_series(conn, "Castlevania")
    right = _seed_series(conn, "Assassin's Creed")
    a = _add_game(conn, "Brotherhood")
    _assign(conn, a, wrong, order=1, source="auto")  # prefix-auto put it wrong
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "brotherhood": {"series": "Assassin's Creed", "order": 3},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Assassin's Creed"  # re-homed
    assert _assignment(conn, a)["series_source"] == "catalog"
    conn.close()


def test_apply_writes_role_independent_of_membership(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man X")  # singleton series, won't be created
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man x": {"series": "Mega Man", "order": 1, "role": "spinoff"},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a) is None       # membership skipped (singleton new series)
    assert _role(conn, a)["series_role"] == "spinoff"        # role still filled
    assert _role(conn, a)["series_role_source"] == "catalog"
    conn.close()


def test_apply_role_skips_manual(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man X")
    conn.execute("UPDATE games SET series_role = 'mainline', series_role_source = 'manual' WHERE id = ?",
                 (a,))
    conn.commit()
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man x": {"series": "Mega Man", "role": "spinoff"},
    })
    models.apply_series_catalog(conn)
    assert _role(conn, a)["series_role"] == "mainline"        # locked, untouched
    assert _role(conn, a)["series_role_source"] == "manual"
    conn.close()


def test_apply_absent_order_leaves_series_order(monkeypatch, temp_db):
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    a = _add_game(conn, "Halo Wars")
    _assign(conn, a, sid, order=7, source="auto")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "halo wars": {"series": "Halo", "role": "spinoff"},  # no order
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series_order"] == 7  # preserved
    assert _assignment(conn, a)["series_source"] == "catalog"
    conn.close()


def test_apply_absent_entry_is_noop(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {})
    assert models.apply_series_catalog(conn) == []
    assert _assignment(conn, a) is None
    assert _role(conn, a)["series_role"] is None
    conn.close()


def test_apply_single_game_id(monkeypatch, temp_db):
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    a = _add_game(conn, "Halo 3")
    b = _add_game(conn, "Halo 4")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "halo 3": {"series": "Halo", "order": 3},
        "halo 4": {"series": "Halo", "order": 4},
    })
    models.apply_series_catalog(conn, game_id=a)
    assert _assignment(conn, a)["series"] == "Halo"
    assert _assignment(conn, b) is None  # other game untouched
    conn.close()


def test_apply_dry_run_writes_nothing_returns_report(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man")
    b = _add_game(conn, "Mega Man 2")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man":   {"series": "Mega Man", "order": 1, "role": "mainline"},
        "mega man 2": {"series": "Mega Man", "order": 2, "role": "mainline"},
    })
    report = models.apply_series_catalog(conn, dry_run=True)
    assert _assignment(conn, a) is None  # nothing written
    assert _role(conn, a)["series_role"] is None
    assert conn.execute("SELECT COUNT(*) FROM series WHERE name = 'Mega Man'").fetchone()[0] == 0
    assert any(r["series"] == "Mega Man" and r["assigned"] == 2 for r in report)
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_series_catalog.py -k apply -v`
Expected: FAIL — `AttributeError: ... 'apply_series_catalog'`.

- [ ] **Step 3: Add the constant + function**

In `models.py`, after `backfill_series_source`, add:

```python
SERIES_ROLE_VALUES = frozenset({"mainline", "spinoff"})


def apply_series_catalog(conn: sqlite3.Connection, game_id: int | None = None,
                         *, dry_run: bool = False) -> list[dict]:
    """Default games into series from the per-title catalog. Fill-only, idempotent.

    Pass A (membership+order): bucket catalog-matched games by target series; join an
    existing series always, create a new series only at >=2 catalog-matched games;
    write series_id/series_order/series_source='catalog' unless the current source is
    'manual'. Absent order leaves the existing series_order unchanged. Pass B (role):
    write games.series_role (source='catalog') unless series_role_source='manual'.
    Matches by normalized_title. game_id scopes to one game; dry_run writes nothing.
    Missing catalog/entry/file is a safe no-op. Returns a report list.
    """
    catalog = load_series_catalog()
    if not catalog:
        return []

    game_sql = "SELECT id, normalized_title FROM games"
    params: tuple = ()
    if game_id is not None:
        game_sql += " WHERE id = ?"
        params = (game_id,)
    games = conn.execute(game_sql, params).fetchall()

    # --- Pass A: membership + order ---
    by_series: dict[str, list[tuple[int, dict]]] = {}
    for g in games:
        entry = catalog.get(g["normalized_title"])
        if entry and entry.get("series"):
            by_series.setdefault(entry["series"], []).append((g["id"], entry))

    report: list[dict] = []
    for series_name, members in by_series.items():
        existing = conn.execute("SELECT id FROM series WHERE name = ?", (series_name,)).fetchone()
        if existing:
            series_id, created = existing["id"], False
        elif len(members) < 2:
            report.append({"series": series_name, "action": "skipped_singleton",
                           "created": False, "assigned": 0})
            continue
        else:
            created = True
            if dry_run:
                series_id = None
            else:
                conn.execute("INSERT INTO series (name) VALUES (?)", (series_name,))
                series_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        assigned = 0
        for gid, entry in members:
            cur = conn.execute(
                "SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
            if cur and cur["series_source"] == "manual":
                continue  # locked
            assigned += 1
            if dry_run:
                continue
            conn.execute("""
                INSERT INTO user_ratings (game_id, series_id, series_order, series_source)
                VALUES (?, ?, ?, 'catalog')
                ON CONFLICT(game_id) DO UPDATE SET
                    series_id = excluded.series_id,
                    series_order = COALESCE(excluded.series_order, user_ratings.series_order),
                    series_source = excluded.series_source,
                    updated_at = CURRENT_TIMESTAMP
            """, (gid, series_id, entry.get("order")))
        report.append({"series": series_name, "action": "created" if created else "joined",
                       "created": created, "assigned": assigned})

    # --- Pass B: role (independent of membership) ---
    for g in games:
        entry = catalog.get(g["normalized_title"])
        if not entry:
            continue
        role = entry.get("role")
        if role not in SERIES_ROLE_VALUES:
            continue
        src = conn.execute(
            "SELECT series_role_source FROM games WHERE id = ?", (g["id"],)).fetchone()
        if src and src["series_role_source"] == "manual":
            continue
        if not dry_run:
            conn.execute(
                "UPDATE games SET series_role = ?, series_role_source = 'catalog' WHERE id = ?",
                (role, g["id"]))

    if not dry_run:
        conn.commit()
    return report
```

- [ ] **Step 4: Run the apply tests to verify they pass**

Run: `uv run python -m pytest tests/test_series_catalog.py -k apply -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Run the full series-catalog file**

Run: `uv run python -m pytest tests/test_series_catalog.py -v`
Expected: PASS (all).

- [ ] **Step 6: Lint**

Run: `uv run ruff check models.py tests/test_series_catalog.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add models.py tests/test_series_catalog.py
git commit -m "$(cat <<'EOF'
feat(series): apply_series_catalog (fill-only membership+order+role, dry-run report)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Transfer `series_role` ownership from the trait catalog

The series catalog now owns `series_role`. Remove it from `apply_traits_catalog` so the two catalogs don't both write the column, and update the trait tests accordingly. (`migrate_game_traits` keeps creating the `series_role`/`series_role_source` columns — the series catalog uses them.)

**Files:**
- Modify: `models.py` (`TRAIT_FIELDS` line 715; `apply_traits_catalog` SELECT line 729-730)
- Modify: `tests/test_game_traits_catalog.py`

- [ ] **Step 1: Update the trait tests to the new contract (failing first)**

In `tests/test_game_traits_catalog.py`:

Replace `test_apply_traits_catalog_sets_catalog_values` (lines 73-82) with:

```python
def test_apply_traits_catalog_sets_session_length_only(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short", "series_role": "mainline"}})
    models.apply_traits_catalog(conn)
    t = _traits(conn, gid)
    assert t["session_length"] == "short" and t["session_length_source"] == "catalog"
    # series_role is now owned by the series catalog, NOT the trait catalog:
    assert t["series_role"] is None and t["series_role_source"] is None
    conn.close()
```

Replace `test_apply_traits_catalog_skips_manual` (lines 85-97) with:

```python
def test_apply_traits_catalog_skips_manual(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    conn.execute("UPDATE games SET session_length = 'long', session_length_source = 'manual' "
                 "WHERE id = ?", (gid,))
    conn.commit()
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short"}})
    models.apply_traits_catalog(conn)
    t = _traits(conn, gid)
    assert t["session_length"] == "long" and t["session_length_source"] == "manual"  # locked
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -v`
Expected: FAIL — the two updated tests fail because `apply_traits_catalog` still writes `series_role`.

- [ ] **Step 3: Drop `series_role` from the trait catalog apply**

In `models.py`, change `TRAIT_FIELDS` (line 715):

```python
TRAIT_FIELDS = ("session_length",)
```

And change the `apply_traits_catalog` SELECT (lines 729-730) to stop reading the now-unused `series_role_source`:

```python
    sql = "SELECT id, normalized_title, session_length_source FROM games"
```

- [ ] **Step 4: Run the trait tests to verify they pass**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint**

Run: `uv run ruff check models.py tests/test_game_traits_catalog.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add models.py tests/test_game_traits_catalog.py
git commit -m "$(cat <<'EOF'
refactor(traits): series catalog owns series_role; trait catalog = session_length only

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Integration — migrate_db, on-add hook, UI source stamping

**Files:**
- Modify: `models.py` (`migrate_db` ~lines 803-811)
- Modify: `app.py` (on-add ~line 236; series write paths: from-group ~1033, add-to-series ~1494, delete-series ~1059, remove-from-series ~1535)
- Modify: `tests/test_series_catalog.py`

- [ ] **Step 1: Wire the migration, backfill, and apply into `migrate_db`**

In `models.py` `migrate_db`, the block currently reads (lines 808-811):

```python
    migrate_game_traits(conn)
    migrate_collection_name(conn)
    apply_traits_catalog(conn)
    seed_default_slots(conn)
```

Change to:

```python
    migrate_game_traits(conn)
    migrate_collection_name(conn)
    migrate_series_source(conn)
    backfill_series_source(conn)
    apply_traits_catalog(conn)
    apply_series_catalog(conn)
    seed_default_slots(conn)
```

(`migrate_db` deliberately does NOT call `auto_populate_series`; that stays endpoint-triggered. The catalog's authority comes from the fill rule, not call order.)

- [ ] **Step 2: Wire the on-add hook in `app.py`**

In `app.py`, find the on-add call (line 236, inside the add-game flow):

```python
    apply_traits_catalog(conn, game_id)
```

Add immediately after it:

```python
    apply_series_catalog(conn, game_id)
```

Then add `apply_series_catalog` to the `models` import block at the top of `app.py` (the line that already imports `apply_traits_catalog`, line 17):

```python
    reclean_display_titles, DB_PATH, add_series_pattern, apply_traits_catalog,
    apply_series_catalog,
```

(Match the existing import formatting; ensure the name is included in the `from models import (...)` group.)

- [ ] **Step 3: Stamp `series_source='manual'` on the UI membership writes**

In `app.py` `api_series_from_group` (the upsert at lines 1033-1037), change:

```python
            conn.execute(
                "INSERT INTO user_ratings (game_id, series_id, series_order) VALUES (?, ?, ?) "
                "ON CONFLICT(game_id) DO UPDATE SET series_id = excluded.series_id, "
                "series_order = excluded.series_order, updated_at = CURRENT_TIMESTAMP",
                (gid, series_id, order))
```

to:

```python
            conn.execute(
                "INSERT INTO user_ratings (game_id, series_id, series_order, series_source) "
                "VALUES (?, ?, ?, 'manual') "
                "ON CONFLICT(game_id) DO UPDATE SET series_id = excluded.series_id, "
                "series_order = excluded.series_order, series_source = 'manual', "
                "updated_at = CURRENT_TIMESTAMP",
                (gid, series_id, order))
```

In `api_add_game_to_series` (the upsert at lines 1494-1501), change the INSERT to include `series_source` and stamp `'manual'`:

```python
    conn.execute("""
        INSERT INTO user_ratings (game_id, series_id, series_order, series_source)
        VALUES (?, ?, ?, 'manual')
        ON CONFLICT(game_id) DO UPDATE SET
            series_id = excluded.series_id,
            series_order = excluded.series_order,
            series_source = 'manual',
            updated_at = CURRENT_TIMESTAMP
    """, (game_id, series_id, next_order))
```

- [ ] **Step 4: Clear `series_source` on the removal paths**

In `app.py` `api_delete_series` (line 1059), change:

```python
    conn.execute("UPDATE user_ratings SET series_id = NULL, series_order = NULL WHERE series_id = ?", (series_id,))
```

to:

```python
    conn.execute("UPDATE user_ratings SET series_id = NULL, series_order = NULL, "
                 "series_source = NULL WHERE series_id = ?", (series_id,))
```

In `api_remove_game_from_series` (lines 1535-1539), change the UPDATE to also clear `series_source`:

```python
    conn.execute("""
        UPDATE user_ratings
        SET series_id = NULL, series_order = NULL, series_source = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE game_id = ?
    """, (game_id,))
```

- [ ] **Step 5: Write an integration test for the from-group manual stamp**

Append to `tests/test_series_catalog.py`:

```python
def test_from_group_stamps_manual(client, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Some Game")
    conn.close()
    resp = client.post("/api/series/from-group", json={"name": "My Series", "game_ids": [gid]})
    assert resp.status_code == 200
    conn = models.get_db()
    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    conn.close()
    assert src == "manual"
```

- [ ] **Step 6: Run to verify the integration test passes**

Run: `uv run python -m pytest tests/test_series_catalog.py::test_from_group_stamps_manual -v`
Expected: PASS.

- [ ] **Step 7: Run the FULL suite (regression gate)**

Run: `uv run python -m pytest`
Expected: PASS (all prior tests still green; the series-catalog + integration tests included).

- [ ] **Step 8: Lint**

Run: `uv run ruff check models.py app.py tests/test_series_catalog.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add models.py app.py tests/test_series_catalog.py
git commit -m "$(cat <<'EOF'
feat(series): wire apply_series_catalog into migrate_db + on-add; UI stamps manual

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `--apply-series-catalog` CLI flag

**Files:**
- Modify: `import_scraped.py` (`main()` argparse ~line 651; dispatch ~line 689; the `paths` guard ~line 700)
- Modify: `tests/test_series_catalog.py`

`apply_series_catalog` already lives in `models`, so the CLI just calls it. The report items have shape `{series, action, created, assigned}`.

- [ ] **Step 1: Add the argparse flag**

In `import_scraped.py` `main()`, after the `--apply-bundle-catalog` argument (line 651-653), add:

```python
    parser.add_argument("--apply-series-catalog", action="store_true",
                        help="default games into series from series_catalog.json "
                             "(fill-only; never clobbers a manual assignment), then exit")
```

- [ ] **Step 2: Add the dispatch block**

In `import_scraped.py` `main()`, after the `if args.apply_bundle_catalog:` block (ends line 697), add:

```python
    if args.apply_series_catalog:
        report = models.apply_series_catalog(conn, dry_run=args.dry_run)
        for r in report:
            logger.info("%s: %s (%d games)", r["series"], r["action"], r["assigned"])
        logger.info("DRY RUN — no changes written." if args.dry_run
                    else "series-catalog: %d series processed" % len(report))
        conn.close()
        return
```

- [ ] **Step 3: Update the `paths` guard**

In `import_scraped.py` `main()`, change the guard (line 700):

```python
        parser.error("paths are required unless --cleanup-bundles or --apply-bundle-catalog is given")
```

to:

```python
        parser.error("paths are required unless --cleanup-bundles, --apply-bundle-catalog, "
                     "or --apply-series-catalog is given")
```

- [ ] **Step 4: Write a CLI test (dry-run, no live DB)**

Append to `tests/test_series_catalog.py`:

```python
import import_scraped


def test_cli_apply_series_catalog_dry_run(monkeypatch, temp_db):
    conn = models.get_db()
    _add_game(conn, "Mega Man")
    _add_game(conn, "Mega Man 2")
    conn.close()
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man":   {"series": "Mega Man", "order": 1},
        "mega man 2": {"series": "Mega Man", "order": 2},
    })
    # main() calls models.migrate_db() which is safe against the temp DB (DB_PATH is patched).
    import_scraped.main(["--apply-series-catalog", "--dry-run"])
    conn = models.get_db()
    # dry-run wrote nothing:
    assert conn.execute("SELECT COUNT(*) FROM series WHERE name = 'Mega Man'").fetchone()[0] == 0
    conn.close()
```

- [ ] **Step 5: Run the CLI test**

Run: `uv run python -m pytest tests/test_series_catalog.py::test_cli_apply_series_catalog_dry_run -v`
Expected: PASS.

- [ ] **Step 6: Run the FULL suite**

Run: `uv run python -m pytest`
Expected: PASS (all).

- [ ] **Step 7: Lint**

Run: `uv run ruff check import_scraped.py tests/test_series_catalog.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add import_scraped.py tests/test_series_catalog.py
git commit -m "$(cat <<'EOF'
feat(series): --apply-series-catalog CLI flag (dry-run + report)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Controller-run operation (after the code lands — NOT a subagent task)

This is performed by the **controller** (you), not impl subagents, mirroring Spec A. It writes the live `games.db`.

1. **AI-draft Workflow** (Claude Code): classify all ~764 library titles. Embed the title list as a `const` literal in the script body (NEVER via the Workflow `args` channel — it arrives as a string and shreds into char-fragment agents). Guard with `Array.isArray(...)/length`. Each agent (~20 titles) returns verdicts `{normalized_title, series, order, role}` or `standalone`. Match back by `normalized_title`, never a returned id.
2. **Pilot first:** one ~20-game batch; owner reviews verdict quality + the mainline/spinoff judgment before the full run.
3. Owner reviews + corrects; commit `series_catalog.default.json`.
4. **Dry-run on the live DB:** `uv run python import_scraped.py --apply-series-catalog --dry-run` → review the create/join/assign report. Owner OKs.
5. **Back up** `games.db` → `games.db.bak-20260601-pre-series-apply` (PowerShell `Copy-Item`).
6. **Apply:** `uv run python import_scraped.py --apply-series-catalog`. Report results.
7. **UI verification (controller):** stop any running `app.py` (PowerShell `Stop-Process`; `use_reloader=False`), relaunch, confirm series render with the newly defaulted members. Impl subagents never run the app.

---

## Self-Review

**Spec coverage:**
- Per-title catalog `normalized_title → {series, order, role}` + per-user override + loader → Task 1. ✓
- Fill-only `apply_series_catalog` (join-always / create-≥2 / never clobber manual / order / role / dry-run / single-game) → Task 5. ✓
- `series_source` column + reconstruction backfill → Tasks 2, 4. ✓
- `auto_populate_series` stamps `auto`; shared prefix helper → Task 3. ✓
- Series catalog owns `series_role`; trait catalog → `session_length` only → Task 6. ✓
- Integration (migrate_db + on-add + UI manual-stamp + removal-clear) → Task 7. ✓
- CLI `--apply-series-catalog` + dry-run → Task 8. ✓
- Controller-run AI-draft → pilot → dry-run → backup → apply → UI verify → final section. ✓
- No new UI (out of scope per spec). ✓

**Placeholder scan:** none — every code/test step contains complete code and exact commands.

**Type/name consistency:** `match_series_prefix` (Tasks 3, 4), `migrate_series_source` (Tasks 2, 7, conftest), `backfill_series_source` (Tasks 4, 7), `apply_series_catalog(conn, game_id=None, *, dry_run=False)` (Tasks 5, 7, 8), `load_series_catalog` (Tasks 1, 5, 8), `SERIES_ROLE_VALUES` (Task 5), report keys `{series, action, created, assigned}` (Tasks 5, 8) — all consistent. `series_source` values `auto`/`catalog`/`manual` used uniformly. `TRAIT_FIELDS=("session_length",)` (Task 6) consistent with the trait SELECT change.

**Note on dependency ordering:** Task 2 wires `migrate_series_source` into the `temp_db` fixture, so Tasks 4 and 5 (which need the `series_source` column) run against a correctly-migrated temp DB. Task 6's `TRAIT_FIELDS` change comes after Task 5 so role has its new home in the series catalog before the trait catalog stops writing it.
