# Picks-Tab Library-Consistent Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the picks tab on the library's visual language (shared stats hero on every page, `.game-card` tiles, library inputs), add any-game search + accept/dismiss suggestions, and drive Quick-vs-Long with real time-to-beat + series ranking.

**Architecture:** Phased. Phase 1 extracts the stats hero into `base.html` (shared on every page). Phases 2–4 are backend (a `slot_dismissals` table, new ranking signals in `slots.py`, new/extended API routes) under strict TDD. Phases 5–7 are the UI (modal hours-to-beat, picks rebuild, per-slot settings) verified by server-render smoke tests + manual checks. Phase 8 validates + runs HLTB enrichment.

**Tech Stack:** Python 3, Flask, sqlite3 (stdlib), `requests`, pytest, vanilla JS + Jinja2 + Tailwind. Tests: `uv run python -m pytest` (plain `uv run pytest` fails). Lint: `ruff check` only (never `ruff format`).

**Spec:** `docs/superpowers/specs/2026-06-01-picks-tab-library-consistent-redesign-design.md`

**Conventions locked from the codebase:**
- All templates extend `base.html` (`index`, `recommendations`, `series`, `settings`).
- `base.html` globals: `api` (get/put/post/delete/patch), `openModal`, `closeModal` (line 791), `escapeHtml`, `showModalEl`/`hideModalEl`, `refreshGameList`. The game modal is `#game-modal`; `closeModal()` hides it.
- `api.post` returns `{ok, status, data}`; `api.get/put/patch` return raw JSON.
- `/api/stats` returns `{total_games, by_status:{...}, dlc_owned, dlc_total}`; `renderHeroStats(stats)` already consumes it.
- The games `PUT /api/games/<id>` route is at `app.py:632`; its game-table update block (gated `if 'title' in data or 'cover_url' in data`) is lines 640–653.
- `slots.rank_candidates(conn, slot, limit)` SELECTs `g.*, ur.status, ur.priority, ur.hours_played`; constants `FINISHED_STATUSES`, `FATIGUE_PENALTY`, `SESSION_MISMATCH_PENALTY`, `STARTED_BOOST`. `slot_signals.effective_time_to_beat_minutes(row)` exists and is ready.
- Migrations: idempotent `migrate_*` in `models.py`, registered in `migrate_db`, mirrored in `tests/conftest.py` `temp_db`.
- Commit directly to `main`. End commit messages with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

> **RUNNING APP:** the dev app runs with `use_reloader=False`. STOP any running `app.py` (PowerShell `Stop-Process`) before editing `.py`, then relaunch to verify. Never run against the live `games.db`; tests use temp DBs only.

---

## Phase 1 — Shared stats hero on every page

### Task 1: Extract the stats hero into base.html

**Files:**
- Modify: `templates/base.html` (add hero markup + hero JS above content block)
- Modify: `templates/index.html` (remove its hero markup + hero JS; provide mode-switcher via `hero_aside`)
- Test: `tests/test_shared_hero_render.py`

- [ ] **Step 1: Write the render smoke test**

```python
# tests/test_shared_hero_render.py
"""The shared stats hero (#hero-stats) renders on every page."""
import pytest


@pytest.mark.parametrize("path", ["/", "/recommendations", "/series", "/settings"])
def test_hero_present_on_every_page(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="hero-stats"' in body


def test_library_still_has_mode_switcher(client):
    body = client.get("/").get_data(as_text=True)
    assert 'id="mode-switcher"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_shared_hero_render.py -v`
Expected: FAIL on `/recommendations`, `/series`, `/settings` (hero only on `/` currently).

- [ ] **Step 3: Add the hero band to `base.html`** — locate the element that wraps `{% block content %}` (the main page container). Immediately BEFORE `{% block content %}`, insert:

```html
    <!-- Shared stats hero (every page). Aside is page-specific (library fills it). -->
    <div class="rounded-2xl mb-6 p-5 bg-gradient-to-br from-[#241b3a] via-[#191522] to-[#151515] border border-gray-700/50 flex flex-wrap items-center gap-4">
        <div id="hero-stats" class="flex gap-6"><!-- renderHeroStats() --></div>
        <div class="ml-auto">{% block hero_aside %}{% endblock %}</div>
    </div>
```

- [ ] **Step 4: Move the hero JS into `base.html`** — inside the base script area (where `api`/`openModal` live), add:

```javascript
        function renderHeroStats(stats) {
            const done = stats.total_games
                ? Math.round(((stats.by_status?.completed || 0) + (stats.by_status?.['100'] || 0)) / stats.total_games * 100)
                : 0;
            const tiles = [
                ['total_games', stats.total_games || 0, 'games'],
                ['completed', stats.by_status?.completed || 0, 'completed'],
                ['playing', stats.by_status?.playing || 0, 'playing'],
                ['backlog', stats.by_status?.backlog || 0, 'backlog'],
                ['done', done + '%', 'done'],
                ['dlc', `${stats.dlc_owned || 0}/${stats.dlc_total || 0}`, 'DLC'],
            ];
            const el = document.getElementById('hero-stats');
            if (el) el.innerHTML = tiles.map(([, v, label]) =>
                `<div><div class="text-2xl font-bold text-white leading-none">${v}</div>
                      <div class="text-xs text-gray-400 mt-1">${label}</div></div>`
            ).join('');
        }
        async function loadHeroStats() {
            const stats = await api.get('/api/stats');
            if (stats) renderHeroStats(stats);
        }
        document.addEventListener('DOMContentLoaded', loadHeroStats);
```

- [ ] **Step 5: Update `index.html`** — (a) DELETE the hero band markup (the `rounded-2xl mb-6 … #hero-stats … #mode-switcher` div, lines ~8–15) and replace it by filling the new block at the TOP of `{% block content %}`:

```html
{% block hero_aside %}
<div id="mode-switcher" class="flex gap-1.5 bg-black/30 border border-gray-700/60 rounded-xl p-1.5">
    <!-- Filled by renderModeBar() -->
</div>
{% endblock %}
```

(b) In `index.html`'s script, DELETE the `renderHeroStats` and `loadHeroStats` function definitions (now in base.html) and the `loadHeroStats();` call inside `DOMContentLoaded` (base.html calls it). Keep `renderModeBar`, `setMode`, and all other library JS.

- [ ] **Step 6: Run to verify pass + no regressions**

Run: `uv run python -m pytest tests/test_shared_hero_render.py -v` → PASS.
Run: `uv run python -m pytest -q` → full suite green.

- [ ] **Step 7: Manual verification (controller does this)** — stop the app, relaunch, load `/`, `/recommendations`, `/series`, `/settings`; confirm the hero band shows stats on all four and the mode-switcher appears only on the library, top-right.

- [ ] **Step 8: Commit**

```bash
git add templates/base.html templates/index.html tests/test_shared_hero_render.py
git commit -m "feat(ui): shared stats hero on every page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Dismissals table

### Task 2: `slot_dismissals` migration

**Files:**
- Modify: `models.py` (`migrate_slot_dismissals`, register in `migrate_db`)
- Modify: `tests/conftest.py` (`temp_db`)
- Test: `tests/test_slot_dismissals_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slot_dismissals_migration.py
"""migrate_slot_dismissals: composite-PK table, FK cascade, idempotent."""
import sqlite3

import pytest

from models import migrate_slot_dismissals


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    c.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, label TEXT)")
    yield c
    c.close()


def test_creates_table(conn):
    migrate_slot_dismissals(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(slot_dismissals)").fetchall()}
    assert cols == {"slot_id", "game_id", "created_at"}


def test_is_idempotent(conn):
    migrate_slot_dismissals(conn)
    migrate_slot_dismissals(conn)  # must not raise


def test_composite_pk_blocks_dupes(conn):
    migrate_slot_dismissals(conn)
    conn.execute("INSERT INTO slots (id, label) VALUES (1, 'S')")
    conn.execute("INSERT INTO games (id, title) VALUES (7, 'G')")
    conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (1, 7)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (1, 7)")


def test_fk_cascade_on_slot_delete(conn):
    migrate_slot_dismissals(conn)
    conn.execute("INSERT INTO slots (id, label) VALUES (1, 'S')")
    conn.execute("INSERT INTO games (id, title) VALUES (7, 'G')")
    conn.execute("INSERT INTO slot_dismissals (slot_id, game_id) VALUES (1, 7)")
    conn.execute("DELETE FROM slots WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals").fetchone()[0] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_slot_dismissals_migration.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_slot_dismissals'`

- [ ] **Step 3: Implement `migrate_slot_dismissals` in `models.py`** (after `migrate_slot_history`)

```python
def migrate_slot_dismissals(conn: sqlite3.Connection) -> None:
    """Create the slot_dismissals table if missing. Idempotent.

    A dismissed suggestion (slot_id, game_id) is hidden from that slot's candidate
    list until the slot's current game is replaced (the engine clears the slot's
    rows then). Cascades away if the slot or game is deleted.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_dismissals (
            slot_id    INTEGER NOT NULL,
            game_id    INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (slot_id, game_id),
            FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE CASCADE,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
```

Register it in `migrate_db()` right after the `migrate_slot_history(conn)` line:

```python
    migrate_slot_dismissals(conn)
```

- [ ] **Step 4: Update `tests/conftest.py`** — after `models.seed_default_slots(conn)` (or after the slot-history migrate line), add:

```python
    models.migrate_slot_dismissals(conn)
```

(Place it before `seed_default_slots` is fine too, as long as it runs; order vs seed doesn't matter — no data dependency.)

- [ ] **Step 5: Run to verify pass**

Run: `uv run python -m pytest tests/test_slot_dismissals_migration.py -v` → PASS.
Run: `uv run python -m pytest -q` → full suite green.

- [ ] **Step 6: Commit**

```bash
git add models.py tests/conftest.py tests/test_slot_dismissals_migration.py
git commit -m "feat(slots): slot_dismissals table migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — Ranking signals

### Task 3: dismissal engine + exclusion (slots.py)

**Files:**
- Modify: `slots.py` (`dismiss_suggestion`, `_clear_dismissals`, exclude in `rank_candidates`, clear on pin/outcome)
- Test: `tests/test_slot_dismissals_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slot_dismissals_engine.py
import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_game(conn, title, status="backlog"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _slot_id(conn, label):
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def test_dismissed_game_excluded(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    sid = _slot_id(conn, "Switch · Quick")
    assert any(c["game"]["id"] == gid for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick")))
    slots.dismiss_suggestion(conn, sid, gid)
    assert all(c["game"]["id"] != gid for c in slots.rank_candidates(conn, _slot(conn, "Switch · Quick")))
    conn.close()


def test_dismissals_cleared_on_pin(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    other = _add_game(conn, "Other")
    sid = _slot_id(conn, "Switch · Quick")
    slots.dismiss_suggestion(conn, sid, gid)
    slots.pin_game(conn, sid, other, "go")          # replacing the slot's game clears dismissals
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals WHERE slot_id=?", (sid,)).fetchone()[0] == 0
    conn.close()


def test_dismissals_cleared_on_outcome(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    pinned = _add_game(conn, "Pinned", status="playing")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, pinned, "beat it")
    slots.dismiss_suggestion(conn, sid, gid)
    slots.apply_outcome(conn, sid, "complete")      # frees slot -> clears dismissals
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals WHERE slot_id=?", (sid,)).fetchone()[0] == 0
    conn.close()


def test_beat_chase_does_not_clear_dismissals(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hidden")
    pinned = _add_game(conn, "Pinned")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, pinned, "beat it")    # pin clears (none yet)
    slots.dismiss_suggestion(conn, sid, gid)
    slots.apply_outcome(conn, sid, "beat", chase=True, new_goal="plat")  # keeps same game
    assert conn.execute("SELECT COUNT(*) FROM slot_dismissals WHERE slot_id=?", (sid,)).fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_slot_dismissals_engine.py -v`
Expected: FAIL — `AttributeError: module 'slots' has no attribute 'dismiss_suggestion'`

- [ ] **Step 3: Implement in `slots.py`.** Add helpers after `_pinned_game_ids`:

```python
def dismiss_suggestion(conn, slot_id: int, game_id: int) -> None:
    """Hide a game from a slot's suggestion list until the slot's game changes."""
    conn.execute(
        "INSERT OR IGNORE INTO slot_dismissals (slot_id, game_id) VALUES (?, ?)",
        (slot_id, game_id))
    conn.commit()


def _clear_dismissals(conn, slot_id: int) -> None:
    conn.execute("DELETE FROM slot_dismissals WHERE slot_id = ?", (slot_id,))


def _dismissed_game_ids(conn, slot_id: int) -> set[int]:
    return {r["game_id"] for r in conn.execute(
        "SELECT game_id FROM slot_dismissals WHERE slot_id = ?", (slot_id,)).fetchall()}
```

In `rank_candidates`, after computing `pinned = _pinned_game_ids(conn)`, add:

```python
    dismissed = _dismissed_game_ids(conn, slot["id"]) if slot.get("id") else set()
```

and in the per-game loop, right after the `if game["id"] in pinned: continue` line, add:

```python
        if game["id"] in dismissed:
            continue
```

In `pin_game`, after the UPDATE/commit, clear dismissals (the slot's game is being replaced):

```python
def pin_game(conn, slot_id: int, game_id: int, goal: str | None = None) -> None:
    """Assign a game (+ goal) to a slot. Replaces any current game in that slot."""
    conn.execute(
        "UPDATE slots SET current_game_id = ?, goal = ? WHERE id = ?",
        (game_id, goal, slot_id))
    _clear_dismissals(conn, slot_id)
    conn.commit()
```

In `apply_outcome`, call `_clear_dismissals(conn, slot_id)` in every branch that frees/changes the slot — i.e. inside the `swap` branch, the `beat` `else` (shelve) branch, and the `complete`/`dropped` branch — but NOT the `beat` `chase` branch. Concretely add `_clear_dismissals(conn, slot_id)` immediately before each `_clear_slot(conn, slot_id)` call (swap, shelve, complete/dropped). (The chase branch has no `_clear_slot`, so it correctly keeps dismissals.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slot_dismissals_engine.py -v` → PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check slots.py tests/test_slot_dismissals_engine.py
git add slots.py tests/test_slot_dismissals_engine.py
git commit -m "feat(slots): dismiss suggestions + clear-on-replace + exclusion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: directional time-to-beat ranking (slots.py)

**Files:**
- Modify: `slots.py` (`rank_candidates`: fetch ttb, add directional term)
- Test: `tests/test_slots_ttb_ranking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slots_ttb_ranking.py
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


def _rank(conn, label):
    return [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, label))]


def test_quick_slot_ranks_short_game_above_long(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short Game", hltb_main=300)    # 5h
    long_ = _add_game(conn, "Long Game", hltb_main=2400)    # 40h
    ids = _rank(conn, "Switch · Quick")                     # max_session_minutes=60
    assert ids.index(short) < ids.index(long_)
    conn.close()


def test_long_slot_ranks_long_game_above_short(temp_db):
    conn = models.get_db()
    short = _add_game(conn, "Short Game", hltb_main=300)
    long_ = _add_game(conn, "Long Game", hltb_main=2400)
    ids = _rank(conn, "Switch · Long")                      # min_session_minutes=60, no max
    assert ids.index(long_) < ids.index(short)
    conn.close()


def test_unknown_ttb_is_neutral(temp_db):
    conn = models.get_db()
    # equal priority, no hltb -> no ttb term; both present, order stable by score tie
    a = _add_game(conn, "A", hltb_main=None)
    b = _add_game(conn, "B", hltb_main=None)
    ids = _rank(conn, "Switch · Quick")
    assert a in ids and b in ids
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_slots_ttb_ranking.py -v`
Expected: FAIL — short and long rank equally (no ttb term yet), so the index assertions fail.

- [ ] **Step 3: Implement in `slots.py`.** Add the import at the top alongside `from slot_signals import ...`:

```python
from slot_signals import latency_tolerant, session_tolerant, effective_time_to_beat_minutes
```

Add constants near the others:

```python
TTB_REFERENCE_MINUTES = 1200   # 20h: the pivot between "short" and "long"
TTB_WEIGHT = 0.02              # score points per minute of deviation
TTB_TERM_CAP = 20.0
```

In `rank_candidates`, read the slot's session window once before the loop:

```python
    max_session = slot.get("max_session_minutes")
    min_session = slot.get("min_session_minutes")
```

In the per-game scoring section (after the existing session-tolerance/`SESSION_MISMATCH_PENALTY` block), add the directional time-to-beat term:

```python
        ttb = effective_time_to_beat_minutes(game)
        if ttb is not None:
            if max_session is not None:                     # short-session slot: prefer short games
                term = max(-TTB_TERM_CAP, min(TTB_TERM_CAP, (TTB_REFERENCE_MINUTES - ttb) * TTB_WEIGHT))
                if term:
                    score += term
                    reasons.append("Short play" if term > 0 else "Long for a quick session")
            elif min_session is not None:                   # long-session slot: prefer long games
                term = max(-TTB_TERM_CAP, min(TTB_TERM_CAP, (ttb - TTB_REFERENCE_MINUTES) * TTB_WEIGHT))
                if term:
                    score += term
                    reasons.append("Meaty play" if term > 0 else "Short for a long session")
```

Keep the existing `SESSION_MISMATCH_PENALTY` genre block as-is (it still helps when ttb is unknown).

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slots_ttb_ranking.py -v` → PASS.
Run: `uv run python -m pytest -q` → full suite green (confirm the Task 8/enhancement engine tests still pass — the ttb term is additive and only fires when hltb_main is set, which those tests don't set).

- [ ] **Step 5: Lint + commit**

```bash
ruff check slots.py tests/test_slots_ttb_ranking.py
git add slots.py tests/test_slots_ttb_ranking.py
git commit -m "feat(slots): directional time-to-beat ranking (quick=short, long=long)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: series boost (slots.py)

**Files:**
- Modify: `slots.py` (`rank_candidates`: series boost from slot's most recent history game)
- Test: `tests/test_slots_series_ranking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slots_series_ranking.py
import models
import slots


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_game(conn, title, series_id=None, status="backlog", priority=5):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, "Switch")))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority, series_id) VALUES (?, ?, ?, ?)",
                 (gid, status, priority, series_id))
    conn.commit()
    return gid


def _slot(conn, label):
    return dict(conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone())


def _slot_id(conn, label):
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def test_same_series_as_last_history_game_is_boosted(temp_db):
    conn = models.get_db()
    ff = _series(conn, "Final Fantasy")
    sid = _slot_id(conn, "Switch · Long")
    # A finished FF game passed through this slot:
    ff_done = _add_game(conn, "FF VII", series_id=ff, status="completed")
    conn.execute("INSERT INTO slot_history (slot_id, game_id, outcome) VALUES (?, ?, 'completed')",
                 (sid, ff_done))
    conn.commit()
    # Two candidates, equal priority: one in the FF series, one not.
    ff_next = _add_game(conn, "FF IX", series_id=ff, priority=5)
    other = _add_game(conn, "Random RPG", series_id=None, priority=5)
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Long"))]
    assert ids.index(ff_next) < ids.index(other)
    conn.close()


def test_no_history_no_series_boost(temp_db):
    conn = models.get_db()
    ff = _series(conn, "Final Fantasy")
    a = _add_game(conn, "FF IX", series_id=ff, priority=5)
    b = _add_game(conn, "Random RPG", series_id=None, priority=9)  # higher priority wins, no boost
    ids = [c["game"]["id"] for c in slots.rank_candidates(conn, _slot(conn, "Switch · Long"))]
    assert ids.index(b) < ids.index(a)
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_slots_series_ranking.py -v`
Expected: FAIL on `test_same_series_as_last_history_game_is_boosted` (no boost yet).

- [ ] **Step 3: Implement in `slots.py`.** Add a constant:

```python
SERIES_BOOST = 30.0
```

Add a helper after `_recent_fatigue_tags`:

```python
def _slot_recent_series_id(conn, slot_id):
    """series_id of the most recent game that passed through this slot, or None."""
    row = conn.execute("""
        SELECT ur.series_id
        FROM slot_history h JOIN user_ratings ur ON ur.game_id = h.game_id
        WHERE h.slot_id = ? AND ur.series_id IS NOT NULL
        ORDER BY h.removed_at DESC LIMIT 1
    """, (slot_id,)).fetchone()
    return row["series_id"] if row else None
```

Extend the candidate SELECT in `rank_candidates` to include `ur.series_id` (change `SELECT g.*, ur.status, ur.priority, ur.hours_played` to also select `ur.series_id`). Before the loop:

```python
    recent_series_id = _slot_recent_series_id(conn, slot["id"]) if slot.get("id") else None
```

In the scoring section add:

```python
        if recent_series_id is not None and game["series_id"] == recent_series_id:
            score += SERIES_BOOST
            reasons.append("Next in this series")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slots_series_ranking.py -v` → PASS.
Run: `uv run python -m pytest -q` → full suite green.

- [ ] **Step 5: Lint + commit**

```bash
ruff check slots.py tests/test_slots_series_ranking.py
git add slots.py tests/test_slots_series_ranking.py
git commit -m "feat(slots): boost same-series games from the slot's last play

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — API

### Task 6: dismiss route + games time-to-beat field

**Files:**
- Modify: `app.py` (`POST /api/slots/<id>/dismiss`; extend `PUT /api/games/<id>`; include ttb in `GET /api/games/<id>`)
- Test: `tests/test_api_slots.py` (extend), `tests/test_api_games_ttb.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_games_ttb.py
import models


def _add_game(title):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit(); conn.close()
    return gid


def test_put_sets_time_to_beat_override(client):
    gid = _add_game("Tunic")
    assert client.put(f"/api/games/{gid}", json={"time_to_beat_override_minutes": 660}).status_code == 200
    data = client.get(f"/api/games/{gid}").get_json()
    assert data["time_to_beat_override_minutes"] == 660


def test_put_clears_time_to_beat_override_with_null(client):
    gid = _add_game("Tunic")
    client.put(f"/api/games/{gid}", json={"time_to_beat_override_minutes": 660})
    client.put(f"/api/games/{gid}", json={"time_to_beat_override_minutes": None})
    assert client.get(f"/api/games/{gid}").get_json()["time_to_beat_override_minutes"] is None


def test_get_game_includes_hltb_main(client):
    gid = _add_game("Tunic")
    conn = models.get_db()
    conn.execute("UPDATE games SET hltb_main_minutes=720 WHERE id=?", (gid,)); conn.commit(); conn.close()
    assert client.get(f"/api/games/{gid}").get_json()["hltb_main_minutes"] == 720
```

```python
# append to tests/test_api_slots.py
def test_dismiss_removes_from_candidates(client):
    gid = _add_backlog_game("Dismissable")   # helper already defined in this file
    # find a slot whose candidates include this game (Switch slot if it has Switch platform)
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    assert client.post(f"/api/slots/{sid}/dismiss", json={"game_id": gid}).status_code == 200
    slot = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert all(c["game"]["id"] != gid for c in slot["candidates"])
```

> Note: `_add_backlog_game` in `test_api_slots.py` inserts a game with status backlog but no platform; if the chosen slot has a platform filter the game may not be a candidate anyway, making the assertion vacuously true. To make the dismiss test meaningful, give the helper game a platform that matches `slots[0]`, OR assert the dismiss row exists instead: `assert client.get(...)`—simplest is to add a Switch platform link in the test. Implement by inserting into game_platforms for the first slot's first platform. Keep the test meaningful (the game must be a candidate BEFORE dismiss).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/test_api_games_ttb.py tests/test_api_slots.py -k "ttb or dismiss or time_to_beat or hltb_main" -v`
Expected: FAIL (route 404 / field absent).

- [ ] **Step 3: Implement in `app.py`.**

(a) Add the dismiss route after the other `/api/slots/<id>/...` routes:

```python
@app.route('/api/slots/<int:slot_id>/dismiss', methods=['POST'])
def api_slot_dismiss(slot_id):
    """Hide a suggested game from this slot until its game is replaced."""
    data = request.get_json() or {}
    game_id = data.get('game_id')
    if not game_id:
        return jsonify({'error': 'game_id required'}), 400
    conn = get_db()
    slots.dismiss_suggestion(conn, slot_id, game_id)
    conn.close()
    return jsonify({'ok': True})
```

(b) Extend the games `PUT` game-table update block (app.py ~640). Change the gate and add the field:

```python
        if 'title' in data or 'cover_url' in data or 'time_to_beat_override_minutes' in data:
            game_updates = []
            game_params = []
            if 'title' in data:
                game_updates.append("title = ?")
                game_updates.append("normalized_title = ?")
                game_params.append(data['title'])
                game_params.append(normalize_title(data['title']))
            if 'cover_url' in data:
                game_updates.append("cover_url = ?")
                game_params.append(data['cover_url'] if data['cover_url'] else None)
            if 'time_to_beat_override_minutes' in data:
                game_updates.append("time_to_beat_override_minutes = ?")
                v = data['time_to_beat_override_minutes']
                game_params.append(int(v) if v not in (None, "") else None)
            game_updates.append("updated_at = CURRENT_TIMESTAMP")
            game_params.append(game_id)
            conn.execute(f"UPDATE games SET {', '.join(game_updates)} WHERE id = ?", game_params)
```

(c) Ensure `GET /api/games/<id>` returns the ttb fields. Find the `GET /api/games/<int:game_id>` route; it builds a game dict. Add `time_to_beat_override_minutes` and `hltb_main_minutes` to the SELECT / returned dict (if it does `SELECT *` from games they're already present; if it lists columns explicitly, add them). Verify by reading the route and matching its style.

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_api_games_ttb.py tests/test_api_slots.py -v` → PASS.
Run: `uv run python -m pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_games_ttb.py tests/test_api_slots.py
git commit -m "feat(api): slot dismiss route + editable time-to-beat override

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Modal: hours-to-beat + re-rank on close

### Task 7: editable hours-to-beat in the game modal + re-rank on close

**Files:**
- Modify: `templates/base.html` (modal field + save + `closeModal` re-rank hook)

- [ ] **Step 1: Add the re-rank hook to `closeModal`** (base.html:791). Change it to:

```javascript
        function closeModal() {
            const modal = document.getElementById('game-modal');
            modal.classList.add('hidden');
            modal.classList.remove('flex');
            if (typeof refreshGameList === 'function') refreshGameList();
        }
```

(On the library this reloads the grid; on picks `refreshGameList` is aliased to `loadSlate`, so edits re-rank on close.)

- [ ] **Step 2: Add the hours-to-beat field to the modal.** In `loadGameModal(gameId)` (base.html ~801), the modal is populated from `await api.get('/api/games/'+gameId)`. Add an input near the priority/notes controls:

```html
            <label class="block text-sm text-gray-400 mt-3">Hours to beat
                <input type="number" min="0" step="0.5" id="ttb-input"
                       class="mt-1 w-24 bg-surface rounded-lg border border-gray-600 px-2 py-1 text-white text-sm focus:border-accent focus:outline-none"
                       onchange="saveTimeToBeat(${gameId}, this.value)">
            </label>
            <span class="text-xs text-gray-500 ml-2" id="ttb-hint"></span>
```

Seed its value when building the modal: effective minutes = `game.time_to_beat_override_minutes ?? game.hltb_main_minutes`; show hours (minutes/60) in `#ttb-input`; set `#ttb-hint` to "(from HowLongToBeat)" when only `hltb_main_minutes` is set, "(manual)" when an override is set, "" otherwise.

Add the save function in the base script:

```javascript
        async function saveTimeToBeat(gameId, hoursValue) {
            const mins = hoursValue === '' ? null : Math.round(parseFloat(hoursValue) * 60);
            await api.put(`/api/games/${gameId}`, { time_to_beat_override_minutes: mins });
        }
```

- [ ] **Step 3: Manual verification (controller)** — stop app, relaunch, open a game modal: confirm the Hours-to-beat field shows the effective value, editing it persists (reopen shows the new value), and closing the modal on the picks page re-ranks the slate.

- [ ] **Step 4: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): editable hours-to-beat in modal + re-rank on close

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6 — Picks page rebuild (library-consistent)

### Task 8: shared `gameCardHtml` helper + Needs-Rating using it

**Files:**
- Modify: `templates/base.html` (add `gameCardHtml(game)` global)
- Test: none (covered by render smoke test in Task 9)

- [ ] **Step 1: Add a `gameCardHtml(game)` global to `base.html`** that returns the EXACT library `.game-card` markup (copy from `index.html` `renderGames`, single-card version), so the library and picks render identical tiles. Signature:

```javascript
        function gameCardHtml(game) {
            const coverUrl = game.cover_url || '';
            const cover = coverUrl
                ? `<img src="${coverUrl}" alt="${escapeHtml(game.title)}" class="w-full h-full object-cover"
                        onerror="this.parentElement.innerHTML='<div class=\\'cover-placeholder w-full h-full flex items-center justify-center\\'><span class=\\'text-4xl\\'>🎮</span></div>'">`
                : `<div class="cover-placeholder w-full h-full flex items-center justify-center border-2 border-dashed border-gray-600"><span class="text-4xl">🎮</span></div>`;
            const badges = (game.platforms || []).map(p => `<span class="platform-badge platform-${p}">${p}</span>`).join('')
                + `<span class="status-badge status-${game.status || 'backlog'}">${game.status === '100' ? '100%' : game.status === 'completed' ? 'complete' : (game.status || 'backlog').replace('_',' ')}</span>`;
            return `<div class="game-card bg-surface-light rounded-lg overflow-hidden cursor-pointer" data-game-id="${game.id}" onclick="openModal(${game.id})">
                <div class="aspect-[3/4] relative overflow-hidden">${cover}</div>
                <div class="p-3"><h3 class="font-medium text-sm text-white leading-tight line-clamp-2 min-h-[2.5rem]" title="${escapeHtml(game.title)}">${escapeHtml(game.title)}</h3>
                    <div class="mt-2 flex flex-wrap items-center gap-1">${badges}</div></div></div>`;
        }
```

(Optionally refactor `index.html`'s `renderGames` to call `gameCardHtml` — do it if low-risk; otherwise leave the library as-is and just share the helper for picks. Do NOT break the library.)

- [ ] **Step 2: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): shared gameCardHtml helper (library tile markup)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 9: rebuild recommendations.html (slate cards, search, accept/dismiss suggestions, needs-rating)

**Files:**
- Modify: `templates/recommendations.html` (full rebuild)
- Test: `tests/test_recommendations_render.py` (extend assertions)

- [ ] **Step 1: Rebuild `{% block content %}`** — no custom hero (shared hero is in base). Structure:

```html
{% block content %}
<div class="space-y-8">
    <div>
        <h2 class="text-xl font-bold text-white mb-3">Your Slate</h2>
        <div id="slate" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4"></div>
    </div>
    <div>
        <h2 class="text-xl font-bold text-white mb-3">Recently Finished</h2>
        <div id="recently-finished" class="flex flex-wrap gap-3"></div>
    </div>
    <div id="needs-rating-section" class="hidden">
        <h2 class="text-xl font-bold text-white mb-3">Needs Rating</h2>
        <div id="needs-rating-grid" class="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-4"></div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Rebuild `{% block scripts %}`** implementing (use `api`, `openModal`, `escapeHtml`, `gameCardHtml` from base):

- `loadSlate()` → `api.get('/api/slots')` → `renderSlate(resp.slots)`, `renderRecentlyFinished(resp.recently_finished)`, and `api.get('/api/games')` → render Needs-Rating via `gameCardHtml` into `#needs-rating-grid` (filter: status not backlog/wishlist and no rating; hide section if none).
- `renderSlate(slots)` → for each slot a card `bg-surface-light rounded-lg p-4` containing: header (label + a gear button `onclick="toggleSlotSettings(id)"`), the current game (if any) as a compact tile (cover + title + goal + status badge, `onclick=openModal`) with action buttons **Beat/Complete/Dropped/Swap** (small, `bg-surface rounded px-2 py-1 text-xs`), a **search box** (library input classes) with a results dropdown, and a **suggestions list** (`slot.candidates`, up to 5) where each row shows a small cover + title + first reason and two icon buttons: **✓** `onclick="pinFromSuggestion(slotId, gameId)"` and **✕** `onclick="dismissSuggestion(slotId, gameId)"`; clicking the row's title/cover calls `openModal(gameId)`.
- Beat flow: inline — replace the card's action area with a two-button "Chase 100% / Shelve" choice (no `confirm`/`prompt`). Goal edit: inline text input committed on Enter/blur to `PATCH /api/slots/<id>/goal`.
- Search: `oninput` debounced (200ms) → `api.get('/api/games/search?q=' + encodeURIComponent(q))` → render a dropdown of small rows (cover + title + platforms); clicking a result calls `pinFromSuggestion(slotId, gameId)` (pins ANY game). Clear the box + dropdown after pin.
- Actions: `pinFromSuggestion` → `api.post('/api/slots/'+slotId+'/pin', {game_id})` then `loadSlate()`. `dismissSuggestion` → `api.post('/api/slots/'+slotId+'/dismiss', {game_id})` then `loadSlate()`. Outcomes → `api.post('/api/slots/'+slotId+'/outcome', {outcome, chase, new_goal})` then `loadSlate()`.
- Define `refreshGameList = loadSlate;` (so base's `closeModal` re-ranks on close) and `document.addEventListener('DOMContentLoaded', loadSlate)`.
- `renderRecentlyFinished(items)` → small tiles (cover + title + outcome), hide if empty.

Use the library's input/dropdown classes verbatim for visual consistency. No `prompt()`/`alert()` anywhere.

- [ ] **Step 3: Extend the render smoke test** in `tests/test_recommendations_render.py`:

```python
def test_recommendations_has_slate_and_needs_rating(client):
    body = client.get("/recommendations").get_data(as_text=True)
    assert 'id="slate"' in body
    assert 'id="needs-rating-grid"' in body
    assert "loadSlate" in body
```

Run: `uv run python -m pytest tests/test_recommendations_render.py -q` → PASS. Run `uv run python -m pytest -q` → green.

- [ ] **Step 4: Manual verification (controller)** — relaunch; on `/recommendations`: slot cards match library surfaces; search finds + pins any game (e.g. a PS game into a Switch slot); suggestions show covers with ✓/✕; dismiss hides until replace; clicking a suggestion opens the modal and closing it re-ranks; Needs-Rating tiles are the same size as the library.

- [ ] **Step 5: Commit**

```bash
git add templates/recommendations.html tests/test_recommendations_render.py
git commit -m "feat(ui): rebuild picks tab consistent with library (search, accept/dismiss, tiles)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 7 — Per-slot settings dropdown

### Task 10: per-slot gear settings (replace standalone Slots button)

**Files:**
- Modify: `templates/recommendations.html` (settings dropdown per slot + add-slot)

- [ ] **Step 1: Implement `toggleSlotSettings(slotId)`** — opens an inline panel/dropdown on the slot card (library dropdown styling: `bg-surface-light border border-gray-700 rounded-lg p-3`) with: label input, platform checkboxes (Switch/PS/Xbox/Steam/PC), max/min session number inputs, `streamable_only` + `prioritize_started` checkboxes, `context_notes` textarea, **Save** (`api.patch('/api/slots/'+id, payload)`) and **Delete** (`api.delete('/api/slots/'+id)`), then `loadSlate()`. Add an **Add slot** button at the end of the slate (`api.post('/api/slots', {label:'New slot', platforms:[]})` → `loadSlate()`). Reuse `_slotRowPayload`-style collection scoped to the slot's panel.

- [ ] **Step 2: Manual verification (controller)** — gear opens a library-styled editor; rename/toggle/save reflects on the slate; add → 5th slot; delete → removed. No standalone "Slots" button remains.

- [ ] **Step 3: Commit**

```bash
git add templates/recommendations.html
git commit -m "feat(ui): per-slot settings dropdown (library-styled), drop Slots button

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 8 — Final validation + data

### Task 11: full suite, lint, HLTB enrichment, manual smoke

- [ ] **Step 1:** `uv run python -m pytest -q` → all green. Investigate any failure; do not skip.
- [ ] **Step 2:** `ruff check .` → clean. (Never `ruff format`.)
- [ ] **Step 3: HLTB enrichment (controller, against live DB after backup).** Back up `games.db`, relaunch the app, `POST /api/hltb/refresh` (or click "Refresh play-times"), confirm a sample of games (Atelier Ryza, Grandia, a short indie) get sane minutes, and that Switch·Quick now ranks short games above the long JRPGs while Switch·Long does the reverse.
- [ ] **Step 4: Manual smoke of the whole flow** — pin via search, accept/dismiss suggestions, beat→chase/shelve, edit hours-to-beat → re-rank, series boost after a series game is in a slot's history.
- [ ] **Step 5: Commit** any final touch-ups.

---

## Self-review (completed by plan author)

**Spec coverage:**
- Shared hero on every page → Task 1. ✓
- Picks layout (hero→slate→needs-rating, library tiles) → Tasks 8, 9. ✓
- Slot card: current game + actions + search-any-game + suggestions accept/dismiss → Task 9. ✓
- Dismiss state server-side + clear-on-replace + exclusion → Tasks 2, 3. ✓
- Click suggestion → modal → re-rank on close → Tasks 7 (close hook), 9 (wiring). ✓
- Manual hours-to-beat in modal → Tasks 6 (API), 7 (UI). ✓
- Ranking: directional time-to-beat → Task 4; series boost → Task 5; any-game override (search pin) → Task 9 (uses existing `pin_game`, bypasses filters). ✓
- Settings per-slot dropdown, no clunky button → Task 10. ✓
- HLTB enrichment run → Task 11. ✓
- Testing per conventions (temp DB, mocked HTTP not needed here, ruff check, render smoke) → throughout. ✓
- Scope boundary (no chat) → honored. ✓

**Placeholder scan:** backend tasks have complete code; UI tasks (1, 7, 9, 10) give concrete markup/JS + the exact endpoints and are verified by render smoke tests + controller manual checks (no JS test harness exists in this repo — same approach as the SP1 plan). The Task 6 dismiss-test note flags making the helper game a candidate; the implementer must give it a matching platform so the assertion is meaningful.

**Type/signature consistency:** `dismiss_suggestion(conn, slot_id, game_id)`, `_clear_dismissals`, `_dismissed_game_ids`, `_slot_recent_series_id`, constants `TTB_REFERENCE_MINUTES/TTB_WEIGHT/TTB_TERM_CAP/SERIES_BOOST` used consistently; `rank_candidates` reads `slot["id"]`/`max_session_minutes`/`min_session_minutes`; routes `/api/slots/<id>/dismiss`, `PUT /api/games/<id>` field `time_to_beat_override_minutes`; UI `loadSlate`/`pinFromSuggestion`/`dismissSuggestion`/`gameCardHtml`/`refreshGameList` consistent across tasks.

**Open implementation-time confirmations (flagged inline):** the `GET /api/games/<id>` route's column list (add ttb fields if not `SELECT *`); where base.html renders `{% block content %}` (place the hero band immediately above it); the exact spot in `loadGameModal` to inject the hours-to-beat field.
