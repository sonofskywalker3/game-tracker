# Session-tolerance + series-role catalog & series-focus slots — design

**Date:** 2026-06-01
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved in brainstorming; ready for implementation plan

Fixes a conceptual error in the just-shipped picks redesign: Quick vs Long was driven
by **total play-time** (HLTB hours), but the right axis is **session tolerance** — does
a game have natural short stopping points (levels, chapters, battles, cases, in-game
days), regardless of length. Sun Haven (44h, day-by-day), Advance Wars (battles), and
Phoenix Wright (cases) are long games that suit short sessions; they were wrongly
banished to Long. This replaces the length signal with a per-game **session-tolerance**
trait, adds a parallel **series-role** trait (mainline vs spin-off/gaiden), seeds both
via Claude Code (free on the owner's subscription), and adds a **Focus-series** slot
setting that routes mainline → long slots and spin-offs → short slots.

Builds on [[slate-picks-tab-feature]]. Honors [[slate-system-must-be-generic]] (slots
fully user-editable, never locked), [[cleanup-fixes-must-be-general]] (catalog + tables,
not one-off patches), [[subagent-impl-never-touch-live-db]] (seed agents RETURN data;
the controller writes the DB), [[work-on-main-no-branches]],
[[run-tests-with-python-m-pytest]], [[ruff-check-not-format]].

## Goal

Quick/Long keys off whether a game suits a short sitting, not how long it is. Two
canonical, catalog-backed per-game traits — `session_length` and `series_role` — are
classified by AI (seeded via Claude Code) with a manual override, shared across users
via a curated committed catalog the owner merges by PR. A slot can optionally focus on
a series and route its mainline/spin-off entries to long/short slots respectively.

## Decisions (approved in brainstorming)

1. **Two canonical per-game traits**, both on `games`, both catalog-backed:
   - `session_length`: `short` (clean short stopping points) | `long` (needs a block) | null.
   - `series_role`: `mainline` | `spinoff` (gaiden/side) | null. Only meaningful for
     games that belong to a series.
2. **Resolution order** (on add + on a startup sync pass): **catalog value → else the
   adder's manual pick → else AI (Phase B, only if an Anthropic key is set) → else null.**
   A `manual` source LOCKS the row: catalog re-sync and AI never overwrite it.
3. **Shared catalog = a committed file** `game_traits.default.json` (curated by the owner
   via PR), with a gitignored per-user `game_traits.json` override — mirroring the
   existing `series_patterns.default.json` pattern (`models.load_series_patterns`).
   Keyed by `normalized_title`.
4. **Quick/Long keys off `session_length`** (clean split): `long` games are **excluded
   from Quick** and **favored in Long**; `short` ranks well in Quick and is still allowed
   in Long; `null` is neutral (not excluded). **Total play-time is demoted from the
   slot-fit axis but NOT discarded** — it stays first-class data. Session-tolerance is
   *how long a sitting*; time-to-beat is *how long the whole game* — a separate, legit
   mood ("a game I can beat in 3 long sessions" vs "chip away at for months"). So the
   directional TTB term is removed from `rank_candidates`'s session-fit scoring, but the
   effective time-to-beat is surfaced in every candidate's payload (for the UI) and
   included in the SP2 chat prompt so length-based moods are answerable.
5. **Focus-series slot setting** (`slots.focus_series_id`, nullable, freely editable):
   when set, the slot boosts that series' games AND routes by role — short-session slot
   (`max_session_minutes` set) favors `spinoff`, long-session slot (`min_session_minutes`
   set) favors `mainline` — platform filter still applies. The existing auto-boost from
   the slot's last-played series stays.
6. **Slots are never locked** — every slot field (label, platforms, session window,
   streamable_only, prioritize_started, context_notes, focus_series_id) is user-editable
   via the ⚙ editor; nothing is hardcoded.
7. **Seed both traits via Claude Code** (free), batched (~20 games/agent). Agents RETURN
   `{normalized_title, session_length, series_role, reason}`; the controller writes
   `games.db` (source=`ai`, only where not already manual/catalog) AND emits/merges
   `game_traits.default.json` for the owner's PR.
8. **Real-time on-add AI is Phase B** (deferred) — it needs the Anthropic API key, which
   rides with the pending Settings-UI key work. Until then, on-add for an unknown game =
   null unless the adder picks a value manually.

## Data model

### `game_traits.default.json` (committed) + `game_traits.json` (gitignored, per-user)
```json
{ "<normalized_title>": { "session_length": "short", "series_role": "spinoff" } }
```
Either field may be absent. `models.load_game_traits()` loads the per-user file if
present else the committed default (mirrors `load_series_patterns`).

### `games` columns (new; idempotent `migrate_game_traits`)
- `session_length TEXT` — short/long/null
- `session_length_source TEXT` — catalog/ai/manual/null
- `series_role TEXT` — mainline/spinoff/null
- `series_role_source TEXT` — catalog/ai/manual/null
Added via `ALTER TABLE games ADD COLUMN ...` guarded by `PRAGMA table_info`.

### `slots.focus_series_id` (new; extend `migrate_slots` backfill)
`focus_series_id INTEGER` nullable, FK to `series(id) ON DELETE SET NULL`. Added by the
existing idempotent ALTER-backfill guard pattern in `migrate_slots`.

### Trait resolution — `apply_traits_catalog(conn)`
Idempotent; run in `migrate_db` and callable after add. For each game, for each trait:
if `<trait>_source` is `manual`, skip (locked). Else if the catalog (by normalized_title)
has the trait, set value + source=`catalog`. (AI/null untouched here — AI is the seed/
Phase B path.) Never downgrades a manual choice.

## Classification paths

- **Catalog** — `apply_traits_catalog` on startup/add. Authoritative shared default.
- **Manual** — Add-Game form selectors (on create) and the game modal (on edit) write
  the trait + source=`manual` via `PUT /api/games/<id>` (new accepted fields
  `session_length`, `series_role`; setting either marks its source manual).
- **AI seed (now, Claude Code)** — the controller runs a workflow over all games lacking
  a catalog/manual value; agents classify from title (+ platform + any tags + series
  name) and RETURN both traits; controller writes source=`ai` and emits the catalog.
- **AI on-add (Phase B)** — when a game is added with null traits and an Anthropic key
  is configured, classify live via the API. Out of scope here.

## Ranking changes (`slots.py rank_candidates`)

- **Remove** the directional time-to-beat term (TTB_REFERENCE/WEIGHT/CAP) from the slot
  session-fit scoring. But **include `effective_time_to_beat_minutes(game)` in each
  returned candidate dict** (e.g. `candidate["time_to_beat_minutes"]`) so the UI can show
  it and the SP2 chat can reason over it. Time-to-beat remains a real signal, just not
  the Quick/Long driver.
- **session_length hard filter / boost:**
  - Quick (`max_session_minutes` set): **skip candidates with `session_length == 'long'`**
    (clean split — they live only in Long). Boost `session_length == 'short'`
    (`+SESSION_FIT_BOOST`, reason "Fits a quick session"). `null` neutral.
  - Long (`min_session_minutes` set, no max): boost `session_length == 'long'`
    (`+SESSION_FIT_BOOST`, reason "Worth a long sitting"). `short`/`null` allowed, no boost.
- **Focus-series routing** (when `slot['focus_series_id']` is set): for candidates in that
  series, `+FOCUS_SERIES_BOOST`; then role routing — short-session slot:
  `+ROLE_BOOST` if `series_role == 'spinoff'`; long-session slot: `+ROLE_BOOST` if
  `series_role == 'mainline'`. (Platform hard-filter already applies, satisfying
  "if it's platform appropriate".)
- Existing signals unchanged: platform/streamable hard filters, priority, tag affinity,
  genre fatigue, prioritize_started, recent-series auto-boost, dismissals, finished/pinned
  exclusion.
- New constants: `SESSION_FIT_BOOST = 25.0`, `FOCUS_SERIES_BOOST = 30.0`,
  `ROLE_BOOST = 20.0`. `rank_candidates`'s SELECT adds `g.session_length, g.series_role`
  (already `g.*`, so present) — read from the row.

## API

- `PUT /api/games/<id>` accepts `session_length` and `series_role`; writing either sets
  its value + the matching `*_source = 'manual'` (empty/null clears value + source).
- `GET /api/games/<id>` returns the four trait columns (via `SELECT g.*`).
- Slot CRUD (`POST`/`PATCH /api/slots`) accept `focus_series_id` (int or null).
- `GET /api/series` (if absent) → `[{id, name}]` for the Focus-series picker. (Confirm
  whether an equivalent exists; reuse it if so.)

## UI

- **Game modal** (`base.html`): two `<select>`s — "Session length" (—/Short/Long) and
  "Series role" (—/Mainline/Spin-off), seeded from the game, `onchange` → `PUT` (manual).
  Show the current source subtly (catalog/ai/manual).
- **Add-Game modal**: the same two selectors, default "—" (null). On create, a chosen
  value is saved manual; left "—" stays null (Phase-B AI may fill later).
- **Slot ⚙ settings** (`recommendations.html`): add a **Focus series** `<select>`
  (—/each series) bound to `focus_series_id`, saved via the existing slot PATCH payload.

## The Claude-Code seed (controller-run operation)

A Workflow over all games missing a catalog/manual trait, batched ~20/agent. Each agent
receives a list of `{id, normalized_title, title, series_name, platforms, tags}` and
returns, per game, `{id, session_length: short|long|null, series_role: mainline|spinoff|
null, reason}` (StructuredOutput schema). The controller:
1. aggregates, writes `games` (source=`ai`) only where the row isn't already
   manual/catalog;
2. merges results into `game_traits.default.json` (sorted, by normalized_title) for the
   owner to review + PR;
3. logs counts (classified / skipped-locked / low-confidence).
Agents never touch `games.db`. Re-runnable (only fills unset rows). "Take as long as you
need" — batched for throughput, not literally one agent per game.

## Error handling
Unknown/null traits are always safe (neutral ranking, never excluded from Long, only
`long` is excluded from Quick). Catalog file missing/malformed → empty dict, no crash
(mirrors `load_series_patterns`). Trait selects accept only the enum values; anything
else is ignored server-side.

## Testing (per conventions; `uv run python -m pytest`, temp DB, `ruff check`)
- `migrate_game_traits`: the 4 columns added, idempotent. `migrate_slots` backfills
  `focus_series_id`, idempotent.
- `apply_traits_catalog`: sets catalog values, skips `manual`-sourced rows, no-op when
  absent.
- ranking: a `long` game is **absent** from a Quick slot and **boosted** in Long; a
  `short` game ranks above a `null` game in Quick; focus-series slot boosts the series
  and routes mainline-above-spinoff in a long slot and the reverse in a short slot;
  platform filter still excludes off-platform series games.
- API: `PUT` sets each trait + source=manual and clears on null; slot PATCH/POST persist
  `focus_series_id`; `GET /api/games/<id>` returns the traits.
- render smoke: `/recommendations` and the modal markup still render (trait selects,
  Focus-series select present).

## Scope / sequence
- **Phase 1 (this spec, free):** catalog file + `migrate_game_traits` + `focus_series_id`
  + `apply_traits_catalog` + ranking change (session_length filter/boost, remove TTB term)
  + manual selectors (modal + Add-Game) + slot Focus-series picker + the Claude-Code seed
  + catalog export. Delivers the corrected ranking on the whole seeded library.
- **Phase B (later):** real-time on-add AI classification via the Anthropic key (with the
  Settings-UI key work; first bite of the SP2 API integration).

**Per-game decision signals available to the SP2 chat** (so it can answer mood-based
asks): `session_length`, `series_role`, **effective time-to-beat (minutes)**, platforms,
genres/tags, priority, status, and the slot's `context_notes`. Time-to-beat in particular
lets the chat handle length moods ("beat in 3 sessions" vs "months-long") that the fixed
slots don't encode.

## Open items to confirm at implementation time
- Whether a `GET /api/series` (or equivalent) already exists for the Focus-series picker;
  reuse or add a minimal one.
- The Add-Game modal's current field layout (from the DLC SP3 work) for where the two
  selectors slot in.
- Exact `series_name`/series membership source to feed the seed agents (the `series`
  table + `user_ratings.series_id` + `auto_populate_series`).
