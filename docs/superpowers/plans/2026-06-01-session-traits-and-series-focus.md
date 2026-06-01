# Session-tolerance + series-role catalog & series-focus slots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the Slate's Quick/Long axis off a per-game **session-tolerance** trait (does a game have clean short stopping points) instead of total play-time, add a parallel **series-role** trait, seed both from a committed catalog (later filled by a Claude-Code pass), and let a slot optionally **focus a series** and route its mainline/spin-off entries to long/short slots.

**Architecture:** Two new catalog-backed columns on `games` (`session_length`, `series_role`, each with a `*_source` of catalog/ai/manual where **manual locks the row**), resolved catalog→manual→AI→null via an idempotent `apply_traits_catalog`. The deterministic ranker in `slots.py` gains a `session_length` hard-filter/boost and a focus-series routing term, and **drops** the directional time-to-beat term and the old genre-based session penalty — but keeps effective time-to-beat as first-class per-candidate data for the UI and the future chat. A nullable `slots.focus_series_id` carries the per-slot focus. Manual selectors appear in the game modal + Add-Game modal; a Focus-series picker appears in the slot ⚙ editor.

**Tech Stack:** Python 3 + sqlite3, Flask, vanilla JS / Jinja / Tailwind, `uv` for env, `pytest` for tests.

---

## Conventions (read once, apply to every task)

- **Run tests:** `uv run python -m pytest` (plain `uv run pytest` fails: `ModuleNotFoundError: models`). Single test: `uv run python -m pytest tests/test_x.py::test_name -v`.
- **Lint gate:** `ruff check` ONLY. **NEVER** `ruff format` (the codebase is hand-aligned).
- **Subagents:** never run `app.py`, never touch the live `games.db`, never `git push`. Use pytest temp DBs (the `temp_db` / `client` fixtures in `tests/conftest.py`) only. The controller restarts the app and verifies UI live.
- **Commit message footer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Process hygiene (controller only):** the running app uses `use_reloader=False`. STOP it (PowerShell `Stop-Process`) before editing `.py`, relaunch to verify. Don't churn `.py` files while the app runs.
- **Genericity:** slots stay fully user-editable; nothing about the owner's persona is hardcoded — it only seeds defaults.

## File Structure

**New files**
- `game_traits.default.json` — committed catalog (starts as `{}`; the seed PR fills it). Keyed by `normalized_title`.
- `tests/test_game_traits_catalog.py` — `load_game_traits` + `migrate_game_traits` + `apply_traits_catalog`.
- `tests/test_focus_series_migration.py` — `slots.focus_series_id` column migration.
- `tests/test_slots_session_length_ranking.py` — Quick/Long behavior off `session_length`.
- `tests/test_slots_focus_series_ranking.py` — focus-series boost + role routing.
- `tests/test_api_games_traits.py` — `PUT`/`GET /api/games/<id>` trait fields.
- `tests/test_api_slots_focus.py` — slot POST/PATCH `focus_series_id`.

**Modified files**
- `models.py` — `GAME_TRAITS_PATH`/`_DEFAULT_PATH`, `load_game_traits`, `migrate_game_traits`, `apply_traits_catalog`, `focus_series_id` in `migrate_slots`, register both in `migrate_db`.
- `tests/conftest.py` — mirror `migrate_game_traits` in the `temp_db` fixture.
- `slots.py` — ranking: remove TTB term + genre session penalty, add `session_length` filter/boost + focus-series routing, surface `time_to_beat_minutes` per candidate, new constants.
- `tests/test_slots_ttb_ranking.py` — rewrite (TTB no longer orders; assert it is surfaced + neutral).
- `tests/test_slots_engine.py` — rewrite `test_short_session_slot_penalizes_long_form` to the new `session_length` rule.
- `app.py` — `PUT /api/games` accepts `session_length`/`series_role` (manual); POST-create applies catalog; slot POST/PATCH accept `focus_series_id`; import `apply_traits_catalog`.
- `templates/base.html` — trait `<select>`s in the game modal + Add-Game modal, `saveTrait` JS, `addNewGame` trait write, reset on open.
- `templates/recommendations.html` — Focus-series `<select>` in the slot ⚙ editor, `_seriesList` load, payload field.
- `.gitignore` — add `game_traits.json` (per-user override).

---

## Task 1: Committed catalog file + `load_game_traits` loader

Mirrors the existing `series_patterns` loader pattern (`models.load_series_patterns`).

**Files:**
- Create: `game_traits.default.json`
- Create: `tests/test_game_traits_catalog.py`
- Modify: `models.py` (add paths + loader near `load_series_patterns`, top of file ~lines 5-17)
- Modify: `.gitignore`

- [ ] **Step 1: Create the committed (empty) catalog file**

`game_traits.default.json`:
```json
{}
```

- [ ] **Step 2: Add `game_traits.json` to `.gitignore`**

Append to `.gitignore` (match the existing `series_patterns.json` entry style — add the line if not already present):
```
game_traits.json
```

- [ ] **Step 3: Write the failing test**

`tests/test_game_traits_catalog.py`:
```python
import json

import models


def test_load_game_traits_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "game_traits.default.json"
    default.write_text(json.dumps({"celeste": {"session_length": "short"}}), encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", default)
    assert models.load_game_traits() == {"celeste": {"session_length": "short"}}


def test_load_game_traits_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "game_traits.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "game_traits.json"
    per_user.write_text(json.dumps({"celeste": {"series_role": "mainline"}}), encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", per_user)
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", tmp_path / "game_traits.default.json")
    assert models.load_game_traits() == {"celeste": {"series_role": "mainline"}}


def test_load_game_traits_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_game_traits() == {}


def test_load_game_traits_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "game_traits.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", bad)
    assert models.load_game_traits() == {}
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -v`
Expected: FAIL with `AttributeError: module 'models' has no attribute 'GAME_TRAITS_PATH'` / `load_game_traits`.

- [ ] **Step 5: Implement the paths + loader**

In `models.py`, directly below the `SERIES_PATTERNS_*` path constants (after line 7) add:
```python
GAME_TRAITS_PATH = Path(__file__).parent / "game_traits.json"                 # per-user (gitignored)
GAME_TRAITS_DEFAULT_PATH = Path(__file__).parent / "game_traits.default.json"  # committed seed
```
Directly below `load_series_patterns` (after its `return {}` at line 17) add:
```python
def load_game_traits() -> dict:
    """Load the normalized_title->traits catalog (per-user file, else committed seed)."""
    path = GAME_TRAITS_PATH if GAME_TRAITS_PATH.exists() else GAME_TRAITS_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Lint**

Run: `ruff check models.py tests/test_game_traits_catalog.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add game_traits.default.json .gitignore models.py tests/test_game_traits_catalog.py
git commit -m "feat(traits): committed game_traits catalog + load_game_traits loader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `migrate_game_traits` — the four trait columns

Mirrors `migrate_game_signals` (idempotent `ALTER TABLE games ADD COLUMN` guarded by `PRAGMA table_info`).

**Files:**
- Modify: `models.py` (new function near `migrate_game_signals` ~line 632; register in `migrate_db` ~line 711)
- Modify: `tests/conftest.py` (mirror in the `temp_db` fixture, ~line 21)
- Modify: `tests/test_game_traits_catalog.py` (add migration tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_game_traits_catalog.py`:
```python
TRAIT_COLUMNS = {
    "session_length", "session_length_source", "series_role", "series_role_source",
}


def test_migrate_game_traits_adds_columns(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert TRAIT_COLUMNS <= cols
    conn.close()


def test_migrate_game_traits_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_game_traits(conn)
    models.migrate_game_traits(conn)  # second run must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert TRAIT_COLUMNS <= cols
    conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py::test_migrate_game_traits_adds_columns -v`
Expected: FAIL — columns absent (the `temp_db` fixture doesn't add them yet) / `migrate_game_traits` undefined.

- [ ] **Step 3: Implement `migrate_game_traits`**

In `models.py`, directly after `migrate_game_signals` (after line 652) add:
```python
def migrate_game_traits(conn: sqlite3.Connection) -> None:
    """Add the session-tolerance + series-role trait columns to games. Idempotent.

    Each trait carries a `*_source` of catalog/ai/manual (manual LOCKS the row against
    catalog re-sync and AI). Values are short/long for session_length and
    mainline/spinoff for series_role; null is always a safe, neutral value.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    additions = [
        ("session_length", "TEXT"),
        ("session_length_source", "TEXT"),
        ("series_role", "TEXT"),
        ("series_role_source", "TEXT"),
    ]
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE games ADD COLUMN {name} {decl}")
    conn.commit()
```

- [ ] **Step 4: Register in `migrate_db`**

In `models.py` `migrate_db`, immediately after the `migrate_game_signals(conn)` line (line 711) add:
```python
    migrate_game_traits(conn)
```
(Leave `seed_default_slots(conn)` after it. `apply_traits_catalog` is added in Task 3.)

- [ ] **Step 5: Mirror in the test fixture**

In `tests/conftest.py`, in the `temp_db` fixture, directly after `models.migrate_game_signals(conn)` (line 21) add:
```python
    models.migrate_game_traits(conn)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -v`
Expected: PASS (all, including the two migration tests).

- [ ] **Step 7: Lint**

Run: `ruff check models.py tests/conftest.py tests/test_game_traits_catalog.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add models.py tests/conftest.py tests/test_game_traits_catalog.py
git commit -m "feat(traits): migrate_game_traits adds session_length + series_role columns

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `apply_traits_catalog` — catalog → games (manual-locked)

Idempotent. Writes catalog values with `source='catalog'`, never overwriting a `manual`-locked row. Accepts an optional `game_id` (None = all games; used on startup and on add).

**Files:**
- Modify: `models.py` (new function after `migrate_game_traits`; register in `migrate_db`)
- Modify: `tests/test_game_traits_catalog.py` (add apply tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_game_traits_catalog.py`:
```python
def _add_game(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def _traits(conn, gid):
    r = conn.execute(
        "SELECT session_length, session_length_source, series_role, series_role_source "
        "FROM games WHERE id = ?", (gid,)).fetchone()
    return dict(r)


def test_apply_traits_catalog_sets_catalog_values(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short", "series_role": "mainline"}})
    models.apply_traits_catalog(conn)
    t = _traits(conn, gid)
    assert t == {"session_length": "short", "session_length_source": "catalog",
                 "series_role": "mainline", "series_role_source": "catalog"}
    conn.close()


def test_apply_traits_catalog_skips_manual(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    conn.execute("UPDATE games SET session_length = 'long', session_length_source = 'manual' "
                 "WHERE id = ?", (gid,))
    conn.commit()
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short", "series_role": "mainline"}})
    models.apply_traits_catalog(conn)
    t = _traits(conn, gid)
    assert t["session_length"] == "long" and t["session_length_source"] == "manual"  # locked, untouched
    assert t["series_role"] == "mainline" and t["series_role_source"] == "catalog"    # unlocked, set
    conn.close()


def test_apply_traits_catalog_absent_is_noop(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_game_traits", lambda: {})
    models.apply_traits_catalog(conn)
    assert _traits(conn, gid) == {"session_length": None, "session_length_source": None,
                                  "series_role": None, "series_role_source": None}
    conn.close()


def test_apply_traits_catalog_single_game(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Celeste")
    b = _add_game(conn, "Hades")
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short"},
                                 "hades": {"session_length": "short"}})
    models.apply_traits_catalog(conn, game_id=a)
    assert _traits(conn, a)["session_length"] == "short"
    assert _traits(conn, b)["session_length"] is None  # other game untouched
    conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -k apply_traits -v`
Expected: FAIL — `apply_traits_catalog` undefined.

- [ ] **Step 3: Implement `apply_traits_catalog`**

In `models.py`, directly after `migrate_game_traits` add:
```python
TRAIT_FIELDS = ("session_length", "series_role")


def apply_traits_catalog(conn: sqlite3.Connection, game_id: int | None = None) -> None:
    """Set catalog trait values on games not already manually locked. Idempotent.

    Resolution: a `manual` source LOCKS the row (skipped here). Otherwise the catalog
    value (keyed by normalized_title) is written with source='catalog', overwriting a
    prior catalog/ai/null value. A missing catalog or absent entry is a safe no-op.
    game_id=None processes every game (startup); a specific id processes one (on add).
    """
    catalog = load_game_traits()
    if not catalog:
        return
    sql = ("SELECT id, normalized_title, session_length_source, series_role_source "
           "FROM games")
    params: tuple = ()
    if game_id is not None:
        sql += " WHERE id = ?"
        params = (game_id,)
    for row in conn.execute(sql, params).fetchall():
        entry = catalog.get(row["normalized_title"])
        if not entry:
            continue
        for trait in TRAIT_FIELDS:
            if row[f"{trait}_source"] == "manual":
                continue  # locked
            value = entry.get(trait)
            if value is None:
                continue
            conn.execute(
                f"UPDATE games SET {trait} = ?, {trait}_source = 'catalog' WHERE id = ?",
                (value, row["id"]))
    conn.commit()
```

- [ ] **Step 4: Register in `migrate_db`**

In `models.py` `migrate_db`, directly after the `migrate_game_traits(conn)` line you added in Task 2, add:
```python
    apply_traits_catalog(conn)
```
Order in `migrate_db` is now: `migrate_game_signals` → `migrate_game_traits` → `apply_traits_catalog` → `seed_default_slots`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_game_traits_catalog.py -v`
Expected: PASS (all).

- [ ] **Step 6: Lint**

Run: `ruff check models.py tests/test_game_traits_catalog.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add models.py tests/test_game_traits_catalog.py
git commit -m "feat(traits): apply_traits_catalog writes catalog values, locks manual rows

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `slots.focus_series_id` column

Extend the existing idempotent ALTER-guard pattern in `migrate_slots`.

**Files:**
- Modify: `models.py` `migrate_slots` (~lines 580-586)
- Create: `tests/test_focus_series_migration.py`

- [ ] **Step 1: Write the failing test**

`tests/test_focus_series_migration.py`:
```python
import models


def test_migrate_slots_adds_focus_series_id(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()}
    assert "focus_series_id" in cols
    conn.close()


def test_migrate_slots_focus_series_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_slots(conn)
    models.migrate_slots(conn)  # must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()}
    assert "focus_series_id" in cols
    conn.close()


def test_focus_series_id_defaults_null_and_accepts_value(temp_db):
    conn = models.get_db()
    conn.execute("INSERT INTO series (name) VALUES ('Zelda')")
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO slots (label, focus_series_id) VALUES ('S', ?)", (sid,))
    conn.execute("INSERT INTO slots (label) VALUES ('T')")
    conn.commit()
    rows = {r["label"]: r["focus_series_id"]
            for r in conn.execute("SELECT label, focus_series_id FROM slots").fetchall()}
    assert rows["S"] == sid
    assert rows["T"] is None
    conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_focus_series_migration.py -v`
Expected: FAIL — `focus_series_id` column absent.

- [ ] **Step 3: Add the column guard to `migrate_slots`**

In `models.py` `migrate_slots`, directly before the final `conn.commit()` (line 586) add:
```python
    cols = [c[1] for c in conn.execute("PRAGMA table_info(slots)").fetchall()]
    if "focus_series_id" not in cols:
        conn.execute(
            "ALTER TABLE slots ADD COLUMN focus_series_id INTEGER "
            "REFERENCES series(id) ON DELETE SET NULL")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_focus_series_migration.py -v`
Expected: PASS (3 tests). (`temp_db` already calls `migrate_slots`, so the column appears.)

- [ ] **Step 5: Lint**

Run: `ruff check models.py tests/test_focus_series_migration.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add models.py tests/test_focus_series_migration.py
git commit -m "feat(slots): focus_series_id column on slots (FK series, SET NULL)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Ranking — `session_length` filter/boost; drop TTB term + genre penalty; surface time-to-beat

This is the core ranking change. **Remove** the directional time-to-beat term (`TTB_REFERENCE_MINUTES`/`TTB_WEIGHT`/`TTB_TERM_CAP`) and the genre-based `SESSION_MISMATCH_PENALTY`/`session_tolerant` usage. **Add** a `session_length` hard-filter/boost. **Surface** `effective_time_to_beat_minutes` as `candidate["time_to_beat_minutes"]` (data retained for the UI + future chat — only demoted from the slot axis). Two existing tests assert the old behavior and are rewritten here.

**Why drop the genre penalty (not keep it as a fallback):** spec decision #4 says `null` session_length is **neutral**. A genre-based fallback would re-penalize null long-form-genre games in Quick — exactly the bug this feature fixes (Advance Wars/Phoenix Wright are long-form genres but short-session). The catalog/seed fills the library, so `null` is transient.

**Files:**
- Modify: `slots.py` (constants ~lines 20-25; `rank_candidates` body ~lines 80-169)
- Create: `tests/test_slots_session_length_ranking.py`
- Rewrite: `tests/test_slots_ttb_ranking.py`
- Rewrite: `tests/test_slots_engine.py::test_short_session_slot_penalizes_long_form`

- [ ] **Step 1: Write the new failing test**

`tests/test_slots_session_length_ranking.py`:
```python
import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_game(conn, title, session_length=None, priority=5):
    conn.execute(
        "INSERT INTO games (title, normalized_title, session_length, session_length_source) "
        "VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(title), session_length,
         "catalog" if session_length else None))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, 'backlog', ?)",
                 (gid, priority))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _ids(conn, label):
    return [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_quick_slot_excludes_long_session_games(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short One", session_length="short")
    long_ = _add_game(conn, "Long One", session_length="long")
    ids = _ids(conn, "Switch · Quick")          # max_session_minutes = 60
    assert short in ids
    assert long_ not in ids                      # clean split: long lives only in Long
    conn.close()


def test_quick_slot_boosts_short_above_null(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short One", session_length="short")
    neutral = _add_game(conn, "Neutral One", session_length=None)
    ids = _ids(conn, "Switch · Quick")
    assert ids.index(short) < ids.index(neutral)
    conn.close()


def test_long_slot_boosts_long_and_allows_short(temp_db):
    conn = models.get_db()
    long_ = _add_game(conn, "Long One", session_length="long")
    short = _add_game(conn, "Short One", session_length="short")
    ids = _ids(conn, "Switch · Long")            # min_session_minutes = 60
    assert long_ in ids and short in ids         # short still allowed in Long
    assert ids.index(long_) < ids.index(short)   # long boosted above
    conn.close()


def test_null_session_length_neutral_in_quick(temp_db):
    conn = models.get_db()
    a = _add_game(conn, "A", session_length=None)
    b = _add_game(conn, "B", session_length=None)
    ids = _ids(conn, "Switch · Quick")
    assert a in ids and b in ids                 # null never excluded from Quick
    conn.close()


def test_candidate_dict_includes_time_to_beat(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Timed", session_length="short")
    conn.execute("UPDATE games SET hltb_main_minutes = 600 WHERE id = ?", (gid,))
    conn.commit()
    cand = next(c for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
                if c["game"]["id"] == gid)
    assert cand["time_to_beat_minutes"] == 600   # retained as first-class data
    conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_slots_session_length_ranking.py -v`
Expected: FAIL — `long` games not excluded from Quick; no `time_to_beat_minutes` key.

- [ ] **Step 3: Update the ranking constants**

In `slots.py`, replace the constants block (lines 20-25):
```python
SESSION_MISMATCH_PENALTY = 25.0
STARTED_BOOST = 1000.0
TTB_REFERENCE_MINUTES = 1200   # 20h: pivot between "short" and "long"
TTB_WEIGHT = 0.02              # score points per minute of deviation
TTB_TERM_CAP = 20.0
SERIES_BOOST = 30.0
```
with:
```python
STARTED_BOOST = 1000.0
SERIES_BOOST = 30.0            # recent-series auto-boost (slot's last play)
SESSION_FIT_BOOST = 25.0      # session_length matches the slot's session window
FOCUS_SERIES_BOOST = 30.0     # candidate is in the slot's focus_series_id
ROLE_BOOST = 20.0             # mainline->long slot / spinoff->short slot routing
```

- [ ] **Step 4: Drop the `session_tolerant` import**

In `slots.py` line 13, change:
```python
from slot_signals import session_tolerant, latency_tolerant, effective_time_to_beat_minutes
```
to:
```python
from slot_signals import latency_tolerant, effective_time_to_beat_minutes
```
(`session_tolerant` stays defined in `slot_signals.py` for its own unit tests; it is no longer used by the ranker.)

- [ ] **Step 5: Replace the session/TTB scoring block**

In `slots.py` `rank_candidates`, replace this block (lines 138-156, from the `# Session-tolerance penalty` comment through the end of the directional time-to-beat `elif min_session is not None:` clause):
```python
        # Session-tolerance penalty for short-session slots
        max_session = slot.get("max_session_minutes")
        min_session = slot.get("min_session_minutes")
        if max_session is not None and not session_tolerant(tag_names):
            score -= SESSION_MISMATCH_PENALTY
            reasons.append("May not suit a short session")
        # Directional time-to-beat term
        ttb = effective_time_to_beat_minutes(game)
        if ttb is not None:
            if max_session is not None:
                term = max(-TTB_TERM_CAP, min(TTB_TERM_CAP, (TTB_REFERENCE_MINUTES - ttb) * TTB_WEIGHT))
                if term:
                    score += term
                    reasons.append("Short play" if term > 0 else "Long for a quick session")
            elif min_session is not None:
                term = max(-TTB_TERM_CAP, min(TTB_TERM_CAP, (ttb - TTB_REFERENCE_MINUTES) * TTB_WEIGHT))
                if term:
                    score += term
                    reasons.append("Meaty play" if term > 0 else "Short for a long session")
```
with:
```python
        # Session-tolerance fit, keyed off the per-game session_length trait
        # (catalog/ai/manual). null is always neutral; only 'long' is excluded from
        # a short-session (Quick) slot.
        max_session = slot.get("max_session_minutes")
        min_session = slot.get("min_session_minutes")
        session_length = game["session_length"]
        if max_session is not None:
            if session_length == "long":
                continue                       # clean split: long games live only in Long slots
            if session_length == "short":
                score += SESSION_FIT_BOOST
                reasons.append("Fits a quick session")
        elif min_session is not None:
            if session_length == "long":
                score += SESSION_FIT_BOOST
                reasons.append("Worth a long sitting")
```

- [ ] **Step 6: Surface the effective time-to-beat in the candidate dict**

In `slots.py` `rank_candidates`, replace the append line (line 166):
```python
        out.append({"game": dict(game), "score": round(score, 1), "reasons": reasons})
```
with:
```python
        out.append({"game": dict(game), "score": round(score, 1), "reasons": reasons,
                    "time_to_beat_minutes": effective_time_to_beat_minutes(game)})
```

- [ ] **Step 7: Update the module docstring**

In `slots.py`, change the docstring lines 4-5:
```python
slot-specific hard filters (platform, latency) + a session/length fit nudge +
a genre-fatigue penalty from recent slot_history.
```
to:
```python
slot-specific hard filters (platform, latency, session_length) + a session-fit and
focus-series boost + a genre-fatigue penalty from recent slot_history. Effective
time-to-beat is surfaced per candidate (data for the UI + chat) but no longer drives
the Quick/Long axis.
```

- [ ] **Step 8: Run the new test to verify it passes**

Run: `uv run python -m pytest tests/test_slots_session_length_ranking.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Rewrite the obsolete TTB ranking test**

Replace the entire contents of `tests/test_slots_ttb_ranking.py` with:
```python
"""Time-to-beat is retained as first-class candidate data but no longer orders the
Quick/Long axis (that is now session_length). See the session-traits spec."""
import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_game(conn, title, hltb_main, priority=5):
    conn.execute("INSERT INTO games (title, normalized_title, hltb_main_minutes) VALUES (?, ?, ?)",
                 (title, models.normalize_title(title), hltb_main))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, 'backlog', ?)",
                 (gid, priority))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def test_time_to_beat_surfaced_per_candidate(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Timed Game", hltb_main=900)
    cand = next(c for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
                if c["game"]["id"] == gid)
    assert cand["time_to_beat_minutes"] == 900
    conn.close()


def test_time_to_beat_does_not_order_quick_slot(temp_db):
    conn = models.get_db()
    # Two null-session_length games differing only in HLTB length both qualify and
    # neither is excluded — TTB is no longer the slot axis.
    short = _add_game(conn, "Short Game", hltb_main=300)
    long_ = _add_game(conn, "Long Game", hltb_main=2400)
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))]
    assert short in ids and long_ in ids
    conn.close()


def test_unknown_ttb_is_neutral(temp_db):
    conn = models.get_db()
    a = _add_game(conn, "A", hltb_main=None)
    b = _add_game(conn, "B", hltb_main=None)
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))]
    assert a in ids and b in ids
    conn.close()
```

- [ ] **Step 10: Rewrite the genre-penalty test in `test_slots_engine.py`**

In `tests/test_slots_engine.py`, replace `test_short_session_slot_penalizes_long_form` (lines 89-98) with:
```python
def test_short_session_slot_excludes_long_session_length(temp_db):
    conn = models.get_db()
    # "Switch · Quick" has max_session_minutes=60. The Quick/Long split now keys off
    # the per-game session_length trait, not genre tags: a 'long' game is excluded,
    # a 'short' game qualifies.
    short = _add_game(conn, "Short One", "Switch", tags=("Puzzle",))
    long_ = _add_game(conn, "Long One", "Switch", tags=("Puzzle",))
    conn.execute("UPDATE games SET session_length='short', session_length_source='catalog' "
                 "WHERE id=?", (short,))
    conn.execute("UPDATE games SET session_length='long', session_length_source='catalog' "
                 "WHERE id=?", (long_,))
    conn.commit()
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))]
    assert short in ids
    assert long_ not in ids
    conn.close()
```

- [ ] **Step 11: Run the full slots test suite to verify it passes**

Run: `uv run python -m pytest tests/test_slots_session_length_ranking.py tests/test_slots_ttb_ranking.py tests/test_slots_engine.py tests/test_slots_series_ranking.py tests/test_slot_dismissals_engine.py -v`
Expected: PASS (all).

- [ ] **Step 12: Lint**

Run: `ruff check slots.py tests/test_slots_session_length_ranking.py tests/test_slots_ttb_ranking.py tests/test_slots_engine.py`
Expected: no errors.

- [ ] **Step 13: Commit**

```bash
git add slots.py tests/test_slots_session_length_ranking.py tests/test_slots_ttb_ranking.py tests/test_slots_engine.py
git commit -m "feat(slots): session_length drives Quick/Long; TTB demoted to surfaced data

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Ranking — focus-series boost + role routing

When `slot['focus_series_id']` is set: boost candidates in that series, then route by `series_role` — short-session slot favors `spinoff`, long-session slot favors `mainline`. The platform hard-filter already applies.

**Files:**
- Modify: `slots.py` `rank_candidates` (after the session-fit block; before the `prioritize_started` block)
- Create: `tests/test_slots_focus_series_ranking.py`

- [ ] **Step 1: Write the failing test**

`tests/test_slots_focus_series_ranking.py`:
```python
import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_game(conn, title, platform="Switch", series_id=None, series_role=None):
    conn.execute(
        "INSERT INTO games (title, normalized_title, series_role, series_role_source) "
        "VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(title), series_role,
         "catalog" if series_role else None))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform)))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority, series_id) "
                 "VALUES (?, 'backlog', 5, ?)", (gid, series_id))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _set_focus(conn, label, series_id):
    conn.execute("UPDATE slots SET focus_series_id=? WHERE label=?", (series_id, label))
    conn.commit()


def _ids(conn, label):
    return [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_focus_series_boosts_series_games(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    in_series = _add_game(conn, "Zelda BotW", series_id=sid)
    other = _add_game(conn, "Some Other Game")
    _set_focus(conn, "Switch · Quick", sid)
    ids = _ids(conn, "Switch · Quick")
    assert ids.index(in_series) < ids.index(other)
    conn.close()


def test_long_slot_routes_mainline_above_spinoff(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    mainline = _add_game(conn, "Zelda Mainline", series_id=sid, series_role="mainline")
    spinoff = _add_game(conn, "Zelda Spinoff", series_id=sid, series_role="spinoff")
    _set_focus(conn, "Switch · Long", sid)       # min_session_minutes set -> long slot
    ids = _ids(conn, "Switch · Long")
    assert ids.index(mainline) < ids.index(spinoff)
    conn.close()


def test_short_slot_routes_spinoff_above_mainline(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    mainline = _add_game(conn, "Zelda Mainline", series_id=sid, series_role="mainline")
    spinoff = _add_game(conn, "Zelda Spinoff", series_id=sid, series_role="spinoff")
    _set_focus(conn, "Switch · Quick", sid)      # max_session_minutes set -> short slot
    ids = _ids(conn, "Switch · Quick")
    assert ids.index(spinoff) < ids.index(mainline)
    conn.close()


def test_focus_series_respects_platform_filter(temp_db):
    conn = models.get_db()
    sid = _series(conn, "Zelda")
    off_platform = _add_game(conn, "Zelda PS", platform="PS", series_id=sid, series_role="mainline")
    _set_focus(conn, "Switch · Long", sid)       # Switch-only slot
    ids = _ids(conn, "Switch · Long")
    assert off_platform not in ids               # platform hard-filter still wins
    conn.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_slots_focus_series_ranking.py -v`
Expected: FAIL — focus games not boosted/routed.

- [ ] **Step 3: Add the focus-series routing block**

In `slots.py` `rank_candidates`, directly after the session-fit block added in Task 5 (after the `elif min_session is not None:` / "Worth a long sitting" lines) and before the `# Boost in-progress games` comment, add:
```python
        # Focus-series boost + role routing (when the slot focuses a series).
        focus_series_id = slot.get("focus_series_id")
        if focus_series_id is not None and game["series_id"] == focus_series_id:
            score += FOCUS_SERIES_BOOST
            reasons.append("In this slot's focus series")
            role = game["series_role"]
            if max_session is not None and role == "spinoff":
                score += ROLE_BOOST
                reasons.append("Spin-off suits a short slot")
            elif min_session is not None and role == "mainline":
                score += ROLE_BOOST
                reasons.append("Mainline for a long sitting")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_slots_focus_series_ranking.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint**

Run: `ruff check slots.py tests/test_slots_focus_series_ranking.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add slots.py tests/test_slots_focus_series_ranking.py
git commit -m "feat(slots): focus-series boost + mainline/spinoff role routing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: API — `PUT /api/games/<id>` manual traits; `GET` returns them; catalog-on-add

`PUT` accepts `session_length`/`series_role`; writing a valid value sets it + `*_source='manual'`; empty/null clears both. `GET /api/games/<id>` already returns the columns via `SELECT g.*` (assert it). The create path applies the catalog to the new game.

**Files:**
- Modify: `app.py` import (line 15-18), `api_update_game` (~lines 632-657), `api_create_game` (~lines 213-237)
- Create: `tests/test_api_games_traits.py`

- [ ] **Step 1: Write the failing test**

`tests/test_api_games_traits.py`:
```python
import json

import models


def _make_game(client, title="Test Game"):
    resp = client.post("/api/games", json={"title": title})
    return resp.get_json()["game_id"]


def test_put_sets_session_length_manual(client):
    gid = _make_game(client)
    r = client.put(f"/api/games/{gid}", json={"session_length": "short"})
    assert r.status_code == 200
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] == "short"
    assert g["session_length_source"] == "manual"


def test_put_sets_series_role_manual(client):
    gid = _make_game(client)
    client.put(f"/api/games/{gid}", json={"series_role": "spinoff"})
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["series_role"] == "spinoff"
    assert g["series_role_source"] == "manual"


def test_put_clears_trait_on_empty(client):
    gid = _make_game(client)
    client.put(f"/api/games/{gid}", json={"session_length": "long"})
    client.put(f"/api/games/{gid}", json={"session_length": ""})
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] is None
    assert g["session_length_source"] is None


def test_put_ignores_invalid_trait_value(client):
    gid = _make_game(client)
    client.put(f"/api/games/{gid}", json={"session_length": "bogus"})
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] is None  # invalid enum ignored, stays null


def test_get_returns_trait_fields(client):
    gid = _make_game(client)
    g = client.get(f"/api/games/{gid}").get_json()
    for key in ("session_length", "session_length_source", "series_role", "series_role_source"):
        assert key in g


def test_create_applies_catalog(monkeypatch, client):
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short"}})
    gid = _make_game(client, title="Celeste")
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["session_length"] == "short"
    assert g["session_length_source"] == "catalog"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games_traits.py -v`
Expected: FAIL — traits not persisted; catalog not applied on create.

- [ ] **Step 3: Import `apply_traits_catalog` in `app.py`**

In `app.py`, extend the `from models import (...)` block (lines 15-18) to include `apply_traits_catalog`:
```python
from models import (
    get_db, init_db, migrate_db, normalize_title, clean_title,
    reclean_display_titles, DB_PATH, add_series_pattern, apply_traits_catalog,
)
```

- [ ] **Step 4: Add the trait enum + PUT handling**

In `app.py`, directly above `def api_update_game` (line 632) add a module constant:
```python
TRAIT_ENUMS = {
    "session_length": {"short", "long"},
    "series_role": {"mainline", "spinoff"},
}
```
In `api_update_game`, change the games-table guard (line 640) from:
```python
        if 'title' in data or 'cover_url' in data or 'time_to_beat_override_minutes' in data:
```
to:
```python
        if ('title' in data or 'cover_url' in data or 'time_to_beat_override_minutes' in data
                or 'session_length' in data or 'series_role' in data):
```
Then, inside that block, directly after the `time_to_beat_override_minutes` handling (after line 654) and before the `game_updates.append("updated_at = CURRENT_TIMESTAMP")` line, add:
```python
            for trait in ('session_length', 'series_role'):
                if trait in data:
                    v = data[trait]
                    if v in (None, ""):
                        game_updates.append(f"{trait} = NULL")
                        game_updates.append(f"{trait}_source = NULL")
                    elif v in TRAIT_ENUMS[trait]:
                        game_updates.append(f"{trait} = ?")
                        game_params.append(v)
                        game_updates.append(f"{trait}_source = 'manual'")
                    # invalid enum value: ignored
```

- [ ] **Step 5: Apply the catalog on create**

In `app.py` `api_create_game`, directly after the platforms loop's `conn.commit()` (line 235) — i.e. after platforms are inserted and committed, before `conn.close()` — add:
```python
    apply_traits_catalog(conn, game_id)
```
(`apply_traits_catalog` commits internally; the subsequent `conn.close()` stays.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games_traits.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Lint**

Run: `ruff check app.py tests/test_api_games_traits.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_api_games_traits.py
git commit -m "feat(api): PUT/GET game traits (manual lock) + catalog-on-create

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: API — slot POST/PATCH accept `focus_series_id`

**Files:**
- Modify: `app.py` `api_create_slot` (~lines 1558-1576), `api_update_slot` (~lines 1579-1604)
- Create: `tests/test_api_slots_focus.py`

- [ ] **Step 1: Write the failing test**

`tests/test_api_slots_focus.py`:
```python
import models


def _series(client, name="Zelda"):
    conn = models.get_db()
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return sid


def _slot_focus(label):
    conn = models.get_db()
    row = conn.execute("SELECT focus_series_id FROM slots WHERE label=?", (label,)).fetchone()
    conn.close()
    return row["focus_series_id"] if row else None


def test_create_slot_persists_focus_series_id(client):
    sid = _series(client)
    client.post("/api/slots", json={"label": "Focus Slot", "platforms": [], "focus_series_id": sid})
    assert _slot_focus("Focus Slot") == sid


def test_patch_slot_sets_and_clears_focus_series_id(client):
    sid = _series(client)
    # The seed slot "Switch · Quick" exists from seed_default_slots.
    conn = models.get_db()
    slot_id = conn.execute("SELECT id FROM slots WHERE label='Switch · Quick'").fetchone()["id"]
    conn.close()
    client.patch(f"/api/slots/{slot_id}", json={"focus_series_id": sid})
    assert _slot_focus("Switch · Quick") == sid
    client.patch(f"/api/slots/{slot_id}", json={"focus_series_id": None})
    assert _slot_focus("Switch · Quick") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_api_slots_focus.py -v`
Expected: FAIL — `focus_series_id` not persisted.

- [ ] **Step 3: Add `focus_series_id` to slot create**

In `app.py` `api_create_slot`, change the INSERT (lines 1564-1573) to include the column:
```python
    conn.execute(
        "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
        "min_session_minutes, streamable_only, prioritize_started, context_notes, "
        "focus_series_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data.get('label', 'New slot'), next_order,
         json.dumps(data.get('platforms', [])),
         data.get('max_session_minutes'), data.get('min_session_minutes'),
         1 if data.get('streamable_only') else 0,
         1 if data.get('prioritize_started', 1) else 0,
         data.get('context_notes'), data.get('focus_series_id')))
```

- [ ] **Step 4: Add `focus_series_id` to slot patch**

In `app.py` `api_update_slot`, change the simple-fields tuple (line 1584) from:
```python
    for key in ('label', 'max_session_minutes', 'min_session_minutes', 'context_notes', 'sort_order'):
```
to:
```python
    for key in ('label', 'max_session_minutes', 'min_session_minutes', 'context_notes',
                'sort_order', 'focus_series_id'):
```
(The loop appends `data[key]` directly, so an `int` or `None` both persist correctly.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_api_slots_focus.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Lint**

Run: `ruff check app.py tests/test_api_slots_focus.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_api_slots_focus.py
git commit -m "feat(api): slot create/patch accept focus_series_id

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: UI — trait selectors in the game modal

Two `<select>`s in the game-detail modal, seeded from the game, writing manual on change. The source is shown subtly. This is a render-smoke + controller-live-verify task (no DB writes from the subagent).

**Files:**
- Modify: `templates/base.html` — the `loadGameModal` template (after the Hours-to-beat block ~line 970) + a `saveTrait` JS helper (near `saveTimeToBeat` ~line 766)
- Modify: `tests/test_recommendations_render.py` OR add a new render test (see Step 1)

- [ ] **Step 1: Write a render-smoke test**

Add to `tests/test_api_games_traits.py` (it already has the `client` fixture and creates games):
```python
def test_game_modal_template_has_trait_selectors():
    with open("templates/base.html", encoding="utf-8") as f:
        html = f.read()
    assert "saveTrait(" in html
    assert "'session_length'" in html
    assert "'series_role'" in html
    assert "Session length" in html
    assert "Series role" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games_traits.py::test_game_modal_template_has_trait_selectors -v`
Expected: FAIL — markers absent.

- [ ] **Step 3: Add the `saveTrait` JS helper**

In `templates/base.html`, directly after the `saveTimeToBeat` function (after line 769) add:
```javascript
        async function saveTrait(gameId, field, value) {
            await api.put(`/api/games/${gameId}`, { [field]: value === '' ? null : value });
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

- [ ] **Step 4: Add the two selects to the modal template**

In `templates/base.html`, directly after the Hours-to-beat block's closing `</div>` (after line 970) insert:
```html
                        <!-- Session length + Series role (manual override; locks the row) -->
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-400 mb-1">
                                    Session length
                                    <span class="text-xs text-gray-500">${game.session_length_source ? '(' + game.session_length_source + ')' : ''}</span>
                                </label>
                                <select onchange="saveTrait(${game.id}, 'session_length', this.value)"
                                        class="w-full bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                                    <option value="" ${!game.session_length ? 'selected' : ''}>—</option>
                                    <option value="short" ${game.session_length === 'short' ? 'selected' : ''}>Short sessions</option>
                                    <option value="long" ${game.session_length === 'long' ? 'selected' : ''}>Long sessions</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-400 mb-1">
                                    Series role
                                    <span class="text-xs text-gray-500">${game.series_role_source ? '(' + game.series_role_source + ')' : ''}</span>
                                </label>
                                <select onchange="saveTrait(${game.id}, 'series_role', this.value)"
                                        class="w-full bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                                    <option value="" ${!game.series_role ? 'selected' : ''}>—</option>
                                    <option value="mainline" ${game.series_role === 'mainline' ? 'selected' : ''}>Mainline</option>
                                    <option value="spinoff" ${game.series_role === 'spinoff' ? 'selected' : ''}>Spin-off</option>
                                </select>
                            </div>
                        </div>
```

- [ ] **Step 5: Run the render test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games_traits.py::test_game_modal_template_has_trait_selectors -v`
Expected: PASS.

- [ ] **Step 6: Lint (no Python changed, but run the broad gate the controller uses)**

Run: `ruff check tests/test_api_games_traits.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add templates/base.html tests/test_api_games_traits.py
git commit -m "feat(ui): session-length + series-role selectors in the game modal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: CONTROLLER live-verify**

Controller stops the running app, relaunches, opens a game modal at `/`, confirms the two selects render with the current value + source label, changes one, and confirms the value persists on reopen (and that `refreshGameList` re-ranks). Subagents do NOT do this.

---

## Task 10: UI — trait selectors in the Add-Game modal

Two selects defaulting "—". On create, a chosen value is saved manual via a follow-up `PUT` (reuses the Task 7 manual path; no POST change). Reset on open.

**Files:**
- Modify: `templates/base.html` — Add-Game modal markup (after the Platforms block ~line 157), `addNewGame` (~line 1685), and the modal reset (~line 1272)

- [ ] **Step 1: Write a render-smoke test**

Add to `tests/test_api_games_traits.py`:
```python
def test_add_game_modal_has_trait_selectors():
    with open("templates/base.html", encoding="utf-8") as f:
        html = f.read()
    assert "new-game-session-length" in html
    assert "new-game-series-role" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_api_games_traits.py::test_add_game_modal_has_trait_selectors -v`
Expected: FAIL — ids absent.

- [ ] **Step 3: Add the two selects to the Add-Game modal**

In `templates/base.html`, directly after the Platforms `<div>` block (after line 157, before `<div id="add-game-error"...>`) insert:
```html
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-400 mb-2">Session length</label>
                            <select id="new-game-session-length"
                                    class="w-full bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                                <option value="">—</option>
                                <option value="short">Short sessions</option>
                                <option value="long">Long sessions</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-400 mb-2">Series role</label>
                            <select id="new-game-series-role"
                                    class="w-full bg-surface rounded-lg border border-gray-600 px-3 py-2 text-white text-sm focus:border-accent focus:outline-none">
                                <option value="">—</option>
                                <option value="mainline">Mainline</option>
                                <option value="spinoff">Spin-off</option>
                            </select>
                        </div>
                    </div>
```

- [ ] **Step 4: Write the chosen traits on create**

In `templates/base.html` `addNewGame`, directly after `const gameId = result.data.game_id;` (line 1685) add:
```javascript
            // Persist any manually chosen traits (reuses the manual PUT path).
            const sl = document.getElementById('new-game-session-length').value;
            const sr = document.getElementById('new-game-series-role').value;
            if (sl || sr) {
                const traitPayload = {};
                if (sl) traitPayload.session_length = sl;
                if (sr) traitPayload.series_role = sr;
                await api.put(`/api/games/${gameId}`, traitPayload);
            }
```

- [ ] **Step 5: Reset the selects when the modal opens/closes**

In `templates/base.html`, find the Add-Game reset that clears `new-game-cover-url` (line 1272) and add alongside it:
```javascript
            document.getElementById('new-game-session-length').value = '';
            document.getElementById('new-game-series-role').value = '';
```

- [ ] **Step 6: Run the render test to verify it passes**

Run: `uv run python -m pytest tests/test_api_games_traits.py::test_add_game_modal_has_trait_selectors -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add templates/base.html tests/test_api_games_traits.py
git commit -m "feat(ui): Add-Game modal trait selectors (manual on create)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: CONTROLLER live-verify**

Controller relaunches the app, adds a game with a chosen session length + series role, confirms the modal shows them as `(manual)` afterward.

---

## Task 11: UI — Focus-series picker in the slot ⚙ editor

A "Focus series" `<select>` (— / each series) in the slot settings editor, bound to `focus_series_id`, saved via the existing slot PATCH payload. Series list is loaded once in `loadSlate`.

**Files:**
- Modify: `templates/recommendations.html` — `loadSlate` (~line 30), a `_seriesList` global (~line 27), `toggleSlotSettings` (~line 221), `_slotSettingsPayload` (~line 237)

- [ ] **Step 1: Write a render-smoke test**

Add to `tests/test_recommendations_render.py` (or create it if a render assertion file is cleaner — check the existing file first and append):
```python
def test_slot_settings_has_focus_series_picker():
    with open("templates/recommendations.html", encoding="utf-8") as f:
        html = f.read()
    assert "focus_series_id" in html
    assert "Focus series" in html
    assert "/api/series" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_recommendations_render.py::test_slot_settings_has_focus_series_picker -v`
Expected: FAIL — markers absent.

- [ ] **Step 3: Load the series list in `loadSlate`**

In `templates/recommendations.html`, add a module global next to `let _slateData = [];` (line 27):
```javascript
    let _seriesList = [];
```
Change `loadSlate` (lines 31-35) to also fetch series:
```javascript
        const [slotsResp, games, series] = await Promise.all([
            api.get('/api/slots'),
            api.get('/api/games'),
            api.get('/api/series'),
        ]);
        _slateData = slotsResp.slots || [];
        _seriesList = (series || []).map(s => ({ id: s.id, name: s.name }));
```

- [ ] **Step 4: Add the Focus-series select to `toggleSlotSettings`**

In `templates/recommendations.html` `toggleSlotSettings`, directly before the `<textarea data-f="context_notes" ...>` line (line 221) insert this into the template literal:
```javascript
                <label class="block text-xs text-gray-400">Focus series
                    <select data-f="focus_series_id" class="mt-1 w-full bg-surface rounded border border-gray-600 px-2 py-1 text-white text-xs">
                        <option value="">— none —</option>
                        ${_seriesList.map(s => `<option value="${s.id}" ${slot.focus_series_id === s.id ? 'selected' : ''}>${escapeHtml(s.name)}</option>`).join('')}
                    </select>
                </label>
```

- [ ] **Step 5: Add `focus_series_id` to the patch payload**

In `templates/recommendations.html` `_slotSettingsPayload` (lines 237-245), add inside the returned object (after `context_notes: val('context_notes').value,`):
```javascript
            focus_series_id: (() => { const v = val('focus_series_id').value; return v === '' ? null : parseInt(v, 10); })(),
```

- [ ] **Step 6: Run the render test to verify it passes**

Run: `uv run python -m pytest tests/test_recommendations_render.py::test_slot_settings_has_focus_series_picker -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add templates/recommendations.html tests/test_recommendations_render.py
git commit -m "feat(ui): Focus-series picker in the slot settings editor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: CONTROLLER live-verify**

Controller relaunches the app, opens a slot's ⚙ editor, sets a Focus series, saves, confirms the slot's candidate list now surfaces that series (and role routing if the games are classified). Subagents do NOT do this.

---

## Task 12: Full-suite green + ruff gate (integration checkpoint)

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `uv run python -m pytest`
Expected: PASS (the prior 425 + the new tests). Investigate any red with superpowers:systematic-debugging before proceeding.

- [ ] **Step 2: Lint the whole tree**

Run: `ruff check .`
Expected: no errors. (NEVER `ruff format`.)

- [ ] **Step 3: Controller confirms the live app**

Controller relaunches the app and spot-checks `/recommendations`: Quick slots no longer list `long`-session games, Long slots surface them, time-to-beat still shows in the modal, and a focused slot routes by role.

---

## The Claude-Code classification seed (CONTROLLER-run operation — NOT a subagent TDD task)

Run **after** Tasks 1-8 land (schema + `apply_traits_catalog` + the manual PUT path exist). This is a Workflow the **controller** runs; the agents classify and RETURN verdicts, and **the controller writes `games.db`** (`source='ai'`, only where the row is not already `manual`/`catalog`) and emits/merges `game_traits.default.json` for the owner's PR. Agents NEVER touch `games.db` or run the app (see [[subagent-impl-never-touch-live-db]]).

**Pre-flight (controller):**
- Stop the running app. Back up the DB: copy `games.db` to `games.db.bak-YYYYMMDD-pre-traits-seed`.
- Build the work-list: every game lacking a non-null catalog/manual value for *either* trait —
  `SELECT g.id, g.title, g.normalized_title, g.session_length, g.session_length_source, g.series_role, g.series_role_source FROM games g`, then in Python keep rows where `session_length_source not in ('manual','catalog')` OR `series_role_source not in ('manual','catalog')`. For each, gather platforms (`game_platforms`→`platforms.short_name`), tags (`game_tags`→`tags.name`), and series name (`user_ratings.series_id`→`series.name`).

**Workflow shape (~20 games/agent):**
- Each agent receives a JSON list of `{id, title, normalized_title, series_name, platforms, tags}` and is told: classify each game's session-tolerance and series role from general game knowledge; `session_length` = `short` (clean short stopping points: levels/chapters/cases/in-game days/battles — regardless of total length) | `long` (needs an uninterrupted block) | `null` (unsure); `series_role` = `mainline` | `spinoff` (gaiden/side story/spin-off) | `null` (standalone or unknown). Return per game; do NOT touch any database.
- StructuredOutput schema (per agent): `{ "classifications": [ {"id": int, "normalized_title": string, "session_length": "short"|"long"|null, "series_role": "mainline"|"spinoff"|null, "reason": string} ] }`.
- Controller aggregates all agent results.

**Controller write-back (deterministic, the only DB writer):**
1. For each returned classification, re-read the row's `*_source`. For each trait: if the source is already `manual` or `catalog`, skip (locked/authoritative). Else if the returned value is non-null, `UPDATE games SET <trait> = ?, <trait>_source = 'ai' WHERE id = ?`.
2. Merge non-null results into `game_traits.default.json` keyed by `normalized_title` (sorted keys, each entry holding whichever of `session_length`/`series_role` is non-null), for the owner to review + PR. Do NOT overwrite an existing catalog entry that disagrees without surfacing it — log conflicts.
3. `log()` counts: classified, skipped-locked, left-null/low-confidence, catalog conflicts.
- Re-runnable: only fills unset (non-manual/catalog) rows. "Take as long as you need" — batched for throughput, not one agent per game.

**After the seed:** controller relaunches the app, spot-checks the slate, and reports counts to the owner. The committed `game_traits.default.json` change is the owner's to review and PR (work stays on `main`; controller does not push unless asked).

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Two canonical traits + sources, manual locks → Tasks 2, 3, 7. ✓
- Resolution catalog→manual→AI→null → `apply_traits_catalog` (Task 3) + manual PUT (Task 7) + AI seed (seed op). ✓
- Committed catalog + gitignored per-user override, `load_game_traits` mirroring `load_series_patterns` → Task 1. ✓
- Quick excludes `long`, boosts `short`; Long boosts `long`; null neutral → Task 5. ✓
- TTB demoted but retained as `candidate["time_to_beat_minutes"]` → Task 5 (Steps 5-6) + rewritten TTB test. ✓
- `focus_series_id` column + boost + role routing → Tasks 4, 6, 8, 11. ✓
- Manual selectors in game modal + Add-Game modal → Tasks 9, 10. ✓
- Slot Focus-series picker → Task 11. ✓
- `GET /api/games/<id>` returns traits via `g.*` → Task 7 (Step 1 asserts). ✓
- `/api/series` reused (already exists, returns id+name) → Task 11. ✓
- Claude-Code seed: agents return, controller writes + emits catalog → seed-op section. ✓
- Error handling (null safe; malformed catalog → {}) → Task 1 (malformed test), Task 5 (null neutral). ✓
- Slots never locked / generic → focus_series_id is an optional editable field; no behavior hardcoded. ✓
- Phase B (on-add Anthropic AI) explicitly out of scope. ✓

**Type/name consistency:** `session_length`/`series_role` + `*_source` columns, enum values `short`/`long`/`mainline`/`spinoff`, `source` values `catalog`/`ai`/`manual`, constants `SESSION_FIT_BOOST`/`FOCUS_SERIES_BOOST`/`ROLE_BOOST`, helper `apply_traits_catalog(conn, game_id=None)`, `load_game_traits()`, `migrate_game_traits(conn)` — all used identically across tasks. ✓

**Behavioral note flagged for the owner:** Task 5 *removes* the old genre-tag session penalty (`SESSION_MISMATCH_PENALTY` via `session_tolerant`) rather than keeping it as a null fallback, because a fallback would re-penalize null long-form-genre games in Quick and contradict the spec's "null is neutral." The catalog/seed fills the library so null is transient. The `session_tolerant` function stays in `slot_signals.py` (still unit-tested) but is unused by the ranker.
