# Picks-tab library-consistent redesign — design

**Date:** 2026-06-01
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved in brainstorming; ready for implementation plan

The first cut of the Slate picks tab (`templates/recommendations.html`, shipped
2026-06-01) was built in isolation and does not match the rest of the tool: custom
oversized cards, text-only candidate lists, `prompt()`/`alert()` dialogs, a clunky
"Slots" button, and a layout that shares nothing with the library page. This redesign
rebuilds the picks tab using the **library's existing visual language**, adds the
ranking signals the owner needs (real play-time + series affinity), and makes the
stats hero a **persistent headline on every page**.

Builds on [[slate-picks-tab-feature]] (SP1 foundation) and the owner's
[[gamer-persona-and-backlog-psychology]]. Honors [[slate-system-must-be-generic]]
(per-slot config, not hardcoded), [[work-on-main-no-branches]],
[[subagent-impl-never-touch-live-db]], [[run-tests-with-python-m-pytest]],
[[ruff-check-not-format]].

## Goal

The picks tab should feel like the same app as the library: same hero, same
`.game-card` tiles, same grid density, same input/dropdown styling, same `openModal`
flow. Centered on the **Slate** (one game per context slot), it lets the owner pin
**any** game via search, see ranked suggestions they can **accept or dismiss**, and
get meaningfully different Quick vs Long lists driven by **time-to-beat** and
**series** signals.

## Library components to reuse (the consistency contract)

From `templates/index.html` / `templates/base.html`:
- **Stats hero band:** `rounded-2xl … bg-gradient-to-br from-[#241b3a] via-[#191522]
  to-[#151515] border border-gray-700/50`, stat tiles (`total / completed / playing /
  backlog / done% / DLC`) via `renderHeroStats(stats)` + `loadHeroStats()`.
- **`.game-card` tile:** `bg-surface-light rounded-lg overflow-hidden`, `aspect-[3/4]`
  cover with placeholder + `onerror` fallback, `p-3` body, `font-medium text-sm
  line-clamp-2`, `platform-badge` + `status-badge`, `onclick="openModal(id)"`.
- **Grid:** `grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-4`.
- **Inputs/dropdowns:** search input `bg-surface rounded-lg border border-gray-600
  px-3 py-1.5 text-white text-sm placeholder-gray-500 focus:border-accent`; dropdown
  pattern (`toggleDropdown`, `bg-surface-light border border-gray-700 rounded-lg`).
- **Globals (base.html):** `api` (get/put/post/delete/patch), `openModal`,
  `escapeHtml`, `showModalEl`/`hideModalEl`, `refreshGameList`.

## Decisions (approved in brainstorming)

1. **Shared stats hero on EVERY page.** Extract the hero band + `renderHeroStats`/
   `loadHeroStats` from `index.html` into `base.html`, rendered above `{% block
   content %}` on every page. The library's mode-switcher becomes a right-aligned
   `{% block hero_aside %}` that only the library fills. All page content renders
   below the hero. The hero is the persistent "what everything is working toward."
2. **Picks layout:** shared hero → **Slate** (slot cards) → **Needs Rating**
   (library `.game-card` 190px grid). No custom hero on picks.
3. **Slot card** holds: the current game (mini `.game-card`, click → modal), inline
   actions, a per-slot **gear** → library-styled settings dropdown, a **search box**
   (pins any game), and a **suggestions list**.
4. **Suggestions list:** each row = cover thumb + title + reason, with **accept (✓)**
   = pin, and **dismiss (✕)**. Dismiss hides that game from this slot's list until the
   slot's game is replaced; then it may return if still a good match. Clicking the
   **title/cover** (not accept) opens the modal; **on modal close the slate re-ranks**.
5. **Dismiss state:** server-side `slot_dismissals` table, auto-cleared when the slot's
   game changes.
6. **Manual time-to-beat:** an editable "hours to beat" field in the game modal writes
   `time_to_beat_override_minutes`; feeds ranking immediately on modal close.
7. **Ranking signals:** directional **time-to-beat** (short-session slots favor short
   games, long-session slots favor long games) + **series boost** (same series as the
   slot's most recent game) + dismissal exclusion. Pinning via search **bypasses all
   eligibility filters** (manual override always wins). HLTB enrichment is run so the
   play-time data exists.
8. **Settings:** per-slot gear opens a library-styled dropdown editor (label,
   platforms, session window, streamable_only, prioritize_started, context_notes,
   Save/Delete) + an "Add slot" affordance. No standalone "Slots" button / `alert()`.

## Components & changes

### A. Shared stats hero (`base.html`, `index.html`)
- Move hero markup into `base.html` directly above the content block: stats container
  (`#hero-stats`) + `<div class="ml-auto">{% block hero_aside %}{% endblock %}</div>`.
- Move `renderHeroStats`/`loadHeroStats` into `base.html`; call `loadHeroStats()` on
  `DOMContentLoaded`. One `/api/stats` fetch per page.
- `index.html`: delete its hero markup + hero JS; fill `{% block hero_aside %}` with
  the mode-switcher (`#mode-switcher` + `renderModeBar`/`setMode` stay library-local).
- `series.html`, `settings.html`, `recommendations.html` inherit the hero automatically
  (verify each extends `base.html`).

### B. Ranking (`slots.py`)
- **Time-to-beat (directional).** Import `slot_signals.effective_time_to_beat_minutes`.
  Add constants `TTB_REFERENCE_MINUTES = 1200` (20h), `TTB_WEIGHT = 0.02`,
  `TTB_TERM_CAP = 20.0`. Per candidate with known `ttb`:
  - short-session slot (`max_session_minutes` set): `score += clamp((TTB_REFERENCE -
    ttb) * TTB_WEIGHT, -CAP, +CAP)` → short games rise, long games sink.
  - long-session slot (`min_session_minutes` set and no `max_session_minutes`):
    `score += clamp((ttb - TTB_REFERENCE) * TTB_WEIGHT, -CAP, +CAP)` → long games rise.
  - unknown `ttb` → no term (neutral). Reason string when nudged: "Short play" /
    "Meaty play".
  - This **replaces** the genre-only `SESSION_MISMATCH_PENALTY` as the primary
    session-fit signal (keep the genre penalty as a secondary nudge only if both a
    `max_session_minutes` is set AND ttb is unknown — so untagged+unknown still has
    some signal; otherwise ttb governs).
- **Series boost.** Add `SERIES_BOOST = 30.0`. Find the slot's most recent
  `slot_history` row → its game's `user_ratings.series_id`. Boost candidates sharing
  that `series_id`: `score += SERIES_BOOST`, reason "Next in this series". (If the slot
  has no history or the last game has no series, no boost.)
- **Dismissal exclusion.** Exclude candidates present in `slot_dismissals` for that
  slot.
- Existing signals (platform/streamable hard filters, priority, tag-affinity,
  genre-fatigue, prioritize_started, finished/pinned exclusion) remain.

### C. Dismissals (`models.py`, `slots.py`, `app.py`)
- New table `slot_dismissals (slot_id INTEGER NOT NULL, game_id INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (slot_id, game_id),
  FOREIGN KEY(slot_id) REFERENCES slots(id) ON DELETE CASCADE, FOREIGN KEY(game_id)
  REFERENCES games(id) ON DELETE CASCADE)`. Idempotent `migrate_slot_dismissals`,
  registered in `migrate_db` + `conftest.temp_db`.
- `slots.dismiss_suggestion(conn, slot_id, game_id)` → INSERT OR IGNORE.
- `_clear_dismissals(conn, slot_id)` → DELETE for slot; called from `pin_game` and from
  `apply_outcome` on every branch that changes/clears `current_game_id` (beat-shelve,
  complete, dropped, swap; NOT beat-chase, which keeps the same game).
- `rank_candidates` excludes dismissed game_ids for the slot.
- Endpoint `POST /api/slots/<id>/dismiss {game_id}`.

### D. Manual time-to-beat in the modal (`base.html`, `app.py`)
- Add an "Hours to beat" number input to the game modal, seeded from the game's
  effective time-to-beat (override else HLTB main, shown in hours). Saving writes
  `time_to_beat_override_minutes` (hours × 60) via the existing `PUT /api/games/<id>`.
- Extend the games `PUT` handler to accept `time_to_beat_override_minutes` (a `games`
  column) alongside the existing `title`/`cover_url` game-table updates. Empty clears
  it (NULL).
- `GET /api/games/<id>` includes `time_to_beat_override_minutes` +
  `hltb_main_minutes` so the modal can display the effective value.

### E. Re-rank on modal close (`base.html`)
- The picks page already aliases `refreshGameList = loadSlate`. Ensure the modal's
  close path calls `refreshGameList()` so edits re-rank immediately. Hook the existing
  modal-close function (e.g., wrap `closeModal`/`hideModalEl` for the game modal) to
  invoke `refreshGameList()` if defined.

### F. Picks page rebuild (`templates/recommendations.html`)
- Content: `#slate` row of slot cards + Needs-Rating section using the library
  `.game-card` 190px grid (reuse the library's card markup; factor a shared
  `gameCardHtml(game)` helper in `base.html` so picks + library render identical tiles
  and the Needs-Rating tiles match the library exactly).
- **Slot card** (`bg-surface-light rounded-lg`, library surface):
  - Header: label + gear (opens settings dropdown).
  - Current game (if any): mini `.game-card` (cover, title, goal, status badge),
    `onclick=openModal`. Actions: Beat / Complete / Dropped / Swap (small consistent
    buttons; beat → inline chase/shelve choice; no `prompt()`/`alert()` — use small
    inline inputs/confirm dialogs styled like the app).
  - **Search box** (library input style) → debounced typeahead on `/api/games/search`
    → results dropdown of small tiles; selecting one pins it (any game).
  - **Suggestions list** (~5): row = cover thumb + title + top reason + ✓ accept (pin)
    + ✕ dismiss. Title/cover click → `openModal`.
- Replace `prompt`/`alert` flows: goal edit = inline text input; beat chase/shelve =
  inline two-button choice; hltb refresh feedback = a small toast/inline status, not
  `alert()`.

### G. Settings editor (`templates/recommendations.html`)
- Per-slot gear toggles a dropdown (library `toggleDropdown` styling) with: label
  input, platform checkboxes (Switch/PS/Xbox/Steam/PC), max/min session number inputs,
  `streamable_only` + `prioritize_started` checkboxes, `context_notes` textarea,
  Save (`PATCH /api/slots/<id>`) + Delete (`DELETE`). An "Add slot" button (`POST`).

## Data flow

`loadSlate()` → `GET /api/slots` (`{slots[], recently_finished[]}`; each slot carries
`current_game` + filtered, dismissal-excluded, ranked `candidates`). Accept →
`POST …/pin` → reload. Dismiss → `POST …/dismiss` → reload. Search → `GET
/api/games/search` → `POST …/pin`. Outcomes → `POST …/outcome` → reload (clears
dismissals server-side). Modal edits → `PUT /api/games/<id>` → on close `loadSlate()`.

## Error handling
- HLTB stays graceful (already degrades to None). Unknown time-to-beat → neutral
  ranking term, never a crash. Search < 2 chars → `[]`. Pinning a nonexistent game →
  no-op. All new routes follow the existing `get_db()/jsonify/close` pattern.

## Testing (per conventions)
`uv run python -m pytest`, temp-DB only, `ruff check` (never `ruff format`):
- `migrate_slot_dismissals`: table/columns/PK/FK-cascade, idempotent.
- ranking: time-to-beat direction (short game ranks above long in a Quick slot and
  below it in a Long slot, given HLTB minutes); series boost (same-series candidate
  outranks an equal one after a series game is in the slot's history); dismissal
  exclusion; dismissals cleared on pin/outcome.
- `dismiss_suggestion` + clear-on-replace lifecycle.
- API: `POST /api/slots/<id>/dismiss`; `PUT /api/games/<id>` accepts
  `time_to_beat_override_minutes`; `GET /api/games/<id>` returns the time-to-beat
  fields.
- render smoke tests: `/recommendations` and `/` (library) both still render 200 with
  the shared hero present (`id="hero-stats"`).

## Scope boundary
No Anthropic/LLM/chat (still SP2). This is deterministic ranking + a UI rebuild + the
shared-hero refactor. The chat later reuses the same slot config + `rank_candidates`
seam.

## Open items to confirm at implementation time
- Confirm `series.html` / `settings.html` extend `base.html` (so they get the hero).
- Confirm the exact game-modal close function name in `base.html` to hook re-rank.
- Confirm the `PUT /api/games/<id>` handler's game-table update block (where
  `title`/`cover_url` are written) to add `time_to_beat_override_minutes`.
