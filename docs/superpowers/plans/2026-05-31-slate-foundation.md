# The Slate — Foundation (SP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the picks tab around a deterministic "Slate" — user-defined context slots, each always holding one game + a goal — backed by HowLongToBeat session-fit signals and per-slot ranking.

**Architecture:** Three new SQLite tables (`slots`, `slot_history`, signal columns on `games`) added via idempotent `migrate_*` functions. A pure `slot_signals.py` derives session-tolerance / latency-tolerance from genre tags + hours via extensible lookup tables. A `requests`-based `hltb.py` enriches games with HowLongToBeat durations (degrades gracefully). A `slots.py` engine does hard-filtered, fit-scored per-slot ranking (reusing `recommendation.py`'s tag affinity) plus pin/outcome lifecycle. New `/api/slots*` routes and a rebuilt `templates/recommendations.html` surface it. No AI — the Anthropic chat is SP2 and plugs into `slots.rank_candidates`.

**Tech Stack:** Python 3, Flask, sqlite3 (stdlib), `requests`, pytest. Run tests with `uv run python -m pytest` (plain `uv run pytest` fails — `ModuleNotFoundError: models`). Lint gate: `ruff check` only (never `ruff format` — codebase is hand-aligned).

**Spec:** `docs/superpowers/specs/2026-05-31-slate-foundation-design.md`

**Conventions locked from the codebase:**
- Statuses are free-text strings on `user_ratings.status`. Lifecycle mapping: **beat → `completed`**, **complete → `100`**, **dropped → `dropped`**. (`100` is the existing "fully complete" value used in `templates/recommendations.html`.)
- A game's platforms come from `game_platforms` joined to `platforms.short_name` (multi-valued). Seed slots reference short_names: `Switch`, `PS`, `Xbox`.
- A game's genre tags come from `game_tags` joined to `tags.name`.
- Migrations are idempotent functions in `models.py`, registered in `migrate_db()`, and added to the `temp_db` fixture in `tests/conftest.py`.
- Migration tests use an in-memory `sqlite3` connection (see `tests/test_dlc_review_queue_migration.py`). Engine/API tests use the `temp_db` / `client` fixtures (see `tests/conftest.py`).
- Routes follow: `conn = get_db()` … `jsonify(...)` … `conn.close()`.
- Commit directly to `main` (no branches). End every commit message with the Co-Authored-By trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

> **IMPORTANT — running app:** the dev app is started with `use_reloader=False`. **Stop any running `app.py` (PowerShell `Stop-Process`) before editing `.py` files**, then relaunch to manually verify. Never run the implementation against the live `games.db` — tests use temp DBs only.

---

## Phase 1 — Data model

### Task 1: `slots` table migration

**Files:**
- Modify: `models.py` (add `migrate_slots`)
- Test: `tests/test_slots_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slots_migration.py
"""Tests for migrate_slots / migrate_slot_history (mirrors test_dlc_review_queue_migration.py)."""
from __future__ import annotations

import sqlite3

import pytest

from models import migrate_slots, migrate_slot_history


def _columns(conn, table):
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")  # FK target
    yield c
    c.close()


def test_slots_creates_table_and_columns(conn):
    migrate_slots(conn)
    cols = _columns(conn, "slots")
    assert set(cols) == {
        "id", "label", "sort_order", "platforms", "max_session_minutes",
        "min_session_minutes", "requires_low_latency", "context_notes",
        "current_game_id", "goal",
    }


def test_slots_is_idempotent(conn):
    migrate_slots(conn)
    migrate_slots(conn)  # must not raise


def test_slots_current_game_fk_set_null_on_delete(conn):
    migrate_slots(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO games (id, title) VALUES (1, 'X')")
    conn.execute(
        "INSERT INTO slots (label, sort_order, current_game_id) VALUES ('S', 0, 1)")
    conn.execute("DELETE FROM games WHERE id = 1")
    g = conn.execute("SELECT current_game_id FROM slots WHERE label='S'").fetchone()[0]
    assert g is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slots_migration.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_slots'`

- [ ] **Step 3: Implement `migrate_slots` in `models.py`** (place after `migrate_dlc_review_queue`, before `migrate_db`)

```python
def migrate_slots(conn: sqlite3.Connection) -> None:
    """Create the slots table if missing. Idempotent.

    A slot is a user-defined play context (label + constraints). It always holds
    at most one current game + a plaintext goal. Constraints (platforms, session
    window, latency) drive deterministic eligibility now and the SP2 chat prompt
    later. current_game_id is SET NULL on game delete so a deleted game never
    orphans a slot.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            label                TEXT    NOT NULL,
            sort_order           INTEGER NOT NULL DEFAULT 0,
            platforms            TEXT,          -- JSON array of platform short_names
            max_session_minutes  INTEGER,       -- upper bound on a comfortable sitting
            min_session_minutes  INTEGER,       -- lower bound
            requires_low_latency INTEGER NOT NULL DEFAULT 0,
            context_notes        TEXT,          -- owner's own words; feeds SP2 prompt
            current_game_id      INTEGER,
            goal                 TEXT,
            FOREIGN KEY (current_game_id) REFERENCES games(id) ON DELETE SET NULL
        )
    """)
    conn.commit()
```

- [ ] **Step 4: Run test to verify the slots tests pass**

Run: `uv run python -m pytest tests/test_slots_migration.py -k slots_creates -v`
Expected: PASS (the `slot_history` import will still fail collection — that's fixed in Task 2; run with `-k slots_` only here.)

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_slots_migration.py
git commit -m "feat(slots): slots table migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `slot_history` table migration

**Files:**
- Modify: `models.py` (add `migrate_slot_history`)
- Test: `tests/test_slots_migration.py` (extend)

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_slots_migration.py

def test_history_creates_table_and_columns(conn):
    migrate_slot_history(conn)
    cols = _columns(conn, "slot_history")
    assert set(cols) == {
        "id", "slot_id", "game_id", "goal",
        "pinned_at", "removed_at", "outcome",
    }


def test_history_is_idempotent(conn):
    migrate_slot_history(conn)
    migrate_slot_history(conn)  # must not raise


def test_history_accepts_outcomes(conn):
    migrate_slot_history(conn)
    for outcome in ("beat", "completed", "dropped", "shelved"):
        conn.execute(
            "INSERT INTO slot_history (slot_id, game_id, outcome) VALUES (1, 1, ?)",
            (outcome,))
    n = conn.execute("SELECT COUNT(*) FROM slot_history").fetchone()[0]
    assert n == 4
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_slots_migration.py -k history -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_slot_history'`

- [ ] **Step 3: Implement `migrate_slot_history` in `models.py`** (after `migrate_slots`)

```python
def migrate_slot_history(conn: sqlite3.Connection) -> None:
    """Create the slot_history table if missing. Idempotent.

    One row per game that has passed through a slot — the "what did I just finish"
    + momentum + genre-fatigue memory. outcome is one of beat/completed/dropped/shelved.
    No FK constraints on slot_id/game_id: history must survive slot or game deletion.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id    INTEGER NOT NULL,
            game_id    INTEGER NOT NULL,
            goal       TEXT,
            pinned_at  TIMESTAMP,
            removed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            outcome    TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_slot_history_removed ON slot_history(removed_at)")
    conn.commit()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slots_migration.py -v`
Expected: PASS (all slots + history tests).

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_slots_migration.py
git commit -m "feat(slots): slot_history table migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: signal columns on `games`

**Files:**
- Modify: `models.py` (add `migrate_game_signals`)
- Test: `tests/test_slots_migration.py` (extend)

- [ ] **Step 1: Append the failing test**

```python
# append to tests/test_slots_migration.py
from models import migrate_game_signals  # add to imports at top


def test_game_signals_adds_columns(conn):
    migrate_game_signals(conn)
    cols = _columns(conn, "games")
    for c in ("hltb_id", "hltb_main_minutes", "hltb_main_extra_minutes",
              "hltb_completionist_minutes", "time_to_beat_override_minutes",
              "input_lag_override"):
        assert c in cols


def test_game_signals_is_idempotent(conn):
    migrate_game_signals(conn)
    migrate_game_signals(conn)  # must not raise (column-exists guard)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_slots_migration.py -k game_signals -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_game_signals'`

- [ ] **Step 3: Implement `migrate_game_signals` in `models.py`**

```python
def migrate_game_signals(conn: sqlite3.Connection) -> None:
    """Add HowLongToBeat + override signal columns to games. Idempotent.

    Session-tolerance and the default latency tolerance are NOT stored — they are
    derived at scoring time (slot_signals.py) so retuning the lookup tables re-scores
    everything without a migration. Only the raw HLTB durations and the manual
    overrides live here.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()]
    additions = [
        ("hltb_id", "TEXT"),
        ("hltb_main_minutes", "INTEGER"),
        ("hltb_main_extra_minutes", "INTEGER"),
        ("hltb_completionist_minutes", "INTEGER"),
        ("time_to_beat_override_minutes", "INTEGER"),
        ("input_lag_override", "INTEGER"),
    ]
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE games ADD COLUMN {name} {decl}")
    conn.commit()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slots_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add models.py tests/test_slots_migration.py
git commit -m "feat(slots): HLTB + override signal columns on games

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: register migrations + seed the four slots

**Files:**
- Modify: `models.py` (`migrate_db`, new `seed_default_slots`)
- Modify: `tests/conftest.py` (`temp_db` fixture)
- Test: `tests/test_slots_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slots_seed.py
"""seed_default_slots inserts the four seed slots once, idempotently."""
import sqlite3

import pytest

from models import migrate_slots, seed_default_slots


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    migrate_slots(c)
    yield c
    c.close()


def test_seeds_four_slots(conn):
    seed_default_slots(conn)
    rows = conn.execute("SELECT label, platforms, requires_low_latency FROM slots ORDER BY sort_order").fetchall()
    labels = [r["label"] for r in rows]
    assert labels == ["Switch · Quick", "Switch · Long", "Garage · Console", "Long · Stream-safe"]


def test_seed_is_idempotent(conn):
    seed_default_slots(conn)
    seed_default_slots(conn)
    n = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    assert n == 4  # does not double-insert when slots already exist


def test_seed_skips_when_user_has_slots(conn):
    conn.execute("INSERT INTO slots (label, sort_order) VALUES ('Custom', 0)")
    seed_default_slots(conn)
    n = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    assert n == 1  # never clobbers existing user-defined slots
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_slots_seed.py -v`
Expected: FAIL — `ImportError: cannot import name 'seed_default_slots'`

- [ ] **Step 3: Implement seeding + register migrations in `models.py`**

Add near the other module-level constants (after `KNOWN_EDITION_SUFFIXES`):

```python
# Seed slots: the owner's four context slots. Inserted only when the slots table
# is empty (seed-once); fully user-editable afterward. platforms are platform
# short_names (see the platforms table). See the gamer-persona memory.
import json as _json  # already imported at module top; keep one import only

SEED_SLOTS = (
    {"label": "Switch · Quick",      "sort_order": 0, "platforms": ["Switch"],
     "max_session_minutes": 60, "min_session_minutes": None, "requires_low_latency": 0,
     "context_notes": "Couch, short sitting, kids in bed. Clean stopping points."},
    {"label": "Switch · Long",       "sort_order": 1, "platforms": ["Switch"],
     "max_session_minutes": None, "min_session_minutes": 60, "requires_low_latency": 0,
     "context_notes": "Couch, longer Switch session."},
    {"label": "Garage · Console",    "sort_order": 2, "platforms": ["PS", "Xbox"],
     "max_session_minutes": None, "min_session_minutes": None, "requires_low_latency": 1,
     "context_notes": "Needs the real garage setup; reflex/low-latency; worth the trip."},
    {"label": "Long · Stream-safe",  "sort_order": 3, "platforms": ["PS", "Xbox"],
     "max_session_minutes": None, "min_session_minutes": 60, "requires_low_latency": 0,
     "context_notes": "Turn-based / lag-tolerant. Garage or Shield-streamed to the couch."},
)


def seed_default_slots(conn: sqlite3.Connection) -> None:
    """Insert the seed slots only if the slots table is empty. Idempotent; never
    clobbers user-defined slots."""
    existing = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    if existing:
        return
    for s in SEED_SLOTS:
        conn.execute(
            "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
            "min_session_minutes, requires_low_latency, context_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (s["label"], s["sort_order"], _json.dumps(s["platforms"]),
             s["max_session_minutes"], s["min_session_minutes"],
             s["requires_low_latency"], s["context_notes"]))
    conn.commit()
```

> Note: `json` is already imported at the top of `models.py`. Do NOT add `import json as _json` if it causes a duplicate — instead use the existing `json` module: replace `_json.dumps` with `json.dumps` and drop the alias line. (The alias is only shown to make the snippet self-contained.)

Then register in `migrate_db()` — add these lines just before the `reclean_display_titles(conn)` call:

```python
    # Add the Slate tables (picks-tab revamp foundation)
    migrate_slots(conn)
    migrate_slot_history(conn)
    migrate_game_signals(conn)
    seed_default_slots(conn)
```

- [ ] **Step 4: Update the `temp_db` fixture** in `tests/conftest.py` so engine/API tests get the new tables. After the existing `models.migrate_dlc_review_queue(conn)` line, add:

```python
    models.migrate_slots(conn)
    models.migrate_slot_history(conn)
    models.migrate_game_signals(conn)
    models.seed_default_slots(conn)
```

- [ ] **Step 5: Run to verify pass + full suite green**

Run: `uv run python -m pytest tests/test_slots_seed.py tests/test_slots_migration.py -v`
Expected: PASS.
Run: `uv run python -m pytest -q`
Expected: full suite still green (no regressions from the conftest change).

- [ ] **Step 6: Commit**

```bash
git add models.py tests/conftest.py tests/test_slots_seed.py
git commit -m "feat(slots): register Slate migrations + seed four slots

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Signal derivation (lookup tables)

### Task 5: `slot_signals.py` — session & latency tolerance + effective time-to-beat

**Files:**
- Create: `slot_signals.py`
- Test: `tests/test_slot_signals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slot_signals.py
"""Pure signal-derivation lookups (no DB)."""
from slot_signals import (
    session_tolerant, latency_tolerant, effective_time_to_beat_minutes,
)


def test_short_session_genres_are_tolerant():
    assert session_tolerant({"Roguelike"}) is True
    assert session_tolerant({"Puzzle", "Indie"}) is True


def test_long_form_genres_are_not_tolerant():
    assert session_tolerant({"Open World"}) is False
    assert session_tolerant({"JRPG"}) is False


def test_unknown_genre_defaults_to_tolerant():
    # No evidence it's long-form -> don't exclude it from short slots.
    assert session_tolerant(set()) is True
    assert session_tolerant({"Totally Made Up Genre"}) is True


def test_latency_sensitive_genres_not_tolerant():
    assert latency_tolerant({"Fighting"}, None) is False
    assert latency_tolerant({"Shooter"}, None) is False


def test_latency_tolerant_genres():
    assert latency_tolerant({"Strategy"}, None) is True
    assert latency_tolerant({"JRPG"}, None) is True


def test_latency_override_wins():
    # override 1 = force tolerant, 0 = force not, regardless of tags
    assert latency_tolerant({"Fighting"}, 1) is True
    assert latency_tolerant({"Strategy"}, 0) is False


def test_effective_time_to_beat_prefers_override():
    row = {"time_to_beat_override_minutes": 600, "hltb_main_minutes": 1200}
    assert effective_time_to_beat_minutes(row) == 600


def test_effective_time_to_beat_falls_back_to_hltb_main():
    row = {"time_to_beat_override_minutes": None, "hltb_main_minutes": 1200}
    assert effective_time_to_beat_minutes(row) == 1200


def test_effective_time_to_beat_none_when_unknown():
    row = {"time_to_beat_override_minutes": None, "hltb_main_minutes": None}
    assert effective_time_to_beat_minutes(row) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_slot_signals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'slot_signals'`

- [ ] **Step 3: Implement `slot_signals.py`**

```python
"""Derive session-fit signals from a game's genre tags + hours.

Lookup tables are module-level and extensible (frozensets), per the project's
"fixes must be general" rule — retuning a table re-scores every game with no
migration. All functions are pure: they take already-fetched tag names / row data,
never a DB connection.
"""
from __future__ import annotations

# Genres whose games are typically grindy/long-form and do NOT suit a short sitting.
# Anything not listed here is treated as short-session tolerant (innocent until
# proven long), so an unknown/untagged game is never silently excluded.
LONG_FORM_GENRES = frozenset({
    "Open World", "JRPG", "RPG", "Strategy", "Simulation", "Story Rich",
})

# Genres that suffer from input lag -> excluded from stream-only contexts and
# required for the low-latency (garage) slot.
LATENCY_SENSITIVE_GENRES = frozenset({
    "Fighting", "Shooter", "Action", "Platformer", "Racing", "Rhythm", "Metroidvania",
})


def session_tolerant(tag_names: set[str]) -> bool:
    """True if the game is enjoyable in a short (<~1hr) sitting / has clean stops."""
    return not (set(tag_names) & LONG_FORM_GENRES)


def latency_tolerant(tag_names: set[str], override: int | None) -> bool:
    """True if the game plays fine over a streamed (laggy) connection.

    override: 1 -> force tolerant, 0 -> force not, None -> derive from tags.
    """
    if override is not None:
        return bool(override)
    return not (set(tag_names) & LATENCY_SENSITIVE_GENRES)


def effective_time_to_beat_minutes(row) -> int | None:
    """Manual override wins; else HLTB 'main story' minutes; else None (unknown)."""
    override = row["time_to_beat_override_minutes"] if "time_to_beat_override_minutes" in row.keys() \
        else row.get("time_to_beat_override_minutes") if isinstance(row, dict) else None
    if override is not None:
        return override
    main = row["hltb_main_minutes"] if not isinstance(row, dict) else row.get("hltb_main_minutes")
    return main
```

> Implementation note: `row` may be a `sqlite3.Row` or a plain dict in tests. The guards above handle both. If preferred, normalize callers to always pass a dict and simplify — but keep the dict-tolerant tests passing.

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slot_signals.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `ruff check slot_signals.py tests/test_slot_signals.py`
Expected: no errors.

```bash
git add slot_signals.py tests/test_slot_signals.py
git commit -m "feat(slots): session/latency tolerance + time-to-beat derivation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — HowLongToBeat enrichment

### Task 6: `hltb.py` — search, parse, match (mocked HTTP)

**Files:**
- Create: `hltb.py`
- Test: `tests/test_hltb.py`

HowLongToBeat exposes an unofficial JSON search endpoint (`POST https://howlongtobeat.com/api/search`). We hit it with `requests` (already a dependency), match by normalized title, and read the `comp_main` / `comp_plus` / `comp_100` fields (seconds). All network calls are mocked in tests — never hit the live site in CI.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hltb.py
"""HLTB search/parse with mocked HTTP — never hits the network."""
from unittest.mock import patch

import hltb


def _fake_response(payload, status=200):
    class R:
        status_code = status
        def json(self):
            return payload
        def raise_for_status(self):
            pass
    return R()


SAMPLE = {"data": [
    {"game_id": 68151, "game_name": "Hades",
     "comp_main": 21600, "comp_plus": 79200, "comp_100": 327600},
    {"game_id": 99999, "game_name": "Hades II",
     "comp_main": 0, "comp_plus": 0, "comp_100": 0},
]}


def test_parse_picks_best_title_match():
    with patch("hltb.requests.post", return_value=_fake_response(SAMPLE)):
        result = hltb.fetch_durations("Hades")
    assert result is not None
    assert result["hltb_id"] == "68151"
    assert result["hltb_main_minutes"] == 360          # 21600s / 60
    assert result["hltb_main_extra_minutes"] == 1320   # 79200s / 60
    assert result["hltb_completionist_minutes"] == 5460


def test_no_match_returns_none():
    with patch("hltb.requests.post", return_value=_fake_response({"data": []})):
        assert hltb.fetch_durations("Nonexistent Game 4791") is None


def test_zero_durations_become_none():
    payload = {"data": [{"game_id": 1, "game_name": "Z",
                         "comp_main": 0, "comp_plus": 0, "comp_100": 0}]}
    with patch("hltb.requests.post", return_value=_fake_response(payload)):
        result = hltb.fetch_durations("Z")
    assert result["hltb_id"] == "1"
    assert result["hltb_main_minutes"] is None  # 0 seconds = "unknown", not "0 hours"


def test_network_error_degrades_to_none():
    import requests
    with patch("hltb.requests.post", side_effect=requests.RequestException("boom")):
        assert hltb.fetch_durations("Hades") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_hltb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hltb'`

- [ ] **Step 3: Implement `hltb.py`**

```python
"""HowLongToBeat enrichment. Unofficial JSON endpoint; degrades gracefully.

Never raises on network/parse failure — returns None so eligibility can fall back
to genre rules. Matches by normalized title; reuses models.normalize_title.
"""
from __future__ import annotations

import logging

import requests

from models import normalize_title

logger = logging.getLogger(__name__)

SEARCH_URL = "https://howlongtobeat.com/api/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Game-Tracker)",
    "Referer": "https://howlongtobeat.com/",
    "Content-Type": "application/json",
}
TIMEOUT_SECONDS = 15


def _minutes(seconds) -> int | None:
    """Convert HLTB seconds to whole minutes; 0/None/falsey -> None (unknown)."""
    if not seconds:
        return None
    return int(seconds) // 60


def _best_match(candidates: list[dict], query: str) -> dict | None:
    """Pick the candidate whose normalized name equals the query, else the first."""
    norm_query = normalize_title(query)
    for c in candidates:
        if normalize_title(c.get("game_name", "")) == norm_query:
            return c
    return candidates[0] if candidates else None


def fetch_durations(title: str) -> dict | None:
    """Search HLTB for `title`; return duration dict or None.

    Returns: {"hltb_id", "hltb_main_minutes", "hltb_main_extra_minutes",
              "hltb_completionist_minutes"} or None if no match / error.
    """
    payload = {
        "searchType": "games",
        "searchTerms": title.split(),
        "searchPage": 1,
        "size": 20,
    }
    try:
        resp = requests.post(SEARCH_URL, json=payload, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("HLTB search failed for %r: %s", title, exc)
        return None

    match = _best_match(data, title)
    if not match:
        return None
    return {
        "hltb_id": str(match.get("game_id")),
        "hltb_main_minutes": _minutes(match.get("comp_main")),
        "hltb_main_extra_minutes": _minutes(match.get("comp_plus")),
        "hltb_completionist_minutes": _minutes(match.get("comp_100")),
    }
```

> If the live endpoint shape differs at implementation time (HLTB changes it periodically), adapt the field names (`comp_main`/`comp_plus`/`comp_100`) and request body in `fetch_durations` only — the tests pin the contract `fetch_durations` exposes, not HLTB's wire format. Keep the graceful-degradation behavior.

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_hltb.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `ruff check hltb.py tests/test_hltb.py`

```bash
git add hltb.py tests/test_hltb.py
git commit -m "feat(hltb): HowLongToBeat search/parse with graceful degradation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `hltb.enrich_game` + batch pass (DB write)

**Files:**
- Modify: `hltb.py` (add `enrich_game`, `enrich_missing`)
- Test: `tests/test_hltb_enrich.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hltb_enrich.py
"""enrich_game writes HLTB durations to the games row; batch skips already-enriched."""
from unittest.mock import patch

import models
import hltb


def _seed_game(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_enrich_game_writes_durations(temp_db):
    conn = models.get_db()
    gid = _seed_game(conn, "Hades")
    conn.commit()
    fake = {"hltb_id": "68151", "hltb_main_minutes": 360,
            "hltb_main_extra_minutes": 1320, "hltb_completionist_minutes": 5460}
    with patch("hltb.fetch_durations", return_value=fake):
        ok = hltb.enrich_game(conn, gid)
    assert ok is True
    row = conn.execute("SELECT hltb_id, hltb_main_minutes FROM games WHERE id=?", (gid,)).fetchone()
    assert row["hltb_id"] == "68151"
    assert row["hltb_main_minutes"] == 360
    conn.close()


def test_enrich_game_no_match_leaves_nulls(temp_db):
    conn = models.get_db()
    gid = _seed_game(conn, "Nope")
    conn.commit()
    with patch("hltb.fetch_durations", return_value=None):
        ok = hltb.enrich_game(conn, gid)
    assert ok is False
    row = conn.execute("SELECT hltb_main_minutes FROM games WHERE id=?", (gid,)).fetchone()
    assert row["hltb_main_minutes"] is None
    conn.close()


def test_enrich_missing_skips_already_enriched(temp_db):
    conn = models.get_db()
    g1 = _seed_game(conn, "Hades")
    g2 = _seed_game(conn, "Celeste")
    conn.execute("UPDATE games SET hltb_id='x' WHERE id=?", (g1,))  # already enriched
    conn.commit()
    calls = []
    def fake_enrich(c, gid):
        calls.append(gid)
        return True
    with patch("hltb.enrich_game", side_effect=fake_enrich):
        hltb.enrich_missing(conn)
    assert calls == [g2]  # only the un-enriched game
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_hltb_enrich.py -v`
Expected: FAIL — `AttributeError: module 'hltb' has no attribute 'enrich_game'`

- [ ] **Step 3: Implement in `hltb.py`**

```python
def enrich_game(conn, game_id: int) -> bool:
    """Fetch + persist HLTB durations for one game. Returns True if matched.

    0->1-style: only writes when a match is found; a miss leaves columns untouched.
    Caller owns the transaction (commits).
    """
    title = conn.execute("SELECT title FROM games WHERE id = ?", (game_id,)).fetchone()
    if title is None:
        return False
    result = fetch_durations(title["title"])
    if result is None:
        return False
    conn.execute(
        "UPDATE games SET hltb_id = ?, hltb_main_minutes = ?, "
        "hltb_main_extra_minutes = ?, hltb_completionist_minutes = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (result["hltb_id"], result["hltb_main_minutes"],
         result["hltb_main_extra_minutes"], result["hltb_completionist_minutes"], game_id))
    conn.commit()
    return True


def enrich_missing(conn) -> dict:
    """Enrich every game lacking an hltb_id. Returns {"matched": n, "missed": n}."""
    rows = conn.execute("SELECT id FROM games WHERE hltb_id IS NULL ORDER BY id").fetchall()
    matched = missed = 0
    for row in rows:
        if enrich_game(conn, row["id"]):
            matched += 1
        else:
            missed += 1
    logger.info("HLTB enrich: %d matched, %d missed", matched, missed)
    return {"matched": matched, "missed": missed}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_hltb_enrich.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check hltb.py tests/test_hltb_enrich.py
git add hltb.py tests/test_hltb_enrich.py
git commit -m "feat(hltb): enrich_game + enrich_missing batch pass

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — Eligibility / ranking engine

### Task 8: `slots.py` — per-slot candidate ranking

**Files:**
- Create: `slots.py`
- Test: `tests/test_slots_engine.py`

`rank_candidates(conn, slot, limit)` hard-filters owned-unfinished games by platform + latency, then sorts by a fit score that reuses `recommendation.calculate_tag_affinity` plus a session-window match and a genre-fatigue penalty from recent `slot_history`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slots_engine.py
"""Per-slot eligibility + ranking."""
import json

import models
import slots


def _platform_id(conn, short_name):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (short_name,)).fetchone()[0]


def _add_game(conn, title, platform_short, tags=(), status="backlog",
              hltb_main=None, priority=5):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform_short)))
    for t in tags:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (t,))
        tid = conn.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)", (gid, tid))
    if hltb_main is not None:
        conn.execute("UPDATE games SET hltb_main_minutes=? WHERE id=?", (hltb_main, gid))
    conn.execute("INSERT INTO user_ratings (game_id, status, priority) VALUES (?, ?, ?)",
                 (gid, status, priority))
    conn.commit()
    return gid


def _slot(conn, label):
    row = conn.execute("SELECT * FROM slots WHERE label=?", (label,)).fetchone()
    return dict(row)


def test_platform_hard_filter(temp_db):
    conn = models.get_db()
    sw = _add_game(conn, "Switch Game", "Switch", tags=("Puzzle",))
    _add_game(conn, "PS Game", "PS", tags=("Puzzle",))
    cands = slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
    ids = [c["game"]["id"] for c in cands]
    assert sw in ids
    assert all(c["game"]["title"] != "PS Game" for c in cands)
    conn.close()


def test_low_latency_slot_excludes_lag_sensitive(temp_db):
    conn = models.get_db()
    fighting = _add_game(conn, "Fighter", "PS", tags=("Fighting",))
    turn = _add_game(conn, "Tactics", "PS", tags=("Strategy",))
    # Garage · Console requires low latency -> only the lag-sensitive (Fighting) qualifies
    cands = slots.rank_candidates(conn, _slot(conn, "Garage · Console"))
    ids = [c["game"]["id"] for c in cands]
    assert fighting in ids
    assert turn not in ids
    conn.close()


def test_stream_safe_slot_excludes_lag_sensitive(temp_db):
    conn = models.get_db()
    fighting = _add_game(conn, "Fighter", "PS", tags=("Fighting",))
    turn = _add_game(conn, "Tactics", "PS", tags=("Strategy",))
    cands = slots.rank_candidates(conn, _slot(conn, "Long · Stream-safe"))
    ids = [c["game"]["id"] for c in cands]
    assert turn in ids          # lag-tolerant qualifies
    assert fighting not in ids  # lag-sensitive excluded
    conn.close()


def test_excludes_completed_and_pinned(temp_db):
    conn = models.get_db()
    done = _add_game(conn, "Done", "Switch", tags=("Puzzle",), status="completed")
    pinned = _add_game(conn, "Pinned", "Switch", tags=("Puzzle",))
    conn.execute("UPDATE slots SET current_game_id=? WHERE label='Switch · Long'", (pinned,))
    conn.commit()
    cands = slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
    ids = [c["game"]["id"] for c in cands]
    assert done not in ids       # completed games are not candidates
    assert pinned not in ids     # already slotted elsewhere
    conn.close()


def test_higher_priority_ranks_first(temp_db):
    conn = models.get_db()
    lo = _add_game(conn, "Low", "Switch", tags=("Puzzle",), priority=2)
    hi = _add_game(conn, "High", "Switch", tags=("Puzzle",), priority=9)
    cands = slots.rank_candidates(conn, _slot(conn, "Switch · Quick"))
    assert cands[0]["game"]["id"] == hi
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_slots_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'slots'`

- [ ] **Step 3: Implement `slots.py`** (ranking only — lifecycle comes in Task 9)

```python
"""The Slate engine: per-slot eligibility/ranking + pin/outcome lifecycle.

Ranking reuses recommendation.calculate_tag_affinity for taste signal and layers
slot-specific hard filters (platform, latency) + a session/length fit nudge +
a genre-fatigue penalty from recent slot_history.
"""
from __future__ import annotations

import json

from recommendation import calculate_tag_affinity
from slot_signals import latency_tolerant

# Statuses that mean "not a candidate to start" (already done or actively elsewhere).
FINISHED_STATUSES = frozenset({"completed", "100", "dropped"})
# Recent-history window for genre-fatigue penalty.
FATIGUE_RECENT_COUNT = 5
FATIGUE_PENALTY = 20.0


def _game_tag_names(conn, game_id) -> set[str]:
    rows = conn.execute(
        "SELECT t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id WHERE gt.game_id = ?",
        (game_id,)).fetchall()
    return {r["name"] for r in rows}


def _recent_fatigue_tags(conn) -> set[str]:
    """Tags of the few most-recently-removed slot_history games (genre fatigue)."""
    rows = conn.execute(
        "SELECT game_id FROM slot_history ORDER BY removed_at DESC LIMIT ?",
        (FATIGUE_RECENT_COUNT,)).fetchall()
    tags: set[str] = set()
    for r in rows:
        tags |= _game_tag_names(conn, r["game_id"])
    return tags


def _pinned_game_ids(conn) -> set[int]:
    rows = conn.execute(
        "SELECT current_game_id FROM slots WHERE current_game_id IS NOT NULL").fetchall()
    return {r["current_game_id"] for r in rows}


def rank_candidates(conn, slot: dict, limit: int = 10) -> list[dict]:
    """Return ranked eligible games for a slot: [{"game", "score", "reasons"}]."""
    platforms = set(json.loads(slot["platforms"])) if slot.get("platforms") else set()
    requires_low_latency = bool(slot["requires_low_latency"])
    affinity = calculate_tag_affinity(conn)
    fatigue_tags = _recent_fatigue_tags(conn)
    pinned = _pinned_game_ids(conn)

    placeholders = ",".join("?" * len(FINISHED_STATUSES))
    rows = conn.execute(f"""
        SELECT g.*, ur.status, ur.priority, ur.hours_played
        FROM games g
        JOIN user_ratings ur ON ur.game_id = g.id
        WHERE ur.status NOT IN ({placeholders})
    """, tuple(FINISHED_STATUSES)).fetchall()

    out = []
    for game in rows:
        if game["id"] in pinned:
            continue
        # Platform hard filter
        game_platforms = {
            r["short_name"] for r in conn.execute(
                "SELECT p.short_name FROM game_platforms gp "
                "JOIN platforms p ON p.id = gp.platform_id WHERE gp.game_id = ?",
                (game["id"],)).fetchall()}
        if platforms and not (game_platforms & platforms):
            continue
        # Latency hard filter
        tag_names = _game_tag_names(conn, game["id"])
        tolerant = latency_tolerant(tag_names, game["input_lag_override"])
        if requires_low_latency and tolerant:
            continue          # garage slot wants games that NEED low latency
        if not requires_low_latency and not tolerant:
            continue          # stream-safe / couch slot excludes lag-sensitive games

        score = 50.0
        reasons = []
        priority = game["priority"] or 5
        score += (priority - 5) * 5
        if priority >= 7:
            reasons.append(f"High priority ({priority}/10)")
        # Taste signal from tag affinity
        tag_boost = 0.0
        for t in tag_names:
            for data in affinity.values():
                if data["name"] == t and data["avg_rating"] >= 7:
                    tag_boost += data["score"] * 0.5
        score += min(tag_boost, 15)
        if tag_boost:
            reasons.append("Matches your taste")
        # Genre fatigue penalty
        if tag_names & fatigue_tags:
            score -= FATIGUE_PENALTY
            reasons.append("Similar to what you just finished")

        out.append({"game": dict(game), "score": round(score, 1), "reasons": reasons})

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slots_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check slots.py tests/test_slots_engine.py
git add slots.py tests/test_slots_engine.py
git commit -m "feat(slots): per-slot eligibility + fit ranking

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Lifecycle

### Task 9: pin / outcome transitions

**Files:**
- Modify: `slots.py` (`pin_game`, `apply_outcome`, `get_slots_state`)
- Test: `tests/test_slots_lifecycle.py`

Outcome map: **beat → status `completed`** (then `chase` keeps it slotted with `new_goal`, or `shelve` frees + history `shelved`); **complete → status `100`**, free, history `completed`; **dropped → status `dropped`**, free, history `dropped`; **swap → free, no history, no status change.**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slots_lifecycle.py
import models
import slots


def _add_game(conn, title, status="backlog"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, ?)", (gid, status))
    conn.commit()
    return gid


def _slot_id(conn, label):
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def _status(conn, gid):
    return conn.execute("SELECT status FROM user_ratings WHERE game_id=?", (gid,)).fetchone()[0]


def test_pin_sets_current_game_and_goal(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades")
    sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    row = conn.execute("SELECT current_game_id, goal FROM slots WHERE id=?", (sid,)).fetchone()
    assert row["current_game_id"] == gid
    assert row["goal"] == "beat it"
    conn.close()


def test_beat_then_shelve_frees_slot_and_logs(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades"); sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "beat", chase=False)
    assert _status(conn, gid) == "completed"
    assert conn.execute("SELECT current_game_id FROM slots WHERE id=?", (sid,)).fetchone()[0] is None
    h = conn.execute("SELECT outcome FROM slot_history WHERE game_id=?", (gid,)).fetchone()
    assert h["outcome"] == "shelved"
    conn.close()


def test_beat_then_chase_keeps_slot_and_rewrites_goal(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades"); sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "beat", chase=True, new_goal="get the plat")
    row = conn.execute("SELECT current_game_id, goal FROM slots WHERE id=?", (sid,)).fetchone()
    assert row["current_game_id"] == gid          # stays slotted
    assert row["goal"] == "get the plat"
    assert _status(conn, gid) == "completed"
    assert conn.execute("SELECT COUNT(*) FROM slot_history WHERE game_id=?", (gid,)).fetchone()[0] == 0
    conn.close()


def test_complete_frees_slot_sets_100(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades"); sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "100%")
    slots.apply_outcome(conn, sid, "complete")
    assert _status(conn, gid) == "100"
    assert conn.execute("SELECT outcome FROM slot_history WHERE game_id=?", (gid,)).fetchone()["outcome"] == "completed"
    conn.close()


def test_dropped_frees_slot_sets_dropped(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades"); sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "dropped")
    assert _status(conn, gid) == "dropped"
    assert conn.execute("SELECT outcome FROM slot_history WHERE game_id=?", (gid,)).fetchone()["outcome"] == "dropped"
    conn.close()


def test_swap_frees_slot_no_history_no_status_change(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades", status="playing"); sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "swap")
    assert conn.execute("SELECT current_game_id FROM slots WHERE id=?", (sid,)).fetchone()[0] is None
    assert _status(conn, gid) == "playing"   # unchanged
    assert conn.execute("SELECT COUNT(*) FROM slot_history WHERE game_id=?", (gid,)).fetchone()[0] == 0
    conn.close()


def test_get_slots_state_includes_current_game_and_candidates(temp_db):
    conn = models.get_db()
    state = slots.get_slots_state(conn)
    assert len(state) == 4
    assert "candidates" in state[0]
    assert "current_game" in state[0]
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_slots_lifecycle.py -v`
Expected: FAIL — `AttributeError: module 'slots' has no attribute 'pin_game'`

- [ ] **Step 3: Implement in `slots.py`**

```python
OUTCOME_STATUS = {"beat": "completed", "complete": "100", "dropped": "dropped"}


def pin_game(conn, slot_id: int, game_id: int, goal: str | None = None) -> None:
    """Assign a game (+ goal) to a slot, recording when it was pinned."""
    conn.execute(
        "UPDATE slots SET current_game_id = ?, goal = ? WHERE id = ?",
        (game_id, goal, slot_id))
    conn.commit()


def _set_status(conn, game_id, status):
    conn.execute("""
        INSERT INTO user_ratings (game_id, status) VALUES (?, ?)
        ON CONFLICT(game_id) DO UPDATE SET status = excluded.status,
            updated_at = CURRENT_TIMESTAMP
    """, (game_id, status))


def _log_history(conn, slot_id, game_id, goal, outcome):
    conn.execute(
        "INSERT INTO slot_history (slot_id, game_id, goal, outcome) VALUES (?, ?, ?, ?)",
        (slot_id, game_id, goal, outcome))


def _clear_slot(conn, slot_id):
    conn.execute("UPDATE slots SET current_game_id = NULL, goal = NULL WHERE id = ?", (slot_id,))


def apply_outcome(conn, slot_id: int, outcome: str, *, chase: bool = False,
                  new_goal: str | None = None) -> None:
    """Apply a slot outcome.

    outcome:
      'beat'     -> status 'completed'. chase=True keeps the game slotted with
                    new_goal; chase=False frees the slot + logs history 'shelved'.
      'complete' -> status '100', free slot, history 'completed'.
      'dropped'  -> status 'dropped', free slot, history 'dropped'.
      'swap'     -> free slot, NO history, NO status change.
    """
    slot = conn.execute("SELECT current_game_id, goal FROM slots WHERE id = ?",
                        (slot_id,)).fetchone()
    if slot is None or slot["current_game_id"] is None:
        return
    game_id, goal = slot["current_game_id"], slot["goal"]

    if outcome == "swap":
        _clear_slot(conn, slot_id)
        conn.commit()
        return

    if outcome == "beat":
        _set_status(conn, game_id, OUTCOME_STATUS["beat"])
        if chase:
            conn.execute("UPDATE slots SET goal = ? WHERE id = ?", (new_goal, slot_id))
        else:
            _log_history(conn, slot_id, game_id, goal, "shelved")
            _clear_slot(conn, slot_id)
        conn.commit()
        return

    if outcome in ("complete", "dropped"):
        _set_status(conn, game_id, OUTCOME_STATUS[outcome])
        history_outcome = "completed" if outcome == "complete" else "dropped"
        _log_history(conn, slot_id, game_id, goal, history_outcome)
        _clear_slot(conn, slot_id)
        conn.commit()
        return

    raise ValueError(f"unknown outcome: {outcome!r}")


def get_slots_state(conn, candidate_limit: int = 8) -> list[dict]:
    """Full slate state: each slot dict + its current_game dict + ranked candidates."""
    slot_rows = conn.execute("SELECT * FROM slots ORDER BY sort_order, id").fetchall()
    state = []
    for row in slot_rows:
        slot = dict(row)
        current_game = None
        if slot["current_game_id"]:
            g = conn.execute("SELECT * FROM games WHERE id = ?",
                            (slot["current_game_id"],)).fetchone()
            current_game = dict(g) if g else None
        slot["current_game"] = current_game
        slot["candidates"] = rank_candidates(conn, slot, limit=candidate_limit)
        state.append(slot)
    return state
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_slots_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check slots.py tests/test_slots_lifecycle.py
git add slots.py tests/test_slots_lifecycle.py
git commit -m "feat(slots): pin + outcome lifecycle + get_slots_state

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6 — API

### Task 10: slot read + CRUD routes

**Files:**
- Modify: `app.py` (import `slots`; add routes)
- Test: `tests/test_api_slots.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_slots.py
import json


def test_get_slots_returns_four(client):
    resp = client.get("/api/slots")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["slots"]) == 4
    assert "candidates" in data["slots"][0]


def test_create_slot(client):
    resp = client.post("/api/slots", json={
        "label": "Deck · Anywhere", "platforms": ["Steam"],
        "max_session_minutes": 45, "requires_low_latency": 0,
        "context_notes": "handheld in bed"})
    assert resp.status_code == 201
    assert client.get("/api/slots").get_json()["slots"].__len__() == 5


def test_patch_slot(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    resp = client.patch(f"/api/slots/{sid}", json={"label": "Renamed"})
    assert resp.status_code == 200
    labels = [s["label"] for s in client.get("/api/slots").get_json()["slots"]]
    assert "Renamed" in labels


def test_delete_slot(client):
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    resp = client.delete(f"/api/slots/{sid}")
    assert resp.status_code == 200
    assert len(client.get("/api/slots").get_json()["slots"]) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_api_slots.py -v`
Expected: FAIL — 404s (routes not defined).

- [ ] **Step 3: Implement routes in `app.py`**

Add `import slots` near the other local imports (line ~10, alongside `import dedup`).
Add these routes (place after `api_recommendations`, ~line 1539):

```python
@app.route('/api/slots')
def api_slots():
    """Full slate state: slot definitions + current games + ranked candidates."""
    conn = get_db()
    state = slots.get_slots_state(conn)
    conn.close()
    return jsonify({'slots': state})


@app.route('/api/slots', methods=['POST'])
def api_create_slot():
    """Create a new slot."""
    data = request.get_json() or {}
    conn = get_db()
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM slots").fetchone()[0]
    conn.execute(
        "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
        "min_session_minutes, requires_low_latency, context_notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (data.get('label', 'New slot'), next_order,
         json.dumps(data.get('platforms', [])),
         data.get('max_session_minutes'), data.get('min_session_minutes'),
         1 if data.get('requires_low_latency') else 0, data.get('context_notes')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True}), 201


@app.route('/api/slots/<int:slot_id>', methods=['PATCH'])
def api_update_slot(slot_id):
    """Update a slot's definition (label / constraints / notes)."""
    data = request.get_json() or {}
    fields, params = [], []
    for key in ('label', 'max_session_minutes', 'min_session_minutes', 'context_notes', 'sort_order'):
        if key in data:
            fields.append(f"{key} = ?")
            params.append(data[key])
    if 'platforms' in data:
        fields.append("platforms = ?")
        params.append(json.dumps(data['platforms']))
    if 'requires_low_latency' in data:
        fields.append("requires_low_latency = ?")
        params.append(1 if data['requires_low_latency'] else 0)
    if not fields:
        return jsonify({'error': 'no fields'}), 400
    params.append(slot_id)
    conn = get_db()
    conn.execute(f"UPDATE slots SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>', methods=['DELETE'])
def api_delete_slot(slot_id):
    """Delete a slot definition."""
    conn = get_db()
    conn.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
```

`import json` is already at the top of `app.py`? Confirm; if not, add it.

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_api_slots.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_slots.py
git commit -m "feat(api): slot read + CRUD routes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: pin / outcome / goal / hltb routes

**Files:**
- Modify: `app.py`
- Test: `tests/test_api_slots.py` (extend)

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_api_slots.py
import models


def _add_backlog_game(title):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (gid,))
    conn.commit(); conn.close()
    return gid


def test_pin_and_outcome_flow(client):
    gid = _add_backlog_game("Hades")
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    assert client.post(f"/api/slots/{sid}/pin", json={"game_id": gid, "goal": "beat it"}).status_code == 200
    state = client.get("/api/slots").get_json()["slots"]
    pinned = next(s for s in state if s["id"] == sid)
    assert pinned["current_game"]["id"] == gid
    assert pinned["goal"] == "beat it"
    # complete it -> slot frees
    assert client.post(f"/api/slots/{sid}/outcome", json={"outcome": "complete"}).status_code == 200
    pinned = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert pinned["current_game"] is None


def test_edit_goal(client):
    gid = _add_backlog_game("Celeste")
    sid = client.get("/api/slots").get_json()["slots"][0]["id"]
    client.post(f"/api/slots/{sid}/pin", json={"game_id": gid, "goal": "beat"})
    assert client.patch(f"/api/slots/{sid}/goal", json={"goal": "C-sides"}).status_code == 200
    pinned = next(s for s in client.get("/api/slots").get_json()["slots"] if s["id"] == sid)
    assert pinned["goal"] == "C-sides"


def test_hltb_refresh_route(client, monkeypatch):
    import hltb
    monkeypatch.setattr(hltb, "enrich_missing", lambda conn: {"matched": 3, "missed": 1})
    resp = client.post("/api/hltb/refresh")
    assert resp.status_code == 200
    assert resp.get_json() == {"matched": 3, "missed": 1}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_api_slots.py -k "pin_and_outcome or edit_goal or hltb_refresh" -v`
Expected: FAIL — 404s.

- [ ] **Step 3: Implement routes in `app.py`** (after the CRUD routes; add `import hltb` near the top imports)

```python
@app.route('/api/slots/<int:slot_id>/pin', methods=['POST'])
def api_pin_slot(slot_id):
    """Assign a game (+ goal) to a slot."""
    data = request.get_json() or {}
    game_id = data.get('game_id')
    if not game_id:
        return jsonify({'error': 'game_id required'}), 400
    conn = get_db()
    slots.pin_game(conn, slot_id, game_id, data.get('goal'))
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/outcome', methods=['POST'])
def api_slot_outcome(slot_id):
    """Apply an outcome: beat (+chase/new_goal) / complete / dropped / swap."""
    data = request.get_json() or {}
    outcome = data.get('outcome')
    if outcome not in ('beat', 'complete', 'dropped', 'swap'):
        return jsonify({'error': 'invalid outcome'}), 400
    conn = get_db()
    slots.apply_outcome(conn, slot_id, outcome,
                        chase=bool(data.get('chase')), new_goal=data.get('new_goal'))
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/goal', methods=['PATCH'])
def api_slot_goal(slot_id):
    """Edit the plaintext goal for a slot's current game."""
    data = request.get_json() or {}
    conn = get_db()
    conn.execute("UPDATE slots SET goal = ? WHERE id = ?", (data.get('goal'), slot_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/hltb/refresh', methods=['POST'])
def api_hltb_refresh():
    """Batch-enrich games lacking HLTB durations."""
    conn = get_db()
    result = hltb.enrich_missing(conn)
    conn.close()
    return jsonify(result)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_api_slots.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_slots.py
git commit -m "feat(api): pin / outcome / goal / hltb-refresh routes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 7 — UI

> UI tasks are specified concretely with the exact endpoints + markup structure and verified manually (vanilla-JS template, no JS test harness in this repo). After editing, **stop the app, relaunch, and look at the page** before marking complete.

### Task 12: rebuild `templates/recommendations.html` around the Slate

**Files:**
- Modify: `templates/recommendations.html`

- [ ] **Step 1: Replace the page body + script.** Keep `{% extends "base.html" %}`, the title/nav blocks, and the existing `openModal(...)` usage (defined in `base.html`). Replace `{% block content %}` and `{% block scripts %}` with:

`{% block content %}`:
- A header (`<h1>What Should You Play Next?</h1>` + subtitle "Your slate — one game per context").
- `<div id="slate" class="grid md:grid-cols-2 lg:grid-cols-4 gap-4"></div>` — filled by JS.
- `<div id="recently-finished" class="bg-surface-light rounded-xl p-6 hidden"> … <div id="recently-finished-list"></div></div>`.
- Keep the existing **Needs Rating** section markup verbatim (it stays).
- A "⚙ Slots" button (`onclick="openSlotSettings()"`) and a "↻ Refresh play-times" button (`onclick="refreshHltb()"`).

`{% block scripts %}` — implement these functions (use the existing global `api` helper: `api.get`, and `api.post`/`api.patch`/`api.del` if present; otherwise use `fetch` with `method` + JSON headers — check `static/js` / `base.html` for the helper's exact method names and match them):

```javascript
async function loadSlate() {
    const [{slots}, games] = await Promise.all([
        api.get('/api/slots'),
        api.get('/api/games'),
    ]);
    renderSlate(slots);
    renderRecentlyFinished(slots);   // derived below from a /api/slots field if added, else hide
    const needsRating = games.filter(g =>
        g.status && g.status !== 'backlog' && g.status !== 'wishlist' && !g.rating);
    renderNeedsRating(needsRating);  // keep the existing function
}

function slotChip(slot) {
    const parts = [];
    const plats = JSON.parse(slot.platforms || '[]');
    if (plats.length) parts.push(plats.join('/'));
    if (slot.max_session_minutes) parts.push('<' + slot.max_session_minutes + 'm');
    else if (slot.min_session_minutes) parts.push(slot.min_session_minutes + 'm+');
    if (slot.requires_low_latency) parts.push('low-latency');
    return parts.join(' · ');
}

function renderSlate(slots) {
    const el = document.getElementById('slate');
    el.innerHTML = slots.map(slot => {
        const g = slot.current_game;
        const body = g ? filledSlot(slot, g) : emptySlot(slot);
        return `<div class="bg-surface-light rounded-xl p-4 flex flex-col">
            <div class="flex items-center justify-between mb-3">
                <h3 class="font-medium text-white">${slot.label}</h3>
            </div>
            <p class="text-xs text-gray-500 mb-3">${slotChip(slot)}</p>
            ${body}
        </div>`;
    }).join('');
}

function filledSlot(slot, g) {
    return `
      <div class="flex items-start space-x-3 cursor-pointer" onclick="openModal(${g.id})">
        ${g.cover_url
            ? `<img src="${g.cover_url}" class="w-14 h-14 object-cover rounded">`
            : `<div class="w-14 h-14 cover-placeholder rounded flex items-center justify-center">🎮</div>`}
        <div class="flex-1 min-w-0">
            <p class="text-sm text-white truncate">${g.title}</p>
            <p class="text-xs text-accent">${slot.goal || 'set a goal'}</p>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-2 text-xs">
        <button onclick="slotOutcome(${slot.id}, 'beat')" class="px-2 py-1 bg-surface rounded">Beat</button>
        <button onclick="slotOutcome(${slot.id}, 'complete')" class="px-2 py-1 bg-surface rounded">Complete</button>
        <button onclick="slotOutcome(${slot.id}, 'dropped')" class="px-2 py-1 bg-surface rounded">Dropped</button>
        <button onclick="editGoal(${slot.id})" class="px-2 py-1 bg-surface rounded">Edit goal</button>
        <button onclick="slotOutcome(${slot.id}, 'swap')" class="px-2 py-1 bg-surface rounded">Swap</button>
      </div>`;
}

function emptySlot(slot) {
    const cands = (slot.candidates || []).slice(0, 5);
    return `
      <p class="text-sm text-gray-400 mb-2">Empty — pick one to pin:</p>
      <div class="space-y-1">
        ${cands.map(c => `
          <button onclick="pinGame(${slot.id}, ${c.game.id})"
                  class="w-full text-left text-sm text-gray-300 hover:bg-surface-lighter rounded px-2 py-1 truncate">
            ${c.game.title}
          </button>`).join('') || '<p class="text-xs text-gray-500">No eligible games.</p>'}
      </div>`;
}

async function pinGame(slotId, gameId) {
    const goal = prompt('Goal for this game? (e.g. "beat it", "get the plat")', 'beat it');
    await apiPost(`/api/slots/${slotId}/pin`, {game_id: gameId, goal});
    loadSlate();
}

async function slotOutcome(slotId, outcome) {
    if (outcome === 'beat') {
        const chase = confirm('Beaten! Chase 100%/plat? OK = keep it slotted, Cancel = shelve it.');
        let new_goal = null;
        if (chase) new_goal = prompt('New goal?', 'get the plat');
        await apiPost(`/api/slots/${slotId}/outcome`, {outcome: 'beat', chase, new_goal});
    } else {
        await apiPost(`/api/slots/${slotId}/outcome`, {outcome});
    }
    loadSlate();
}

async function editGoal(slotId) {
    const goal = prompt('New goal?');
    if (goal === null) return;
    await apiPatch(`/api/slots/${slotId}/goal`, {goal});
    loadSlate();
}

async function refreshHltb() {
    const r = await apiPost('/api/hltb/refresh', {});
    alert(`Updated play-times: ${r.matched} matched, ${r.missed} missed.`);
    loadSlate();
}

// Minimal fetch helpers if the global `api` helper lacks post/patch — match the
// helper's names if it has them instead of redefining.
async function apiPost(url, body) {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    return r.json();
}
async function apiPatch(url, body) {
    const r = await fetch(url, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    return r.json();
}

document.addEventListener('DOMContentLoaded', loadSlate);
function refreshGameList() { loadSlate(); }  // keep modal callback contract
```

Keep the existing `renderNeedsRating(games)` function from the old file verbatim. `renderRecentlyFinished` may be a no-op/hide for now (Task 13 adds the data) — leave the section hidden if there is no data.

- [ ] **Step 2: Manual verification**

Stop any running app (PowerShell `Stop-Process` on the python.exe running `app.py`), then:
Run: `uv run python app.py` (background)
Open `http://127.0.0.1:5000/recommendations`. Confirm:
- Four slot cards render with their constraint chips.
- A filled slot shows the game + goal + the five action buttons; an empty slot shows up to five candidate buttons.
- Pinning a candidate, editing a goal, and each outcome button all update the page without a server error (watch the app log).

- [ ] **Step 3: Commit**

```bash
git add templates/recommendations.html
git commit -m "feat(ui): rebuild picks tab around the Slate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: recently-finished strip + slot settings editor

**Files:**
- Modify: `app.py` (add `recently_finished` to `/api/slots` payload OR a small `/api/slots/history` route)
- Modify: `templates/recommendations.html` (render the strip + a slot-settings modal)

- [ ] **Step 1: Add a history read.** In `slots.py`, add:

```python
def recently_finished(conn, limit: int = 6) -> list[dict]:
    """Most-recently removed slot_history rows joined to game title/cover."""
    rows = conn.execute("""
        SELECT h.outcome, h.removed_at, g.id AS game_id, g.title, g.cover_url
        FROM slot_history h JOIN games g ON g.id = h.game_id
        ORDER BY h.removed_at DESC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]
```

Add a test in `tests/test_slots_lifecycle.py`:

```python
def test_recently_finished_lists_outcomes(temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Hades"); sid = _slot_id(conn, "Switch · Quick")
    slots.pin_game(conn, sid, gid, "beat it")
    slots.apply_outcome(conn, sid, "complete")
    rf = slots.recently_finished(conn)
    assert rf[0]["title"] == "Hades"
    assert rf[0]["outcome"] == "completed"
    conn.close()
```

Run: `uv run python -m pytest tests/test_slots_lifecycle.py -k recently_finished -v` → PASS.

Then include it in the `/api/slots` payload in `app.py`:

```python
    return jsonify({'slots': state, 'recently_finished': slots.recently_finished(conn)})
```

(Move the `conn.close()` to after building the payload.)

- [ ] **Step 2: Render the strip + settings modal** in `recommendations.html`:
- `renderRecentlyFinished(data)` — show `data.recently_finished` as a quiet horizontal strip of small cover tiles with an outcome label; hide the section if empty. Update `loadSlate` to read `recently_finished` off the `/api/slots` response.
- `openSlotSettings()` — a modal listing slots with editable label / platforms (checkbox set from `Switch/PS/Xbox/Steam/PC`) / max & min session minutes / low-latency toggle / context notes, plus "Add slot" and "Delete". Save via `apiPost('/api/slots', …)`, `apiPatch('/api/slots/<id>', …)`, `apiDelete('/api/slots/<id>')` (add an `apiDelete` helper mirroring `apiPost`). Reload the slate on save.

- [ ] **Step 3: Manual verification**

Restart the app; on `/recommendations`:
- Complete a slotted game → it appears in "Recently finished."
- Open ⚙ Slots → rename a slot, toggle low-latency, change platforms, save → slate reflects it; add a slot → 5 cards; delete it → back to 4.

- [ ] **Step 4: Commit**

```bash
git add app.py slots.py templates/recommendations.html tests/test_slots_lifecycle.py
git commit -m "feat(ui): recently-finished strip + slot settings editor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 8 — Final validation

### Task 14: full suite, lint, manual smoke

- [ ] **Step 1: Full test suite**

Run: `uv run python -m pytest -q`
Expected: all green (existing + new). Investigate any regression — do not skip.

- [ ] **Step 2: Lint**

Run: `ruff check .`
Expected: no errors. (Never run `ruff format`.)

- [ ] **Step 3: Manual smoke against a COPY of real data (optional but recommended)**

Do NOT test against the live `games.db`. Copy it to a temp path, point `models.DB_PATH` at the copy via an env shim or a throwaway script, run the HLTB batch (`POST /api/hltb/refresh`), and confirm a few known games get sane play-times and the slate fills with plausible candidates.

- [ ] **Step 4: Final commit (if anything was touched in validation)**

```bash
git add -A
git commit -m "chore(slots): Slate foundation (SP1) complete

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (completed by plan author)

**Spec coverage:**
- slots table + user-defined constraints → Tasks 1, 4, 10. ✓
- slot_history memory → Task 2; surfaced Task 13. ✓
- HLTB hours + graceful degradation → Tasks 6, 7. ✓
- session-tolerance / latency lookup tables (extensible) → Task 5. ✓
- platform-friction derived → encoded in slot `platforms` + latency filter (Tasks 5, 8). ✓
- deterministic per-slot ranking reusing recommendation.py → Task 8. ✓
- lifecycle (beat→chase/shelve, complete, dropped, swap) mapped to existing statuses → Task 9. ✓
- API endpoints (GET/POST/PATCH/DELETE slots, pin, outcome, goal, hltb refresh) → Tasks 10, 11. ✓
- picks-tab rebuild + slot settings + recently-finished + Needs-Rating retained → Tasks 12, 13. ✓
- testing per conventions (temp DB, mocked HTTP, ruff check) → throughout; Task 14. ✓
- scope boundary (no AI) → honored; the candidate ranking is the SP2 seam. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code. UI steps give exact endpoints + markup and a manual verification step (no fabricated JS assertions).

**Type/signature consistency:** `fetch_durations`→`enrich_game`→`enrich_missing`; `rank_candidates(conn, slot, limit)`, `pin_game(conn, slot_id, game_id, goal)`, `apply_outcome(conn, slot_id, outcome, *, chase, new_goal)`, `get_slots_state`, `recently_finished` all used consistently across tasks and routes. Outcome vocabulary (`beat/complete/dropped/swap`) and status mapping (`completed/100/dropped`) consistent between Task 9 and Task 11.

**Open implementation-time confirmations (flagged inline):** exact HLTB wire-field names; the global `api` JS helper's method names (reuse if present, else the provided `fetch` helpers); whether `import json` already exists at the top of `app.py`.
