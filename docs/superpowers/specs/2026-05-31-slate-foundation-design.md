# The Slate — picks-tab revamp foundation — design

**Date:** 2026-05-31
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

This is **sub-project 1** of a two-part picks-tab revamp. The picks tab today is a
deterministic recommendation list (`recommendation.py` + `templates/recommendations.html`):
quick-picks by mood, a weighted "Top Recommendations" ranking, and a "Needs Rating"
strip. It surfaces the whole backlog, which — for the owner — feeds paralysis rather
than action.

This sub-project rebuilds the tab around **The Slate**: a small set of user-defined
**context slots**, each always holding exactly one headline game and a plaintext goal.
It is **fully deterministic — no AI, no Anthropic calls.** Sub-project 2 (separate
spec, later) adds a chat that makes the "what to pin next" decision conversational; it
plugs into the same data and the same per-slot ranking seam this sub-project builds.

Owner context that drives the whole design lives in the persona memory
`gamer-persona-and-backlog-psychology` (collector; PS5/Xbox in the garage = high
activation energy; Switch on the couch + an Nvidia Shield that streams the consoles to
the living room; 3 kids, a ~9–10pm window; backlog guilt → paralysis; helped by
session-tolerance categorization, natural stopping points, quick wins between long
games, and rewarding progress).

## Goal

Replace the backlog-dump picks tab with a focused, always-filled slate of context
slots, backed by real session-fit data, so the owner opens the tab and sees **four
concrete "play this" answers** instead of 700 choices.

After this sub-project:

- The owner defines their own slots (label + constraints + free-text notes). The four
  seed slots match the owner's life today but are data, not code.
- Each slot always holds one game + a goal; finishing/dropping/shelving frees the slot
  and surfaces a deterministic ranked list of what to pin next.
- Every owned-unfinished game has session-fit signals (HowLongToBeat hours + derived
  session-tolerance + platform/latency fit) so ranking is meaningful.
- The slot definitions and per-slot ranking become the shared foundation the SP2 chat
  reasons over.

## Decisions (approved in brainstorming)

- **Foundation first.** Build the deterministic slate + signals now; the Anthropic chat
  is a later sub-project. Acceptable because the chat is only as good as this data.
- **Slots are user-defined, data-driven.** A `slots` table, seeded with the owner's
  four. Label + constraints + free-text `context_notes`. The constraints drive
  deterministic eligibility now AND become part of the SP2 chat prompt later — one
  source of truth.
- **Time-to-beat source = HowLongToBeat + genre rules.** Real per-title hours from HLTB;
  session-tolerance derived from genre/tags + hours via an extensible lookup table (per
  the "fixes must be general" rule — `cleanup-fixes-must-be-general`). Platform-friction
  is derived from platform + the Shield rule. Manual override on any signal.
- **Reuse `recommendation.py`** for per-slot ranking rather than replacing it; the slate
  re-points its existing signals (priority, critic score, tag affinity) at per-slot
  candidate scoring.
- **Pin is outcome-driven, mapped onto existing statuses** (`backlog`/`playing`/
  `completed`/`100`/…), not a parallel state machine. "Beat but not complete" is a
  first-class decision point (chase completion vs shelve).

## The four seed slots (owner's, illustrative — they are editable data)

| Label | platforms | session window | low-latency required | gist |
|---|---|---|---|---|
| Switch · Quick | `["Switch"]` | `max 60 min` | no | couch, short sitting, clean stopping points |
| Switch · Long | `["Switch"]` | `min 60 min` | no | couch, longer session |
| Garage · Console | `["PS5","Xbox"]` | none | **yes** | needs the real setup; reflex/low-latency; worth the trip |
| Long · Stream-safe | `["PS5","Xbox"]` | `min 60 min` | no | turn-based / lag-tolerant; garage **or** Shield-streamed |

## Data model

### `slots` table (new)

- `id` INTEGER PK
- `label` TEXT NOT NULL
- `sort_order` INTEGER NOT NULL
- `platforms` TEXT — JSON array of platform names that qualify
- `max_session_minutes` INTEGER NULL — upper bound on a comfortable sitting (the `<1hr`
  axis); NULL = no cap
- `min_session_minutes` INTEGER NULL — lower bound (the `>1hr` axis); NULL = no floor
- `requires_low_latency` INTEGER NOT NULL DEFAULT 0 — 1 = excludes input-lag-sensitive
  genres (the garage-only slot); 0 = stream-safe OK
- `context_notes` TEXT — owner's own words about the slot; feeds the SP2 chat prompt
- `current_game_id` INTEGER NULL — FK to `games`; the headline game (one per slot)
- `goal` TEXT — plaintext ("beat it", "finish the plat", "play the DLC")

`max_session_minutes` / `min_session_minutes` express the slot's session *window*. They
gate against the game's **session-tolerance** signal (below), not its total length — a
60-hour game can still belong in a short-session slot if it has clean stopping points.

### `slot_history` table (new)

Every game that has passed through a slot — the "what did I just finish" + momentum +
genre-fatigue memory.

- `id` INTEGER PK
- `slot_id` INTEGER — FK to `slots`
- `game_id` INTEGER — FK to `games`
- `goal` TEXT — the goal at the time
- `pinned_at` TEXT — ISO timestamp
- `removed_at` TEXT — ISO timestamp
- `outcome` TEXT — one of `beat` / `completed` / `dropped` / `shelved`

### Decision signals on `games` (new columns)

- `hltb_id` TEXT NULL — matched HowLongToBeat id
- `hltb_main_minutes` INTEGER NULL
- `hltb_main_extra_minutes` INTEGER NULL
- `hltb_completionist_minutes` INTEGER NULL
- `time_to_beat_override_minutes` INTEGER NULL — manual override when HLTB is wrong/missing
- `input_lag_override` INTEGER NULL — manual override of the derived latency tolerance
  (NULL = use the derived value)

**Session-tolerance is NOT stored** — it is derived at scoring time from genre/tags +
hours via the lookup table, so retuning the table re-scores everything without a
migration. Same for the default latency tolerance (overridable per-game via the column
above).

## Decision signals & HowLongToBeat

New module `hltb.py`, sibling to the vendor scrapers:

- Matches each game to a HowLongToBeat entry by title, reusing the existing
  `clean_search_title` normalization. Stores the three durations + `hltb_id`.
- Runs as a **batch enrichment pass** (mirrors `fetch_covers.py`) and **on-demand per
  game**. Cached, rate-limited, respectful.
- HowLongToBeat has **no official API** — this is an unofficial library / thin scraper.
  It must **degrade gracefully**: no match or no hours → leave columns NULL; eligibility
  falls back to genre rules; nothing crashes or blocks the page.

Two distinct derived signals (kept separate on purpose):

- **Session tolerance** — "enjoyable in a short sitting / has clean stopping points."
  Derived from genre/tags (mission-based, roguelike, puzzle, arcade, rhythm → tolerant;
  open-world, long-narrative, grind-heavy → not). This is what the short-session slots
  filter on. Lookup table is extensible.
- **Total length** — HLTB hours. Powers the "quick win vs 60-hour monster" framing and
  goal realism; a secondary ranking signal, **not** the session filter.

Latency tolerance (for the `requires_low_latency` slot) is a genre lookup
(turn-based / strategy / RPG / management → tolerant; action / shooter / fighting /
rhythm → not), overridable per game.

## Slot eligibility (deterministic ranking)

For each slot, score every owned-unfinished game:

- **Hard filters:**
  - game platform must intersect the slot's `platforms`
  - if `requires_low_latency`, exclude latency-sensitive games
- **Fit score** (sorted desc):
  - session-tolerance vs the slot's session window
  - existing `recommendation.py` signals: priority, critic score, tag affinity
  - **genre-fatigue penalty** if a recently-finished `slot_history` game shares dominant
    tags (don't suggest a third JRPG in a row)
  - total-length nudge toward "quick wins" when the slot is short-session
- **Output:** a ranked candidate list per slot — the deterministic "what to pin next."
  **This is the exact seam SP2's chat plugs into:** SP1 ranks, SP2 makes the ranking
  conversational. `recommendation.py` is reused, not discarded.

## Lifecycle / status integration

Pinning a game = assign `current_game_id` + `goal` to a slot. Removal is outcome-driven
and maps onto existing statuses:

- **Beat** (main story done) → status `completed`, then an inline choice:
  - *Chase completion* → stays slotted, `goal` rewritten ("get the plat"); no history row
    yet.
  - *Shelve* → slot frees; `slot_history` row `outcome='shelved'`.
- **Complete** (whatever "done" means for that game) → status `100`; slot frees;
  `outcome='completed'`.
- **Dropped** → slot frees; `outcome='dropped'` (status set to the existing dropped/
  backlog value — exact vocabulary confirmed against `models` during implementation).
- **Swap** → unpin with no outcome (changed your mind); slot frees, no history row.
- An emptied slot immediately surfaces its ranked candidates so it never sits silently
  empty.

## API

REST, matching existing `/api/...` conventions:

- `GET /api/slots` — definitions + current games + per-slot ranked candidates
- `POST /api/slots`, `PATCH /api/slots/<id>`, `DELETE /api/slots/<id>` — slot CRUD
- `POST /api/slots/<id>/pin` — assign game + goal
- `POST /api/slots/<id>/outcome` — `beat` (+ `chase`/`shelve`) / `complete` / `dropped` / `swap`
- `PATCH /api/slots/<id>/goal` — edit the goal
- `POST /api/hltb/refresh` — batch enrichment trigger; per-game refresh variant

## Picks tab UI

`templates/recommendations.html` is rebuilt to lead with the Slate:

- **Slate row** — one card per slot (in `sort_order`): label + a constraint chip
  ("Switch · <1hr", "Garage · low-latency"), the current game (cover, title, goal,
  a subtle hours-played / HLTB-estimate progress bar), and inline actions: **Beat ·
  Complete · Dropped · Edit goal · Swap**. Empty state → inline ranked candidate list
  ("Decide what to pin"); SP2's chat lands in this same spot.
- **Beat-but-not-complete** → inline *Chase completion* / *Shelve* choice (no modal
  sprawl; matches the existing inline-action feel).
- **Recently finished** — a quiet strip fed by `slot_history`, framing momentum /
  "gaming nights," not guilt. The "rewarding, not homework" progress view.
- **Slot settings** — a small editor to add / rename / retune slots (label, platforms,
  session window, low-latency flag, context notes). This is where the owner "defines
  their own four."
- The old static quick-picks / Top Recommendations retire or move below the fold —
  `recommendation.py` now powers per-slot ranking. The **Needs Rating** strip stays
  (useful and orthogonal).

## Testing

Per repo conventions (`uv run python -m pytest`, temp-DB only — never the live
`games.db`, per `subagent-impl-never-touch-live-db`; `ruff check` gate only, never
`ruff format`, per `ruff-check-not-format`):

- **Eligibility scoring** — given slot constraints + game signals, the right games rank
  for the right slots: platform hard-filter, low-latency exclusion, session-tolerance
  windowing, genre-fatigue penalty from history, quick-win nudge.
- **HLTB module** — title matching + duration parsing against **mocked HTTP** (never
  live); graceful degradation on no-match / no-hours.
- **Lifecycle transitions** — beat→chase vs beat→shelve, complete, dropped, swap;
  correct `slot_history` rows + status changes.
- **Slot CRUD** + always-filled / empty-slot behavior.
- **API routes** via the Flask test client.

## Scope boundary

In scope: slots data model, HLTB enrichment, derived signals + lookup tables,
deterministic per-slot ranking, lifecycle/outcomes, slot CRUD, the picks-tab rebuild.

**Out of scope (→ SP2):** any Anthropic/LLM call, any chat UI. The "decide what to pin"
affordance in SP1 is a deterministic ranked list. SP2 swaps that list's brain for a
conversation over the same slots, signals, and endpoints.

## Open items to confirm during implementation

- Exact existing `status` vocabulary in `models` (for the dropped/shelved mapping) —
  read and match, don't invent.
- Whether `platforms` on a game is single-valued or multi (affects the platform
  intersection filter) — confirm against the schema.
- Which unofficial HLTB access path is most stable (maintained library vs thin scraper)
  — evaluate at plan time; either way it sits behind `hltb.py` and degrades gracefully.
