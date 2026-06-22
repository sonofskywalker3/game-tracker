# Tab-scoped Add Game + single-platform auto-select

**Date:** 2026-06-15
**Status:** Approved (design)

## Goal

When adding a game from the library, scope the Add Game modal's platform
controls — and an IGDB-driven auto-select — to the era of the tab the modal was
opened from (Modern / Legacy / PC / All). When the searched game maps to exactly
one platform in that era, select it automatically.

Concrete example: on the **Legacy** tab, typing "Final Fantasy III" and picking
the IGDB result should consider only legacy platforms; if IGDB reports exactly
one legacy platform we model (e.g. Nintendo DS), it is auto-selected.

## Decisions

- **Multi-match:** auto-select **only when exactly one** era platform matches.
  Zero or multiple → no auto-select; the user chooses.
- **Per-tab UI:** the modal shows **only the current era's controls**.

## Components

### 1. IGDB platform map (`igdb_match.py`)

- Extend `IGDB_PLATFORM_IDS` (today: Switch/PS5/PS4/Xbox/Steam/PC) with the 21
  legacy consoles seeded in `models.LEGACY_PLATFORM_SEED`, keyed by our
  short_name → frozenset of IGDB platform id(s).
  - 3DS, NDS, GBA, GBC, GB, WiiU, Wii, GC, N64, SNES, NES, PS3, PS2, PS1, PSP,
    Vita, X360, OGXbox, Genesis, Saturn, Dreamcast.
  - IGDB ids confirmed against IGDB's live `/v4/platforms` during build (the map
    is the single source of truth; a wrong id only means that platform never
    auto-selects, easily corrected).
- Add `short_names_for(igdb_platform_ids) -> list[str]`: reverse lookup mapping a
  set of IGDB ids back to the short_names we model (stable order; unknown ids
  omitted). Built from the same `IGDB_PLATFORM_IDS` table.

### 2. Search endpoint (`/api/games/search` -> `api_igdb_search`)

- Add `platforms` to the IGDB `fields` clause.
- For each result, map the returned IGDB platform ids through `short_names_for`
  and include `platforms: [...]` (only platforms we model) in the JSON.
- No creds / no platform data → `platforms: []` (graceful).

### 3. Add Game modal (`templates/base.html`)

- **Era source:** `const era = (typeof currentMode !== 'undefined') ? currentMode : 'all'`.
  (`currentMode` is defined by the library page; other pages default to `all`.)
- **Era → categories:**
  - `modern` → `['modern_console']`
  - `legacy` → `['legacy_console']`
  - `pc` → `['pc']`
  - `all` / `physical` / `needs_review` → all three categories
- **Control rendering** (`renderNewGamePlatforms`): for the era's categories,
  render `modern_console`/`pc` platforms as checkboxes and `legacy_console` as
  the dropdown. Hide controls for categories not in the era.
- **Auto-select** (`selectIGDBGame`): receives the chosen result's `platforms`.
  Intersect with the era's modeled platform short_names. If the intersection has
  exactly one element → select it (check the checkbox, or set the dropdown and
  auto-check Physical). Otherwise leave selection untouched.
- The selected result's platforms are stored (module-level) so the auto-select
  runs against the chosen IGDB game, not free-typed text.

### 4. Tests

- `igdb_match`: extended map covers every `LEGACY_PLATFORM_SEED` short_name;
  `short_names_for` round-trips ids → short_names and omits unknown ids.
- endpoint: `api_igdb_search` includes mapped `platforms` (IGDB layer mocked, as
  in existing igdb tests).
- JS auto-select behavior verified in a real browser (Playwright), per the
  verify-ui-changes-yourself practice.

## Edge cases

- No IGDB creds, no platform data, or a free-typed title not chosen from the
  dropdown → no auto-select; controls behave as they do today.
- Non-library pages (`currentMode` undefined) → `all` era, all controls.
- Auto-select never *un*-selects; it only ticks a single unambiguous match.
