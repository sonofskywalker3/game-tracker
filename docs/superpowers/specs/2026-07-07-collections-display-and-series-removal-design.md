# Collections display setting, series removal, alphabetical sort

**Date:** 2026-07-07
**Status:** Approved (design)

## Problem

Three related pain points in the main library view:

1. **Sorting.** The main games list is not alphabetical. It sorts by the home-rolled
   *series* name (falling back to title), so entries cluster under franchise names.
   The owner wants a plain alphabetical-by-title list.

2. **Home-rolled series system.** A large, hand-built series layer (JSON catalogs,
   `series` table, per-game/rating series columns, `/series` pages and `/api/series*`
   routes, series boosts in slots/decider) groups games by franchise. The owner
   prefers the newer IGDB **collections** layer (the `/collections` page) and wants
   the entire home-rolled series system removed.

3. **Compilation duplication.** For many compilations, both a *container* tile
   (e.g. "Halo: The Master Chief Collection") and its *member* games show in the
   library. A prior one-time cleanup destructively broke some compilations into
   members and deleted the container (e.g. "Mega Man Legacy Collection" — only
   members 1–6 survive), but only for some compilations, leaving the data
   inconsistent. That cleanup was forced on all data; it should have been a
   per-user preference. The owner wants a **non-destructive** setting to choose,
   per user, whether to show the collection, its member games, or both.

## Non-goals

- Restoring the container tiles deleted by the prior cleanup (deferred; optional
  data-repair for later).
- Changing the IGDB `/collections` page or the `game_collections` m2m layer.
- Any change to the barcode scanner (tracked separately).

## Data facts (verified against live `games.db`, 2026-07-07)

- `games.collection_name` (TEXT) stamps each broken-out **member** row with its
  compilation's title (122 stamped rows). This is the *compilation* grouping.
- The `collection_name` strings are **not reliable join keys**: members may carry
  `"Megaman Battle Network Legacy Collection Vol.1"` while the container row's title
  is `"Mega Man Battle Network Legacy Collection Vol. 1"` (spacing/spelling differ).
- **18** distinct `collection_name` values exactly equal an existing game title —
  i.e. 18 compilations where a container row AND its members both exist (the visible
  duplication: Halo: MCC, BioShock: The Collection, Castlevania Anniversary
  Collection, Uncharted: Legacy of Thieves, etc.).
- Some compilations have **only members, no container** (Mega Man Legacy Collection).
- The IGDB `collections`/`game_collections` layer groups by **franchise**
  (Mega Man 1–6 → IGDB collection "Mega Man"), which is a different axis than the
  compilation. It is unaffected by this work.

## Design

### Part A — Remove the home-rolled series system

Remove every piece of the series layer (full inventory in the code map). Summary:

- **Config files:** delete `series_catalog.default.json`, `series_patterns.default.json`,
  `series_patterns.json`, `series_patterns` / `series_catalog` path constants.
- **models.py:** remove `load_series_patterns`, `match_series_prefix`,
  `load_series_catalog`, `add_series_pattern`, `auto_populate_series`,
  `backfill_series_source`, `apply_series_catalog`, `SERIES_ROLE_VALUES`,
  `migrate_series_columns`, `migrate_series_source`, and the migration wiring that
  calls them. Stop creating the `series` table and the series columns on
  `user_ratings` / `games` / `slots`.
- **app.py:** remove the `/series` and `/series/manage` page routes, the entire
  `/api/series*` block, series fields in `/api/games` (select + ORDER BY),
  `series_role` trait handling, and `focus_series_id` in slot create/update.
- **Other modules:** remove series references in `dedup.py` (`infer_series_name`),
  `import_scraped.py` (series carry-over + `--apply-series-catalog` CLI flag),
  `slots.py` (`SERIES_BOOST`, `FOCUS_SERIES_BOOST`, `_slot_recent_series_id`,
  focus-series boost/role routing), `decider.py` (series columns in snapshot +
  focus-series prompt line).
- **Templates:** delete `series.html`, `series_overview.html`; remove the series nav
  link, series-role selects, create/add-series dialogs, `inferSeriesName`, and series
  display from `base.html`; remove `series_name` usage from `index.html`.
- **Tests:** delete series-specific test files; excise series assertions embedded in
  shared tests (list in the code map).

**DB columns:** SQLite ≥ 3.35 supports `ALTER TABLE ... DROP COLUMN`. Add a migration
that drops the now-dead series columns **if present** (guarded, idempotent). If a
drop fails on an older SQLite, log and leave the column unused (non-fatal). The
`series` table is dropped with `DROP TABLE IF EXISTS`.

### Part B — Alphabetical sort

With series gone, change the default sort in both layers to alphabetical by title:

- **SQL** (`app.py` `/api/games` default branch): `ORDER BY g.title COLLATE NOCASE ASC`
  (respecting the existing asc/desc order param).
- **JS** (`templates/index.html` `sortGames()` default + the alphabet-bar key):
  key on `title` only (drop `series_name || title`).

Other explicit sort options (rating, priority, metacritic, manual, newest) are
unchanged except for removing any series tiebreaker.

### Part C — Collection display setting (non-destructive)

**Stable link.** Add `games.parent_collection_id INTEGER NULL` (FK → `games.id`).
A one-time, idempotent linking migration associates each member row with its
container row **where a container exists**, using a normalized match
(case-insensitive, whitespace/punctuation-collapsed) between `collection_name` and
candidate container titles. `collection_name` is retained as the display label.
Members whose container was deleted keep `parent_collection_id = NULL`.

A row is a **container** if its `id` is referenced by some other row's
`parent_collection_id`. A row is a **member** if its own `parent_collection_id` is set.

**Setting.** Add `user_profile.collection_display_mode TEXT` (default `'members'`),
exposed through the existing `GET/PUT /api/profile`. Allowed values:
`members`, `collection`, `both`. Unknown/NULL is treated as `members`.

**Filter** (applied in `/api/games`):

| mode | rule |
|------|------|
| `members` (default) | exclude rows that are containers with ≥1 member; include members and standalone games |
| `collection` | include container rows; exclude rows that have a `parent_collection_id`; members whose container was deleted (NULL parent) still show (best-effort fallback) |
| `both` | include everything (today's behavior) |

Nothing is deleted; the mode only changes visibility. A game that is neither a
container nor a member is always shown.

**Web UI.** A single select in `templates/settings.html` ("Collection display":
Member games / Collection / Both), reading and writing `collection_display_mode`
via `/api/profile`.

## Testing

- **Series removal:** app boots, `/api/games` returns 200 with alphabetical order,
  no `/series*` routes remain, migrations run clean on a fresh DB and on a copy of
  the real DB, remaining test suite green after excising series assertions.
- **Linking migration:** on a DB copy, the 18 known container+member compilations
  link correctly; Mega Man Legacy Collection members stay NULL; idempotent on rerun.
- **Display filter:** unit tests for each mode against a fixture with (a) a
  container+members compilation, (b) a members-only compilation, (c) a standalone
  game; assert exact visible-id sets. Default mode hides the Battle Network /
  Legacy of Thieves containers.
- **Setting round-trip:** `PUT /api/profile` then `GET` returns the saved mode;
  invalid values rejected/normalized.

## Rollout

- Work on `main`, commit incrementally, per repo convention.
- Back up the live DB before running the linking/column-drop migrations on it.
- Verify in a real browser on a DB copy (isolated port) before the live restart.
