# Schedule-Aware Picks & Home-Screen Widget — Design

**Date:** 2026-06-23
**Status:** Approved (brainstorming complete; pending spec review)
**Supersedes the original Android "Phase 4 = Glance widget" framing:** the widget is now the headline *surface* of a larger schedule-aware picks capability.

## Problem / Goal

The owner's core friction is backlog paralysis inside a constrained daily rhythm (work hours, kids, a ~9pm window, occasional micro-sessions at lunch / on the toilet). A plain widget showing "your picks" doesn't answer the real question: **"given it's *this* day and *this* time, what should I actually play right now?"**

So slots become **schedule-aware**. Each slot can declare *when* it applies (day-of-week + time windows). The system determines which slots are **active right now**, orders them **most-restrictive-first**, and surfaces the **primary** (top) active slot's game on a home-screen widget. Tapping the widget opens the app's Picks view, where the full priority-ordered list lives.

## Decisions (from brainstorming)

- **Slot schedules drive the match** (not an inferred-free-time model). Each slot owns its schedule.
- **Schedule shape:** a slot has **0..N windows**; each window = a set of weekdays (Mon–Sun checkboxes) + a start/end time. Maximum flexibility (e.g. one slot = Mon/Wed/Fri 12:00–13:00 **and** Sat/Sun 08:00–10:00).
- **Overlap / multiple active slots:** ordered **most-restrictive-first**. "Restrictiveness" = how little of the week the slot is active (smaller weekly coverage = more restrictive = first). A slot with **no windows = "anytime"** (always active, ordered last). This keeps every existing slot working unchanged.
- **Widget shows only the primary** (single most-restrictive active slot that has an assigned game). **Tap → opens the app's Picks view**, which shows the matching slots sorted by priority. No swipe/carousel in the widget.
- **Framework = Jetpack Glance** (single-card widget — no horizontal-swipe requirement, so Glance's lack of `LazyRow`/fling is a non-issue; classic `StackView` was considered for true swipe but rejected as unnecessary boilerplate once the widget became a single card).
- **User profile** (work hours / meals / bedtime) is **convenience-only in v1**: it pre-fills suggested window times in the web editor. It does **not** gate or alter matching.
- **Editing is web-only (canonical).** The Android app and widget are **readers** (matches the standing "web-main, mobile-streamlined" principle; complex multi-window editing is far easier on a real screen).

## Architecture — where "active now" is computed

**Hybrid (chosen).** The backend is the source of truth for slots + raw schedule windows + assignments, and computes active/ordering server-side for the **web** Picks view. **Android** caches the raw windows and runs a **small pure-Kotlin matcher** (active-now + restrictiveness) used by **both the widget and the app Picks view**, driven off the **phone's clock**.

Rationale: the backend is LAN/VPN-only, so a backend-resolver-only widget would go stale and break the moment the phone is off-network. On-device matching keeps the widget **correct minute-to-minute and functional offline**. The matching rule is tiny, so a parallel Python + Kotlin implementation — each independently unit-tested, with mirrored test cases to keep them in lockstep — is a deliberate, acceptable trade.

Alternatives rejected: pure backend resolver (simplest clients, but stale/offline-broken widget); pure on-device (no reuse for the web Picks view).

## Data model (backend, SQLite)

- **`slot_schedule_window`**: `id` (PK), `slot_id` (FK → slots, cascade delete), `days` (INTEGER, 7-bit mask, bit 0 = Monday … bit 6 = Sunday), `start_min` (INTEGER 0–1439, minutes since local midnight), `end_min` (INTEGER 0–1439). A slot has 0..N rows. **Zero rows = "anytime."**
- **`user_profile`**: single-row table (seeded with id=1, like `upc_enrichment_state`), nullable columns: `work_start_min`, `work_end_min`, `bed_time_min`, `meal_windows` (JSON list of `{start_min,end_min}`, optional). Used only for web editor pre-fill suggestions.
- Migrations are **idempotent** and registered in **both** `models.migrate_db()` and `tests/conftest.py::temp_db`.

## Matching logic (the rule — implemented in Python and Kotlin)

- **Active now**: a slot is active iff any of its windows covers `(current_weekday, current_minute_of_day)`. A window covers a `(weekday, minute)` when the weekday bit is set in `days` and `start_min <= minute < end_min`. **Midnight-crossing windows** (where `end_min <= start_min`, e.g. 22:00–01:00) are treated as covering `[start_min, 1440)` on the window's weekdays **and** `[0, end_min)` on the *following* weekday. A slot with **zero windows is always active**.
- **Restrictiveness score** = total active minutes per week = Σ over windows of `popcount(days) × window_length_minutes` (a midnight-crossing window's length is `(1440 - start_min) + end_min`). **Ascending** order (smaller = more restrictive = first). A zero-window slot scores `+∞` (last). Ties broken by the existing slot order (drag-rank / `sortOrder`).
- **Primary** = the top-ranked active slot **that has an assigned game**. Active-but-empty slots are skipped for the widget headline (they still appear in-app).

## API

- **`GET /api/slots`** (enriched): each slot gains `windows: [{days, start_min, end_min}, ...]`. The web client also receives server-computed `active_now` + `restrictiveness_rank`; the Android client uses the raw `windows` for its local matcher.
- **Window CRUD** (web): create/update/delete windows for a slot (e.g. `POST /api/slots/{id}/windows`, `PUT /api/slots/{id}/windows/{wid}`, `DELETE /api/slots/{id}/windows/{wid}` — exact shape settled in the plan).
- **Profile**: `GET /api/profile`, `PUT /api/profile`.

## Web UI (canonical editing)

- **Picks tab → per-slot schedule editor**: "Add window" control; each window row = 7 day-checkboxes (Mon–Sun) + start/end time inputs + delete. A slot with no windows shows an "Anytime" badge.
- **Profile section in web Settings**: work hours, bedtime, optional meal windows → renders "suggested times" chips in the window editor (tap to fill). Pure convenience.
- **Schedule-aware Picks view**: the web Picks list sorts/annotates **currently-active slots most-restrictive-first**, with an "active now / next at HH:MM" label — mirroring what the widget surfaces.
- Frontend has no pytest; verified in real Chrome against a **copy** of `games.db` on an alt port (per the standing rule), not by asking the owner.

## Android — widget + lean reader

- **Glance widget (single card)**: shows the **primary** active slot's game — cover (loaded as a bitmap via Coil into a Glance `ImageProvider`, text fallback when null/unreachable), game title, slot label, active-window hint (e.g. "Evening · until 11pm"), and goal. **Tap anywhere → deep-links into the app's Picks tab.**
- **Refresh model**: a **WorkManager** periodic worker (~1–2h, plus on app-foreground) caches `slots + windows + assignments` to disk (DataStore/file); the widget **re-evaluates the current primary on a ~30-min tick** off the **phone clock**, so it advances through the day without the network. Off-network → last cached data (still time-correct). Includes a tap-to-refresh affordance.
- **App Picks view** becomes schedule-aware (read-only): currently-active slots sort **most-restrictive-first** (this is the "sorted by priority order" seen on tapping the widget). Uses the **same pure-Kotlin matcher** as the widget.
- New deps required (absent today): `androidx.glance:glance-appwidget`, `androidx.work:work-runtime-ktx`. New manifest receiver for the Glance widget.

## Edge cases / defaults

- **No active slot with a game** → widget shows the **next upcoming** slot ("Next: Evening at 8:00pm — Hades"); if nothing is scheduled at all → "No picks scheduled — set windows on the web."
- **Active slot but empty** → skipped for the widget headline; still shown in-app.
- **Timezone**: matching uses the **device local time** on Android and the **server local time** on web. Both are the owner's home timezone — an explicit assumption (not a multi-tz design).
- **Midnight-crossing windows** are supported (split internally as described).
- **Existing slots** (no windows) behave as "anytime" — no migration of behavior, no data backfill needed.

## Phasing (one spec → three plans, the established pattern)

1. **Plan A — backend foundation**: `slot_schedule_window` + `user_profile` migrations; the Python matcher (active-now + restrictiveness, exhaustively unit-tested); enriched `GET /api/slots`; window + profile CRUD endpoints.
2. **Plan B — web**: per-slot window editor, profile settings section, schedule-aware Picks view. Chrome-verified by the controller.
3. **Plan C — Android**: pure-Kotlin matcher (JVM unit tests mirroring the Python cases); Glance widget + WorkManager refresh + manifest receiver + new deps; schedule-aware app Picks ordering; widget→Picks deep-link. On-device smoke when the device is back on adb.

## Testing

- **Python matcher**: exhaustive unit tests — day-mask coverage, midnight-crossing, restrictiveness ordering, anytime (zero-window) slots, empty-slot skip, tie-breaking. Endpoints via the `client` fixture + temp DB. Run with `uv run python -m pytest`; lint with `ruff check` only.
- **Kotlin matcher**: JVM unit tests mirroring the Python cases (keeps the two implementations in lockstep). Android build via `./gradlew.bat testDebugUnitTest assembleDebug`.
- **Web + widget**: controller verification (real Chrome for web; on-device widget smoke when the phone is reattached to adb).

## Constraints / process (project rules)

- Deterministic-first; config centralized; type hints on all Python signatures; `logging` not `print`; named constants over magic numbers (CLAUDE.md).
- Subagents: pytest temp-DB + static review only — never the live `games.db`, the running `:5000` server, the network, or the device. The controller does all live ops.
- Work directly on `main` + push (no branches/PRs unless asked).
- App uses `use_reloader=False`: Python route/migration changes require a manual `:5000` restart to take effect.

## Out of scope (v1)

- Profile-as-gate (suppressing picks during work/sleep) — convenience pre-fill only for now.
- Editing schedules/profile on the Android app — web-only.
- Multi-timezone / per-device timezone handling.
- Widget quick-actions (outcome/goal/assign from the widget) — read + launch only.
- Swipe/carousel in the widget — single card only.
