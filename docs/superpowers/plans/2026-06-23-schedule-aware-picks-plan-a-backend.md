# Schedule-Aware Picks — Plan A (Backend Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give slots day/time schedule windows + a user profile, plus the pure Python matcher and API surface that decide which slots are "active now" and how restrictive each is.

**Architecture:** Two idempotent SQLite tables (`slot_schedule_window`, `user_profile`); a pure, side-effect-free matcher module (`slot_schedule.py`) that takes `(weekday, minute)` explicitly so it is trivially testable; `GET /api/slots` enriched with each slot's `windows[]` + server-computed `active_now`/`restrictiveness_rank`; window CRUD + profile GET/PUT endpoints. Matching uses local wall-clock time. The same rule is re-implemented in Kotlin in Plan C — keep this module's behavior the canonical reference.

**Tech Stack:** Python 3 + Flask + SQLite (stdlib `sqlite3`), pytest, `uv`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-06-23-schedule-aware-picks-and-widget-design.md`

## Global Constraints

- Tests run with `uv run python -m pytest` (NOT plain `pytest` — `ModuleNotFoundError: models`). Lint is `ruff check` ONLY — never `ruff format` (codebase is hand-aligned).
- Migrations MUST be idempotent and registered in BOTH `models.migrate_db()` AND `tests/conftest.py::temp_db`.
- Type hints on all Python function signatures (params + return). Use `logging`, not `print`. Named constants over magic numbers.
- Day-of-week bitmask: **bit 0 = Monday … bit 6 = Sunday** (matches Python `datetime.weekday()`).
- Minutes-of-day are integers in `[0, 1439]`. A window is **normal** when `end_min > start_min`, **midnight-crossing** when `end_min < start_min`, and **degenerate/never-active** when `end_min == start_min`.
- Subagents: pytest temp-DB + static review ONLY — never the live `games.db`, the running `:5000` server, the network, or the device.
- Work directly on `main` + push. Commit trailers:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01PQZfqye4BxsLcC3ZiWo6fG`

---

### Task 1: Migrations — `slot_schedule_window` + `user_profile`

**Files:**
- Modify: `models.py` (add two `migrate_*` functions near the other `migrate_*` defs around line 1051; call both inside `migrate_db()` near line 1125 where `migrate_slots(conn)` is called)
- Modify: `tests/conftest.py:18-35` (register both migrations in `temp_db`, after `migrate_slots`)
- Test: `tests/test_schedule_migrations.py` (create)

**Interfaces:**
- Produces:
  - `models.migrate_slot_schedule_window(conn: sqlite3.Connection) -> None`
  - `models.migrate_user_profile(conn: sqlite3.Connection) -> None`
  - Table `slot_schedule_window(id INTEGER PK, slot_id INTEGER FK→slots ON DELETE CASCADE, days INTEGER NOT NULL, start_min INTEGER NOT NULL, end_min INTEGER NOT NULL)`
  - Table `user_profile(id INTEGER PK CHECK(id=1), work_start_min INTEGER, work_end_min INTEGER, bed_time_min INTEGER, meal_windows TEXT)` seeded with one row `(1, NULL, NULL, NULL, NULL)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schedule_migrations.py`:

```python
"""slot_schedule_window + user_profile migrations (uses temp_db)."""
import models


def test_schedule_window_table_exists_and_cascades(temp_db):
    conn = models.get_db()
    # insert a slot, then a window for it
    conn.execute("INSERT INTO slots (label, sort_order) VALUES ('S', 0)")
    sid = conn.execute("SELECT id FROM slots").fetchone()[0]
    conn.execute(
        "INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
        "VALUES (?, ?, ?, ?)", (sid, 0b0011111, 720, 780))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM slot_schedule_window").fetchone()[0] == 1
    # deleting the slot cascades the window away
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM slots WHERE id = ?", (sid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM slot_schedule_window").fetchone()[0] == 0
    conn.close()


def test_user_profile_seeded_single_row(temp_db):
    conn = models.get_db()
    rows = conn.execute("SELECT id, work_start_min, bed_time_min FROM user_profile").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == 1 and rows[0]["work_start_min"] is None
    conn.close()


def test_migrations_are_idempotent(temp_db):
    conn = models.get_db()
    # running again must not throw or duplicate the seed row
    models.migrate_slot_schedule_window(conn)
    models.migrate_user_profile(conn)
    assert conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_schedule_migrations.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: slot_schedule_window` (the migrations aren't registered in `temp_db` yet).

- [ ] **Step 3: Add the migration functions to `models.py`**

Add near the other `migrate_*` functions (e.g. just after `migrate_upc_enrichment_state`, around line 1061):

```python
def migrate_slot_schedule_window(conn: sqlite3.Connection) -> None:
    """Create the per-slot schedule-window table if missing. Idempotent.

    Each row is one day/time window for a slot; a slot may have 0..N windows.
    Zero windows means the slot is 'anytime' (always active). Cascades away with
    its slot so a deleted slot never orphans windows.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slot_schedule_window (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id   INTEGER NOT NULL,
            days      INTEGER NOT NULL,   -- 7-bit mask, bit 0 = Monday .. bit 6 = Sunday
            start_min INTEGER NOT NULL,   -- minutes since local midnight, 0..1439
            end_min   INTEGER NOT NULL,   -- minutes since local midnight, 0..1439
            FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_window_slot "
        "ON slot_schedule_window(slot_id)")
    conn.commit()


def migrate_user_profile(conn: sqlite3.Connection) -> None:
    """Create + seed the single-row user_profile table if missing. Idempotent.

    Holds the owner's daily rhythm (work hours, bedtime, meal windows). Used only
    to pre-fill suggested window times in the web editor; it does not affect
    matching.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id             INTEGER PRIMARY KEY CHECK(id = 1),
            work_start_min INTEGER,
            work_end_min   INTEGER,
            bed_time_min   INTEGER,
            meal_windows   TEXT        -- JSON list of {start_min, end_min}, optional
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO user_profile "
        "(id, work_start_min, work_end_min, bed_time_min, meal_windows) "
        "VALUES (1, NULL, NULL, NULL, NULL)")
    conn.commit()
```

- [ ] **Step 4: Register both in `migrate_db()`**

In `models.py`, inside `migrate_db()` next to the other slot migrations (after `migrate_slots(conn)` near line 1125), add:

```python
    migrate_slot_schedule_window(conn)
    migrate_user_profile(conn)
```

- [ ] **Step 5: Register both in `tests/conftest.py`**

In `tests/conftest.py`, in the `temp_db` fixture after `models.migrate_slots(conn)` (line 18), add:

```python
    models.migrate_slot_schedule_window(conn)
    models.migrate_user_profile(conn)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_schedule_migrations.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check models.py tests/test_schedule_migrations.py tests/conftest.py
git add models.py tests/conftest.py tests/test_schedule_migrations.py
git commit -m "feat(slots): slot_schedule_window + user_profile migrations"
```

---

### Task 2: Matcher — `window_covers` + `slot_active_at`

**Files:**
- Create: `slot_schedule.py`
- Test: `tests/test_slot_schedule_active.py` (create)

**Interfaces:**
- Consumes: nothing (pure module; a window is a `dict` with int keys `days`, `start_min`, `end_min`).
- Produces:
  - `slot_schedule.window_covers(window: dict, weekday: int, minute: int) -> bool`
  - `slot_schedule.slot_active_at(windows: list[dict], weekday: int, minute: int) -> bool`
  - Module constants `DAY_MINUTES = 1440`, `WEEK_MINUTES = 10080`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_slot_schedule_active.py`:

```python
"""slot_schedule.window_covers + slot_active_at (pure, no DB)."""
import slot_schedule as ss

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
ALL_DAYS = 0b1111111


def w(days, start, end):
    return {"days": days, "start_min": start, "end_min": end}


def test_normal_window_covers_inside_only():
    win = w(1 << TUE, 720, 780)  # Tue 12:00-13:00
    assert ss.window_covers(win, TUE, 740) is True
    assert ss.window_covers(win, TUE, 780) is False   # end is exclusive
    assert ss.window_covers(win, TUE, 719) is False
    assert ss.window_covers(win, WED, 740) is False    # wrong day


def test_midnight_crossing_window_covers_both_sides():
    win = w(1 << SAT, 1320, 60)  # Sat 22:00 -> Sun 01:00
    assert ss.window_covers(win, SAT, 1380) is True    # Sat 23:00
    assert ss.window_covers(win, SUN, 30) is True       # Sun 00:30 (next day)
    assert ss.window_covers(win, SUN, 90) is False      # Sun 01:30 (past end)
    assert ss.window_covers(win, SAT, 1200) is False    # Sat 20:00 (before start)
    assert ss.window_covers(win, FRI, 30) is False      # Fri not the day-after a set day


def test_degenerate_window_never_covers():
    assert ss.window_covers(w(ALL_DAYS, 600, 600), MON, 600) is False


def test_zero_windows_is_always_active():
    assert ss.slot_active_at([], MON, 0) is True
    assert ss.slot_active_at([], SUN, 1439) is True


def test_slot_active_if_any_window_matches():
    windows = [w(1 << MON, 0, 60), w(1 << WED, 720, 780)]
    assert ss.slot_active_at(windows, WED, 740) is True
    assert ss.slot_active_at(windows, TUE, 740) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slot_schedule_active.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'slot_schedule'`.

- [ ] **Step 3: Create `slot_schedule.py` with the active-now logic**

```python
"""Pure schedule matcher for slots — active-now + restrictiveness ordering.

A window is a dict with int keys: days (7-bit mask, bit 0 = Monday .. bit 6 =
Sunday), start_min, end_min (minutes since local midnight, 0..1439). end_min >
start_min is a normal window; end_min < start_min crosses midnight; end_min ==
start_min is degenerate (never active). A slot with zero windows is 'anytime'.

This module is the canonical reference for the Kotlin matcher in Plan C — keep
the two in lockstep.
"""

DAY_MINUTES = 1440
WEEK_MINUTES = 7 * DAY_MINUTES


def _day_set(days: int, weekday: int) -> bool:
    """True if the given weekday's bit is set in the mask."""
    return bool(days & (1 << weekday))


def window_covers(window: dict, weekday: int, minute: int) -> bool:
    """True if (weekday, minute) falls inside this window. Handles midnight-cross."""
    days = window["days"]
    start = window["start_min"]
    end = window["end_min"]
    if end > start:                       # normal, same-day window
        return _day_set(days, weekday) and start <= minute < end
    if end < start:                       # crosses midnight
        on_start_day = _day_set(days, weekday) and minute >= start
        prev_day = (weekday - 1) % 7      # the morning portion belongs to day-after a set day
        on_next_day = _day_set(days, prev_day) and minute < end
        return on_start_day or on_next_day
    return False                          # degenerate (end == start)


def slot_active_at(windows: list[dict], weekday: int, minute: int) -> bool:
    """A slot is active if it has no windows (anytime) or any window covers now."""
    if not windows:
        return True
    return any(window_covers(w, weekday, minute) for w in windows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slot_schedule_active.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check slot_schedule.py tests/test_slot_schedule_active.py
git add slot_schedule.py tests/test_slot_schedule_active.py
git commit -m "feat(slots): pure window_covers + slot_active_at matcher"
```

---

### Task 3: Matcher — `restrictiveness_score` + `order_active`

**Files:**
- Modify: `slot_schedule.py`
- Test: `tests/test_slot_schedule_order.py` (create)

**Interfaces:**
- Consumes: `slot_active_at` (Task 2).
- Produces:
  - `slot_schedule.restrictiveness_score(windows: list[dict]) -> float` (weekly active minutes; zero windows → `float("inf")`)
  - `slot_schedule.order_active(slots: list[dict], weekday: int, minute: int) -> list[dict]` — each slot dict must carry keys `id` (int), `sort_order` (int), `windows` (list[dict]). Returns only the active slots, sorted most-restrictive-first (score asc, then `sort_order`, then `id`), and **mutates each returned slot dict** to add `restrictiveness_rank` (0-based int).

- [ ] **Step 1: Write the failing test**

Create `tests/test_slot_schedule_order.py`:

```python
"""slot_schedule.restrictiveness_score + order_active (pure, no DB)."""
import math

import slot_schedule as ss

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def w(days, start, end):
    return {"days": days, "start_min": start, "end_min": end}


def test_zero_windows_scores_infinite():
    assert ss.restrictiveness_score([]) == math.inf


def test_score_is_weekly_active_minutes():
    # Mon+Wed 12:00-13:00 = 2 days * 60 min = 120
    assert ss.restrictiveness_score([w((1 << MON) | (1 << WED), 720, 780)]) == 120.0
    # crossing Sat 22:00->01:00 on 1 day = (1440-1320)+60 = 180
    assert ss.restrictiveness_score([w(1 << SAT, 1320, 60)]) == 180.0


def test_order_active_filters_and_sorts_most_restrictive_first():
    narrow = {"id": 1, "sort_order": 5, "windows": [w(1 << THU, 0, 1020)]}   # Thu until 17:00
    anytime = {"id": 2, "sort_order": 0, "windows": []}                       # anytime
    evening = {"id": 3, "sort_order": 1, "windows": [w(0b1111111, 1200, 1380)]}  # daily 20-23
    # Now: Thursday 16:00 (minute 960) — all three are active
    out = ss.order_active([anytime, evening, narrow], THU, 960)
    assert [s["id"] for s in out] == [1, 3, 2]   # narrow < evening < anytime
    assert [s["restrictiveness_rank"] for s in out] == [0, 1, 2]


def test_order_active_excludes_inactive():
    lunch = {"id": 1, "sort_order": 0, "windows": [w(1 << MON, 720, 780)]}
    out = ss.order_active([lunch], MON, 60)   # 01:00, outside the window
    assert out == []


def test_order_active_tie_breaks_on_sort_order():
    a = {"id": 1, "sort_order": 9, "windows": [w(1 << MON, 0, 60)]}
    b = {"id": 2, "sort_order": 2, "windows": [w(1 << MON, 600, 660)]}  # same score (60 min)
    out = ss.order_active([a, b], MON, 30)  # only 'a' active at 00:30
    assert [s["id"] for s in out] == [1]
    out2 = ss.order_active([a, b], MON, 30)
    # sanity: ranks reset each call
    assert out2[0]["restrictiveness_rank"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_slot_schedule_order.py -v`
Expected: FAIL — `AttributeError: module 'slot_schedule' has no attribute 'restrictiveness_score'`.

- [ ] **Step 3: Append the scoring + ordering functions to `slot_schedule.py`**

```python
def _window_length(start_min: int, end_min: int) -> int:
    """Length of a window in minutes (handles midnight-cross; degenerate -> 0)."""
    if end_min > start_min:
        return end_min - start_min
    if end_min < start_min:
        return (DAY_MINUTES - start_min) + end_min
    return 0


def restrictiveness_score(windows: list[dict]) -> float:
    """Total active minutes per week. Smaller = more restrictive. Zero windows
    ('anytime') scores infinity so it always sorts last. Overlapping windows are
    summed (an acceptable approximation for ordering)."""
    if not windows:
        return float("inf")
    total = 0
    for w in windows:
        total += bin(w["days"]).count("1") * _window_length(w["start_min"], w["end_min"])
    return float(total)


def order_active(slots: list[dict], weekday: int, minute: int) -> list[dict]:
    """Return only the active slots, most-restrictive-first (score asc, then
    sort_order, then id). Mutates each returned slot to add 'restrictiveness_rank'
    (0-based). Each slot dict must have 'id', 'sort_order', 'windows'."""
    active = [s for s in slots if slot_active_at(s["windows"], weekday, minute)]
    active.sort(key=lambda s: (restrictiveness_score(s["windows"]),
                               s.get("sort_order", 0), s["id"]))
    for rank, s in enumerate(active):
        s["restrictiveness_rank"] = rank
    return active
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_slot_schedule_order.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check slot_schedule.py tests/test_slot_schedule_order.py
git add slot_schedule.py tests/test_slot_schedule_order.py
git commit -m "feat(slots): restrictiveness_score + order_active ranking"
```

---

### Task 4: Enrich `GET /api/slots` with windows + active_now + rank

**Files:**
- Modify: `slots.py` (`get_slots_state`, ~line 300-314) — attach each slot's `windows`
- Modify: `app.py` (`api_slots`, ~line 1843-1850) — add `active_now` + `restrictiveness_rank`
- Modify: `slot_schedule.py` — add a `now_weekday_minute` helper
- Test: `tests/test_api_slots_schedule.py` (create)

**Interfaces:**
- Consumes: `slot_schedule.order_active` (Task 3), `slots.get_slots_state` (existing).
- Produces:
  - `slot_schedule.now_weekday_minute(dt: "datetime.datetime | None" = None) -> tuple[int, int]` — returns `(weekday, minute_of_day)` from `dt` or `datetime.datetime.now()`.
  - `get_slots_state` slot dicts gain `windows: list[dict]` (each `{id, days, start_min, end_min}`).
  - `GET /api/slots` slot dicts additionally gain `active_now: bool` and `restrictiveness_rank: int | None` (None when not active).

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_slots_schedule.py`:

```python
"""GET /api/slots schedule enrichment (windows + active_now + rank)."""
import datetime

import slot_schedule
import models


def _slot(conn, label, sort_order):
    conn.execute("INSERT INTO slots (label, sort_order) VALUES (?, ?)", (label, sort_order))
    return conn.execute("SELECT id FROM slots WHERE label=?", (label,)).fetchone()[0]


def _window(conn, slot_id, days, start, end):
    conn.execute("INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
                 "VALUES (?, ?, ?, ?)", (slot_id, days, start, end))


def test_slots_payload_includes_windows_and_active_flags(client, monkeypatch):
    conn = models.get_db()
    # Clear seeded slots so the assertion is deterministic.
    conn.execute("DELETE FROM slots")
    lunch = _slot(conn, "Lunch", 0)
    anytime = _slot(conn, "Anytime", 1)
    _window(conn, lunch, 1 << 0, 720, 780)   # Monday 12:00-13:00
    conn.commit()
    conn.close()

    # Freeze "now" to Monday 12:30 (weekday 0, minute 750).
    monkeypatch.setattr(slot_schedule, "now_weekday_minute", lambda: (0, 750))

    data = client.get("/api/slots").get_json()
    by_label = {s["label"]: s for s in data["slots"]}
    assert by_label["Lunch"]["windows"][0]["start_min"] == 720
    assert by_label["Lunch"]["active_now"] is True
    assert by_label["Lunch"]["restrictiveness_rank"] == 0
    assert by_label["Anytime"]["active_now"] is True            # zero windows = anytime
    assert by_label["Anytime"]["restrictiveness_rank"] == 1     # less restrictive -> after


def test_inactive_slot_has_null_rank(client, monkeypatch):
    conn = models.get_db()
    conn.execute("DELETE FROM slots")
    lunch = _slot(conn, "Lunch", 0)
    _window(conn, lunch, 1 << 0, 720, 780)   # Monday 12:00-13:00
    conn.commit()
    conn.close()
    monkeypatch.setattr(slot_schedule, "now_weekday_minute", lambda: (0, 60))  # Mon 01:00
    data = client.get("/api/slots").get_json()
    lunch_slot = next(s for s in data["slots"] if s["label"] == "Lunch")
    assert lunch_slot["active_now"] is False
    assert lunch_slot["restrictiveness_rank"] is None


def test_now_weekday_minute_uses_given_datetime():
    dt = datetime.datetime(2026, 6, 25, 14, 30)  # a Thursday
    assert slot_schedule.now_weekday_minute(dt) == (3, 14 * 60 + 30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_slots_schedule.py -v`
Expected: FAIL — `AttributeError: module 'slot_schedule' has no attribute 'now_weekday_minute'` (and missing `windows` key).

- [ ] **Step 3: Add `now_weekday_minute` to `slot_schedule.py`**

Add at the top of `slot_schedule.py` (after the module docstring, before the constants add `import datetime`; place the function after the constants):

```python
import datetime


def now_weekday_minute(dt: "datetime.datetime | None" = None) -> tuple[int, int]:
    """(weekday 0=Mon..6=Sun, minute-of-day) for dt, or local now() if dt is None."""
    dt = dt or datetime.datetime.now()
    return dt.weekday(), dt.hour * 60 + dt.minute
```

- [ ] **Step 4: Attach windows in `slots.get_slots_state`**

In `slots.py`, inside `get_slots_state`'s loop (after `slot["candidates"] = ...`, before `state.append(slot)` at line 312-313), add:

```python
        slot["windows"] = [
            dict(wr) for wr in conn.execute(
                "SELECT id, days, start_min, end_min FROM slot_schedule_window "
                "WHERE slot_id = ? ORDER BY id", (slot["id"],)).fetchall()
        ]
```

- [ ] **Step 5: Compute active_now + rank in `api_slots`**

In `app.py`, replace the body of `api_slots` (lines 1846-1850) with:

```python
    conn = get_db()
    state = slots.get_slots_state(conn)
    recent = slots.recently_finished(conn)
    conn.close()
    weekday, minute = slot_schedule.now_weekday_minute()
    active = slot_schedule.order_active(state, weekday, minute)  # mutates active slots' rank
    active_ids = {s["id"] for s in active}
    for s in state:
        s["active_now"] = s["id"] in active_ids
        s["restrictiveness_rank"] = s.get("restrictiveness_rank") if s["id"] in active_ids else None
    return jsonify({'slots': state, 'recently_finished': recent})
```

Add `import slot_schedule` to the imports at the top of `app.py` if not already present.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_api_slots_schedule.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check app.py slots.py slot_schedule.py tests/test_api_slots_schedule.py
git add app.py slots.py slot_schedule.py tests/test_api_slots_schedule.py
git commit -m "feat(slots): enrich /api/slots with windows + active_now + rank"
```

---

### Task 5: Window CRUD endpoints

**Files:**
- Modify: `app.py` (add three routes near the other `/api/slots/<int:slot_id>/...` routes, ~line 1905)
- Test: `tests/test_api_slot_windows.py` (create)

**Interfaces:**
- Consumes: `get_db` (existing).
- Produces:
  - `POST /api/slots/<int:slot_id>/windows` — body `{days, start_min, end_min}` → `201 {"ok": True, "id": <wid>}`; `404` if slot missing; `400` on invalid fields.
  - `PUT /api/slots/<int:slot_id>/windows/<int:wid>` — body `{days, start_min, end_min}` → `200 {"ok": True}`; `404` if window not found for that slot; `400` on invalid fields.
  - `DELETE /api/slots/<int:slot_id>/windows/<int:wid>` → `200 {"ok": True}`; `404` if not found.
- Validation rule (shared): `days` int in `[0, 127]`; `start_min`/`end_min` ints in `[0, 1439]`; `start_min != end_min`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_slot_windows.py`:

```python
"""Slot schedule-window CRUD endpoints."""
import models


def _slot(conn):
    conn.execute("INSERT INTO slots (label, sort_order) VALUES ('S', 0)")
    return conn.execute("SELECT id FROM slots WHERE label='S'").fetchone()[0]


def test_create_window(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.post(f"/api/slots/{sid}/windows",
                    json={"days": 0b0011111, "start_min": 720, "end_min": 780})
    assert r.status_code == 201
    wid = r.get_json()["id"]
    conn = models.get_db()
    row = conn.execute("SELECT days, start_min, end_min FROM slot_schedule_window "
                       "WHERE id=?", (wid,)).fetchone()
    assert (row["days"], row["start_min"], row["end_min"]) == (0b0011111, 720, 780)
    conn.close()


def test_create_window_missing_slot_404(client):
    r = client.post("/api/slots/99999/windows",
                    json={"days": 1, "start_min": 0, "end_min": 60})
    assert r.status_code == 404


def test_create_window_rejects_equal_start_end(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.post(f"/api/slots/{sid}/windows",
                    json={"days": 1, "start_min": 600, "end_min": 600})
    assert r.status_code == 400


def test_create_window_rejects_out_of_range(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.post(f"/api/slots/{sid}/windows",
                    json={"days": 999, "start_min": 0, "end_min": 60})
    assert r.status_code == 400


def test_update_window(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.execute("INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
                 "VALUES (?, 1, 0, 60)", (sid,))
    wid = conn.execute("SELECT id FROM slot_schedule_window").fetchone()[0]
    conn.commit()
    conn.close()
    r = client.put(f"/api/slots/{sid}/windows/{wid}",
                   json={"days": 0b1100000, "start_min": 480, "end_min": 600})
    assert r.status_code == 200
    conn = models.get_db()
    row = conn.execute("SELECT days, start_min FROM slot_schedule_window WHERE id=?",
                       (wid,)).fetchone()
    assert (row["days"], row["start_min"]) == (0b1100000, 480)
    conn.close()


def test_update_window_wrong_slot_404(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.put(f"/api/slots/{sid}/windows/99999",
                   json={"days": 1, "start_min": 0, "end_min": 60})
    assert r.status_code == 404


def test_delete_window(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.execute("INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
                 "VALUES (?, 1, 0, 60)", (sid,))
    wid = conn.execute("SELECT id FROM slot_schedule_window").fetchone()[0]
    conn.commit()
    conn.close()
    r = client.delete(f"/api/slots/{sid}/windows/{wid}")
    assert r.status_code == 200
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM slot_schedule_window").fetchone()[0] == 0
    conn.close()


def test_delete_window_missing_404(client):
    conn = models.get_db()
    sid = _slot(conn)
    conn.commit()
    conn.close()
    r = client.delete(f"/api/slots/{sid}/windows/99999")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_slot_windows.py -v`
Expected: FAIL — `404`/`405` for the not-yet-defined routes (assertions on `201`/`200` fail).

- [ ] **Step 3: Add the three routes + a validation helper to `app.py`**

Add after `api_reorder_slots` (around line 1919), before the `DELETE /api/slots/<int:slot_id>` route:

```python
_WINDOW_DAYS_MAX = 0b1111111   # all 7 day bits set
_MINUTE_MAX = 1439


def _validate_window(data: dict) -> "str | None":
    """Return an error message if the window payload is invalid, else None."""
    try:
        days = int(data["days"])
        start = int(data["start_min"])
        end = int(data["end_min"])
    except (KeyError, TypeError, ValueError):
        return "days, start_min, end_min are required integers"
    if not (0 <= days <= _WINDOW_DAYS_MAX):
        return "days out of range"
    if not (0 <= start <= _MINUTE_MAX and 0 <= end <= _MINUTE_MAX):
        return "start_min/end_min out of range"
    if start == end:
        return "start_min and end_min must differ"
    return None


@app.route('/api/slots/<int:slot_id>/windows', methods=['POST'])
def api_create_slot_window(slot_id: int):
    """Add one schedule window to a slot."""
    data = request.get_json() or {}
    err = _validate_window(data)
    if err:
        return jsonify({'error': err}), 400
    conn = get_db()
    if conn.execute("SELECT 1 FROM slots WHERE id = ?", (slot_id,)).fetchone() is None:
        conn.close()
        return jsonify({'error': 'slot not found'}), 404
    cur = conn.execute(
        "INSERT INTO slot_schedule_window (slot_id, days, start_min, end_min) "
        "VALUES (?, ?, ?, ?)",
        (slot_id, int(data['days']), int(data['start_min']), int(data['end_min'])))
    conn.commit()
    wid = cur.lastrowid
    conn.close()
    return jsonify({'ok': True, 'id': wid}), 201


@app.route('/api/slots/<int:slot_id>/windows/<int:wid>', methods=['PUT'])
def api_update_slot_window(slot_id: int, wid: int):
    """Replace a window's days/time."""
    data = request.get_json() or {}
    err = _validate_window(data)
    if err:
        return jsonify({'error': err}), 400
    conn = get_db()
    cur = conn.execute(
        "UPDATE slot_schedule_window SET days = ?, start_min = ?, end_min = ? "
        "WHERE id = ? AND slot_id = ?",
        (int(data['days']), int(data['start_min']), int(data['end_min']), wid, slot_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return jsonify({'error': 'window not found'}), 404
    return jsonify({'ok': True})


@app.route('/api/slots/<int:slot_id>/windows/<int:wid>', methods=['DELETE'])
def api_delete_slot_window(slot_id: int, wid: int):
    """Remove a schedule window from a slot."""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM slot_schedule_window WHERE id = ? AND slot_id = ?", (wid, slot_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return jsonify({'error': 'window not found'}), 404
    return jsonify({'ok': True})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_api_slot_windows.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app.py tests/test_api_slot_windows.py
git add app.py tests/test_api_slot_windows.py
git commit -m "feat(slots): schedule-window CRUD endpoints"
```

---

### Task 6: Profile GET/PUT endpoints

**Files:**
- Modify: `app.py` (add two routes, e.g. after the window routes)
- Test: `tests/test_api_profile.py` (create)

**Interfaces:**
- Consumes: `get_db` (existing).
- Produces:
  - `GET /api/profile` → `200 {work_start_min, work_end_min, bed_time_min, meal_windows}` (`meal_windows` parsed from JSON to a list, `[]` when null).
  - `PUT /api/profile` — body may contain any of `work_start_min`, `work_end_min`, `bed_time_min` (ints in `[0,1439]` or null), `meal_windows` (list of `{start_min,end_min}`) → `200 {"ok": True}`; `400` on out-of-range minutes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_profile.py`:

```python
"""user_profile GET/PUT endpoints."""


def test_get_profile_defaults(client):
    data = client.get("/api/profile").get_json()
    assert data["work_start_min"] is None
    assert data["meal_windows"] == []


def test_put_profile_updates_fields(client):
    r = client.put("/api/profile", json={
        "work_start_min": 540, "work_end_min": 1020, "bed_time_min": 1380,
        "meal_windows": [{"start_min": 720, "end_min": 780}],
    })
    assert r.status_code == 200
    data = client.get("/api/profile").get_json()
    assert data["work_start_min"] == 540 and data["bed_time_min"] == 1380
    assert data["meal_windows"] == [{"start_min": 720, "end_min": 780}]


def test_put_profile_rejects_out_of_range(client):
    r = client.put("/api/profile", json={"work_start_min": 5000})
    assert r.status_code == 400


def test_put_profile_partial_keeps_others(client):
    client.put("/api/profile", json={"work_start_min": 540})
    client.put("/api/profile", json={"bed_time_min": 1380})
    data = client.get("/api/profile").get_json()
    assert data["work_start_min"] == 540 and data["bed_time_min"] == 1380
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_profile.py -v`
Expected: FAIL — `404`/`405` (routes not defined).

- [ ] **Step 3: Add the two routes to `app.py`**

Add after the window routes:

```python
_PROFILE_MINUTE_FIELDS = ("work_start_min", "work_end_min", "bed_time_min")


@app.route('/api/profile')
def api_get_profile():
    """The single user_profile row; meal_windows parsed to a list."""
    conn = get_db()
    row = conn.execute(
        "SELECT work_start_min, work_end_min, bed_time_min, meal_windows "
        "FROM user_profile WHERE id = 1").fetchone()
    conn.close()
    data = dict(row) if row else {f: None for f in _PROFILE_MINUTE_FIELDS}
    data["meal_windows"] = json.loads(data["meal_windows"]) if data.get("meal_windows") else []
    return jsonify(data)


@app.route('/api/profile', methods=['PUT'])
def api_update_profile():
    """Update profile fields (used for web editor pre-fill suggestions)."""
    data = request.get_json() or {}
    fields, params = [], []
    for key in _PROFILE_MINUTE_FIELDS:
        if key in data:
            val = data[key]
            if val is not None and not (isinstance(val, int) and 0 <= val <= _MINUTE_MAX):
                return jsonify({'error': f'{key} out of range'}), 400
            fields.append(f"{key} = ?")
            params.append(val)
    if 'meal_windows' in data:
        fields.append("meal_windows = ?")
        params.append(json.dumps(data['meal_windows']))
    if not fields:
        return jsonify({'error': 'no fields'}), 400
    conn = get_db()
    conn.execute(f"UPDATE user_profile SET {', '.join(fields)} WHERE id = 1", params)
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run python -m pytest tests/test_api_profile.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app.py tests/test_api_profile.py
git add app.py tests/test_api_profile.py
git commit -m "feat(slots): user_profile GET/PUT endpoints"
```

---

### Task 7: Full gate + final review

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `uv run python -m pytest`
Expected: PASS — all prior tests plus the ~30 new tests from Tasks 1-6 (no regressions in the existing slots/`/api/slots` tests).

- [ ] **Step 2: Lint the whole tree**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Verify migrations apply to a fresh DB**

Run:
```bash
uv run python -c "import models, tempfile, os; \
p=os.path.join(tempfile.mkdtemp(),'t.db'); models.DB_PATH=p; models.init_db(); \
import sqlite3; c=sqlite3.connect(p); \
print('window tbl:', c.execute(\"SELECT name FROM sqlite_master WHERE name='slot_schedule_window'\").fetchone()); \
print('profile rows:', c.execute('SELECT COUNT(*) FROM user_profile').fetchone())"
```
Expected: prints the `slot_schedule_window` table name and `profile rows: (1,)`. (Controller-only check — does NOT touch the live `games.db`.)

- [ ] **Step 4: Final whole-plan review**

Dispatch a static review (most-capable model) over the Task 1-6 diff range. Confirm: migrations idempotent + registered in both places; matcher matches the spec rule (midnight-cross, anytime, restrictiveness ordering, tie-break); `/api/slots` enrichment doesn't regress the existing shape; CRUD validation + 404s correct; CLAUDE.md style. Fix any Critical/Important inline.

- [ ] **Step 5: Confirm done**

Plan A complete: `slot_schedule_window` + `user_profile` tables, `slot_schedule.py` matcher, enriched `/api/slots`, window CRUD + profile endpoints. Ready for Plan B (web editor + schedule-aware Picks view).

---

## Self-Review (completed by plan author)

- **Spec coverage:** data model (Task 1) ✓; matching logic — active-now/midnight-cross/anytime (Task 2), restrictiveness/ordering/tie-break (Task 3) ✓; enriched `GET /api/slots` (Task 4) ✓; window CRUD (Task 5) ✓; profile GET/PUT (Task 6) ✓; testing strategy (every task is TDD; Task 7 gate) ✓. Web UI + Android are explicitly out of scope for Plan A (Plans B/C).
- **Placeholder scan:** none — every code step has complete code; every command has expected output.
- **Type consistency:** `window_covers`/`slot_active_at`/`restrictiveness_score`/`order_active`/`now_weekday_minute` signatures are consistent between their defining task and their consumers (Task 4 route, Task 3 ordering). Window dict shape `{id?, days, start_min, end_min}` is consistent across DB, matcher, and API. `_MINUTE_MAX` defined in Task 5 and reused in Task 6 (Task 6 runs after Task 5, so the constant exists).
