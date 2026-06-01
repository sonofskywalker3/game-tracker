# Catalog-Driven Series Defaulting — Design (Spec B)

**Date:** 2026-06-01
**Status:** Approved in brainstorming; ready to plan.
**Arc:** Spec A (bundle/compilation expansion, landed) → **Spec B (this) → Spec C (trait seed)**.

## Problem

The library assigns games to series with `models.auto_populate_series()`, which
**prefix-matches** each title against `series_patterns.default.json` (a
`prefix → series-name` table), creates a series only when **≥2 games** match, and
never overwrites an existing `series_id`.

Prefix-matching structurally misses any game whose **canonical title does not begin
with the series name** — e.g. *Assassin's Creed Brotherhood* / *Revelations*,
*Super Castlevania IV*, *Grand Theft Auto: Vice City* / *San Andreas*. The Spec A
bundle apply also created ~107 broken-out constituents (Mega Man 1–6, the AC Ezio
trilogy, Castlevania classics, etc.) that are not yet in any series. Prefix-matching
also carries **no per-game ordering or role** information.

**Goal (owner's words):** the catalog should carry each game's series so that games
the AI knows belong to a series — but which aren't manually assigned — get
**defaulted** into that series. Extend defaulting beyond prefix-matching to explicit
per-title (AI/curated) knowledge, and let that catalog also own within-series
**order** and **role** (mainline/spinoff).

## Approach (chosen)

A new **per-title series catalog** that **coexists with** the existing prefix table:

- Prefix-matching (`series_patterns`) keeps doing the broad work and keeps catching
  **future imports** that aren't in the catalog yet.
- The per-title catalog handles what prefix cannot: non-prefix titles, plus `order`
  and `role` for everyone.
- **On conflict the catalog wins** (it is explicit), so it can correct a wrong prefix
  grouping — but it never touches a **manual** assignment.

This mirrors the established `game_traits` / `bundle_catalog` pattern: a committed
`*.default.json` seed (+ gitignored per-user override), a `load_*` loader, and an
idempotent, **fill-only** `apply_*` that the controller (not impl subagents) runs
against the live DB. It honors:
- `cleanup-fixes-must-be-general` — durable pipeline logic + an extensible curated
  table, never a one-off SQL patch.
- `canonical-rename-equality-only` — fill-only writes, dry-run on real data + backup
  before any live apply, match by `normalized_title` (never a returned id).
- `subagent-impl-never-touch-live-db` — AI/impl agents return data; the controller writes.
- `slate-system-must-be-generic` — the catalog seeds defaults for any user; no behavior
  hardcoded to the owner.

### Approaches considered & rejected
- **Fold prefix-matching into the per-title catalog (deprecate `series_patterns`).**
  Rejected: a future-imported game not yet in the catalog would get **no** series,
  losing prefix-matching's coverage for future imports/users.
- **Extend the prefix table with more patterns/regex** (`"Brotherhood" → AC`).
  Rejected: ambiguous across series, collides, and cannot carry per-game order/role.

## Data model & files

**New committed seed:** `series_catalog.default.json`, keyed by `normalized_title`
(mirrors `game_traits.default.json`). Per-user override `series_catalog.json`
(gitignored); default committed.

```json
{
  "assassins creed brotherhood": { "series": "Assassin's Creed", "order": 3, "role": "spinoff" },
  "super castlevania iv":        { "series": "Castlevania",      "order": 4, "role": "mainline" },
  "mega man 2":                  { "series": "Mega Man",         "order": 2, "role": "mainline" }
}
```

- `series` — series **name** string; created if missing (subject to the ≥2 rule below).
- `order` — integer within-series sequence → `user_ratings.series_order`.
- `role` — `"mainline" | "spinoff"` → `games.series_role` (existing column/enum).
- Each field is **optional**; a missing field is a no-op for that field. An absent
  entry, a missing file, or malformed JSON is a safe no-op (loader returns `{}`).

**Loader:** `models.load_series_catalog()` — twin of `load_series_patterns` /
`load_game_traits` (per-user file if it exists, else committed default; JSON-decode-safe).

**No new tables.** Reuses `series (id, name UNIQUE)`, `user_ratings.series_id` /
`series_order`, and `games.series_role` / `series_role_source` — all already present.

**One new column:** `user_ratings.series_source` (`'auto' | 'catalog' | 'manual'`,
NULL allowed) — source tracking for `series_id`, mirroring the trait-source pattern.

### Cross-spec consequence (Spec C)
Because the series catalog now **owns `role`**, Spec C's `game_traits.default.json`
and trait-apply **drop `series_role`** and carry **only `session_length`**. The
`games.series_role` column and `apply_traits_catalog`'s handling of it are **moved
into** this spec's apply. (`apply_traits_catalog` continues to own `session_length`.)

## The apply algorithm

`models.apply_series_catalog(conn, game_id=None)` — idempotent, fill-only, twin of
`apply_traits_catalog`. `game_id=None` processes the whole library (startup); a
specific id processes one game (on add). Two passes:

**Pass A — membership + order (per series):**
1. Group catalog-matched games by target `series` name (match by `normalized_title`).
2. Decide whether to act on a series name:
   - Series **already exists** in the `series` table → **join always** (even one straggler).
   - Series **does not exist** → count library games mapping to it (catalog-matched
     **plus** any prefix-assignable); **create only if ≥2**, else skip.
3. For each game to assign, write `series_id` + `series_order = catalog.order` and
   `series_source = 'catalog'` **only when the current assignment is not `manual`**
   (i.e. `series_id IS NULL` or `series_source IN ('auto','catalog')`). If `order` is
   absent, fall back to the current title-sort ordering.

**Pass B — role (per game):** write `games.series_role = catalog.role`,
`series_role_source = 'catalog'`, **skipping** rows where `series_role_source = 'manual'`.
Runs **independently of membership**, so games already correctly prefix-grouped still
get their role filled.

**Precedence:** the catalog is authoritative over prefix-matching not by call order
but by the fill rule — it may overwrite `NULL`/`auto`/`catalog` assignments but
**never `manual`**. So whether `auto_populate_series` ran before or after, the
catalog's explicit assignment wins over a prefix-`auto` one while a hand-curated
`manual` assignment is always preserved.

## Source tracking for `series_id`

`user_ratings.series_id` is today a bare FK with no provenance, so prefix-auto and
hand-curated assignments are indistinguishable. The new `series_source` column fixes
this:
- `auto_populate_series` stamps `'auto'` on what it assigns.
- `apply_series_catalog` stamps `'catalog'` (may overwrite `NULL/auto/catalog`, never `manual`).
- Series UI write paths (`/api/series/from-group`, `/api/series/<id>/games`,
  `/api/series/<id>/reorder`, `PUT /api/series/<id>`) stamp `'manual'` → locked.

### Backfill (chosen: reconstruct auto vs manual)
During migration, for each existing assignment, recompute the prefix match for that
game's title:
- current series **== prefix-match result** → stamp `'auto'` (catalog may re-home it).
- otherwise → stamp `'manual'` (a human must have set it; locked).

This protects hand-built groupings while letting the catalog correct genuine prefix
groupings.

## Building & applying the catalog (controller-run)

Mirrors Spec A. **Impl subagents never touch the live DB or run the app**; the
**controller** runs the workflow and all live writes.

1. **AI-draft workflow** (Claude Code, free on subscription): classify all ~764 titles.
   Each agent processes a batch (~20 titles) and **returns verdicts**:
   `{ normalized_title, series, order, role }` or `standalone`. Match results back **by
   `normalized_title`, never a returned id** (the AI transposes ids — the Bloodstained
   lesson). The batch list is **embedded as a `const` literal in the script body** with
   an `Array.isArray(...)/length` guard — **never** passed through the Workflow `args`
   channel (that arrives as a string and shreds into char-fragment agents; cost 1.8M
   tokens once).
2. **Pilot first:** one ~20-game batch; owner reviews verdict quality and the
   mainline/spinoff judgment before the full run.
3. **Owner review + correct**, then commit `series_catalog.default.json`.
4. **Apply to live DB:** a `--apply-series-catalog` CLI flag → **dry-run first** (prints
   create / join / assign / role-set counts and every re-home), owner OKs, **back up
   `games.db`** (`games.db.bak-20260601-pre-series-apply`), then live apply, then report.

The CLI runner + dry-run logic live in a small module (e.g. `series_pipeline.py`)
rather than bloating `models.py` (the file-size rule; `import_scraped.py` was already
flagged for a similar split).

## Integration points (durable pipeline)

- `migrate_db()`: add `series_source` column + reconstruction backfill, then call
  `apply_series_catalog(conn)`. (`migrate_db` does not itself run
  `auto_populate_series` — that stays endpoint-triggered; the catalog's authority comes
  from the fill rule, not call order.)
- On game add (app.py, beside `apply_traits_catalog(conn, game_id)`): call
  `apply_series_catalog(conn, game_id)` so future imports auto-join their series.
- `auto_populate_series` and the series UI write paths updated to stamp `series_source`.

## Testing (strict TDD, pytest temp DBs only)

- `load_series_catalog`: per-user override wins; default fallback; missing/malformed → `{}`.
- `apply_series_catalog`:
  - join existing series always (single straggler joins);
  - create new series only at ≥2 (singleton new series skipped);
  - fill-only on `series_id` (NULL / `auto` / `catalog` written; `manual` never touched);
  - `order` → `series_order`; absent `order` → title-sort fallback;
  - `role` → `series_role` with `source='catalog'`; `manual` role skipped;
  - absent entry / missing file → no-op; single-`game_id` path.
- Backfill reconstruction: prefix-equal → `auto`; non-prefix → `manual`.
- Regression: `auto_populate_series` still assigns and now stamps `auto`.

**No new UI required** (series already render). A "defaulted vs manual" badge is a
possible follow-up and is **out of scope** here.

**Commands:** tests `uv run python -m pytest`; lint `ruff check` only (never `ruff format`).

## Non-goals / out of scope

- UI changes (defaulted-vs-manual badge).
- Deprecating or replacing the prefix table.
- Renaming/consolidating existing series (fill-only only).
- Spec C's trait seed (separate spec; this only shrinks its catalog to `session_length`).
