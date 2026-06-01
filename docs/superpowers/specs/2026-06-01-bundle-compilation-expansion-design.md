# Bundle / compilation expansion — design (Spec A)

**Date:** 2026-06-01
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved in brainstorming; ready for implementation plan (A1)

First of three sequenced specs that must land before the trait classification seed runs:
**Spec A — bundle/compilation expansion** (this doc) → **Spec B — catalog-driven series
defaulting** → **Spec C — the trait seed** (already designed in
`2026-06-01-session-traits-and-series-focus-design.md`, just re-sequenced to run last).

Builds on the just-shipped trait catalog ([[slate-picks-tab-feature]],
`game_traits.default.json`). Honors [[cleanup-fixes-must-be-general]] (catalog + tables,
not one-off patches), [[canonical-rename-equality-only]] (destructive bulk changes need
exact match + dry-run-on-real-data approval), [[subagent-impl-never-touch-live-db]] (the
AI draft RETURNS data; the controller writes the DB with a backup),
[[work-on-main-no-branches]], [[run-tests-with-python-m-pytest]], [[ruff-check-not-format]].

## Problem

The library contains multi-game products stored as a single tile. The trait seed would
misclassify them: e.g. *Assassin's Creed: The Ezio Collection* is one tile but contains
AC II / Brotherhood / Revelations (each a mainline game); the pilot tagged the whole
collection "spinoff". Before classifying traits we must break such products into their
constituent games so each is classified, slotted, and tracked on its own — while keeping
a cue for what to actually launch.

## Goal

A committed, AI-drafted, owner-curated **enrichment catalog** records each multi-game
product's *type* and *constituent titles*. A gated, dry-run-first apply step breaks
products out per type, deletes the now-redundant parent tile (except anthologies), and
marks launcher-compilation constituents with a collection label that drives a tile badge
+ a detail-view "what to launch" cue. Anthologies stay a single tile (their contents-list
+ on-demand promote is **deferred to A2**).

## Decisions (approved in brainstorming)

1. **Three product types** (anything not in the catalog is a normal standalone game):
   - **`compilation`** — a single launcher app you open, then pick a game (Mega Man /
     Castlevania Legacy/Anniversary, Ezio Collection, BioShock: The Collection,
     `.Hack//G.U. Last Recode`, Chrono Cross: Radical Dreamers Edition). Break out → each
     constituent is a tracked game carrying the collection name → **delete the parent
     tile**. Constituents show a **stacked-cards badge** on the tile and a **"Part of
     <collection>"** line in the detail view (the launch cue). Platform icons unchanged.
   - **`entitlement`** — buying grants several **separately launched** games (Pikmin 1+2,
     Portal: Companion Collection, the Borderlands pack). Break out → **delete the parent
     tile** → constituents are **plain standalone tiles, no badge / no collection name**
     (≈ today's `cleanup_bundles` behavior).
   - **`anthology`** — a museum of many micro-games (Atari 50, UFO 50, Rare Replay's
     emulated extras). **Keep as one tile.** Store its contents list for **A2** (on-demand
     promote/check-off). Do NOT flood the library. *(A1 stores nothing beyond leaving the
     tile intact; the contents table + promote UI is A2.)*
2. **Single-game "editions" are NOT compilations** — "BioShock Infinite: The Complete
   Edition", "Bulletstorm: Full Clip Edition" are one game + DLC/remaster naming. They are
   absent from the catalog (type effectively standalone) and never broken out.
3. **Source/apply = catalog-driven, AI-drafted, dry-run apply.** A committed
   `bundle_catalog.default.json` (per-user gitignored override `bundle_catalog.json`) is
   the only thing the apply step reads. A Claude-Code Workflow DRAFTS it; the owner
   reviews/edits and merges by PR. Apply is **idempotent, dry-run-first, DB backed up**,
   and never auto-runs at startup (it creates/deletes rows). Manual/owner catalog edits
   are authoritative.
4. **Reuse, don't reinvent.** The breakout machinery already exists in `import_scraped`
   (`_resolve_constituent_ids`, the import expansion, `_migrate_bundle_curation`
   fill-only status/series migration, `cleanup_bundles`) and `bundles.py`
   (`expand_bundle`, the curated `(source, external_id)` map). Spec A adds a
   **normalized-title-keyed catalog** alongside the vendor-id map, a `type` distinction,
   and the `collection_name` label; it routes through the existing create-constituents +
   migrate-curation + delete-parent flow.
5. **`collection_name` is an identity fact on `games`**, not a per-user rating. Non-null →
   badge + "Part of …". Set only for `compilation` constituents.
6. **Scope split: build A1 now, defer A2.**
   - **A1:** catalog file + `collection_name` migration + `apply_bundle_catalog` (compilation
     + entitlement) + badge/subtitle UI + the AI draft Workflow.
   - **A2 (deferred):** anthology `collection_contents` table + on-demand promote/check-off UI.

## Data model

### `games.collection_name` (new; idempotent `migrate_collection_name`)
`collection_name TEXT` nullable. The display title of the launcher compilation a
constituent belongs to (e.g. "Mega Man Legacy Collection"). Non-null drives the tile
badge + the modal "Part of <collection_name>" line. Added via guarded `ALTER TABLE games
ADD COLUMN` (mirrors `migrate_game_traits`). Null for standalone and entitlement games.

### `bundle_catalog.default.json` (committed) + `bundle_catalog.json` (gitignored)
Keyed by the parent product's `normalized_title`:
```json
{
  "mega man legacy collection": {
    "type": "compilation",
    "constituents": ["Mega Man", "Mega Man 2", "Mega Man 3",
                     "Mega Man 4", "Mega Man 5", "Mega Man 6"]
  },
  "assassins creed the ezio collection": {
    "type": "compilation",
    "constituents": ["Assassin's Creed II",
                     "Assassin's Creed Brotherhood",
                     "Assassin's Creed Revelations"]
  },
  "pikmin 12": { "type": "entitlement", "constituents": ["Pikmin 1", "Pikmin 2"] },
  "atari 50 the anniversary celebration": { "type": "anthology", "constituents": [] }
}
```
`type` ∈ `{compilation, entitlement, anthology}`. `constituents` is a list of display
titles (matched/created through the importer's `clean_title`+`normalize_title` cascade,
so casing/punctuation need not be exact). Anthology `constituents` may be empty in A1
(the list matters in A2). `models.load_bundle_catalog()` loads the per-user file if
present else the committed default (mirrors `load_series_patterns` / `load_game_traits`).

### `collection_contents` (A2 — deferred, specified for completeness)
`(parent_game_id, title, promoted_game_id NULL, tackled INTEGER DEFAULT 0)` — one row per
title an anthology contains; `promoted_game_id` set once the owner promotes it to a real
badged game. Not created in A1.

## Apply pipeline — `apply_bundle_catalog(conn, *, dry_run=False)`

Gated controller/admin operation (NOT in `migrate_db`). Idempotent. Returns a structured
report of actions for the dry-run preview. For each catalog entry (keyed by parent
`normalized_title`):

- Resolve the parent game by `normalized_title`. If absent → skip (nothing owned).
- **`anthology`** → no-op in A1 (leave the parent tile; A2 fills `collection_contents`).
- **`compilation` / `entitlement`**:
  1. Find-or-create each constituent game by title (reuse the importer's create path so
     `clean_title`/`normalize_title` and existing-row matching apply; a missing
     constituent is created from the catalog title).
  2. Inherit the parent's platform link(s) onto newly-created constituents (a constituent
     should be owned on the platform the bundle was owned on).
  3. **Migrate parent curation fill-only** to the constituents via the existing
     `_migrate_bundle_curation` semantics (status/series filled only where the constituent
     is still at defaults; never clobber a user value; ambiguous rating/notes → leave the
     parent, report `kept_ambiguous`, matching today's `cleanup_bundles`).
  4. **`compilation` only:** set `collection_name = <parent display title>` on every
     constituent.
  5. Delete the parent game row (CASCADE clears its platform/tag/rating rows) — unless the
     curation migration flagged it ambiguous (then keep it + report), mirroring
     `cleanup_bundles(include_curated=…)`.
- `dry_run=True` writes nothing and returns the same report (counts: constituents created
  / matched, parents deleted / kept-ambiguous, collection_name set).

The controller runs this once (with a `games.db` backup) after reviewing the dry-run; it
is re-runnable (idempotent — already-broken-out entries no-op). Future scraped imports of
a catalogued product expand through the same path (extend the importer's existing
`expand_bundle` call to also consult `load_bundle_catalog()` by normalized_title, not only
the vendor-id map).

## AI draft (controller-run Workflow)

A Workflow over candidate multi-game products (heuristic pre-filter: titles containing
`collection|compilation|trilogy|anthology|legacy|bundle|edition|+|&|/` etc., plus all
games — the agent decides). Each agent receives `{normalized_title, title, platforms}`
batches (~20) and RETURNS, per product:
`{normalized_title, type: compilation|entitlement|anthology|standalone, constituents:
[titles], reason}` (StructuredOutput schema). The controller:
1. matches each verdict to the authoritative game by **`normalized_title`** (never trusts
   a returned id — the trait pilot proved the model can transpose ids);
2. drops `standalone` verdicts; merges the rest into `bundle_catalog.default.json` (sorted)
   for the owner to review + PR;
3. logs counts + low-confidence items.
Agents never touch `games.db`. The owner curates the emitted catalog before any apply.

## UI

- **Tile** (`gameCardHtml` in `base.html`): when `game.collection_name` is set, render the
  **stacked-cards badge** (three offset cards, top-right) approved in brainstorming. No
  text on the tile; platform icons unchanged.
- **Detail view** (game modal): when `collection_name` is set, a **"Part of
  <collection_name>"** line (with the small stacked glyph) under the title — the launch
  cue. Read-only in A1.
- `GET /api/games/<id>` already returns `collection_name` via `SELECT g.*`; the tile/search
  payloads that feed `gameCardHtml` must include `collection_name` (extend the
  `/api/games` and `/api/games/search` SELECTs if not already `g.*`).

## Error handling

- Catalog missing/malformed → empty dict, no crash (mirrors `load_series_patterns`).
- A catalogued parent not owned → skipped (no-op).
- Unknown `type` value → skipped + logged (never guessed).
- Apply is dry-run-first + backed up; ambiguous curation is preserved, never silently lost.
- `collection_name` null is always safe (no badge, normal game).

## Testing (per conventions; `uv run python -m pytest`, temp DB, `ruff check`)

- `migrate_collection_name`: column added, idempotent.
- `load_bundle_catalog`: per-user precedence, missing/malformed → `{}`.
- `apply_bundle_catalog` (temp DB): a `compilation` creates constituents, sets
  `collection_name`, deletes the parent; an `entitlement` creates constituents WITHOUT
  `collection_name` and deletes the parent; an `anthology` is a no-op (parent kept);
  curation migrates fill-only (don't clobber a user value); ambiguous curation keeps the
  parent; `dry_run=True` writes nothing; re-running is idempotent.
- platform inheritance: a created constituent owns the parent's platform.
- API/render: `/api/games` payload includes `collection_name`; tile renders the badge when
  set; modal renders the "Part of …" line when set; both absent when null.

## Scope / sequence

- **A1 (this build):** `migrate_collection_name`, `load_bundle_catalog` +
  `bundle_catalog.default.json` (seeded empty or with a few hand-verified entries),
  `apply_bundle_catalog` (compilation + entitlement, dry-run + report), badge + detail UI,
  the AI draft Workflow + catalog emit. Delivers a library of individual games ready for
  the trait seed.
- **A2 (deferred):** `collection_contents` table, anthology contents-list UI, on-demand
  promote/check-off.
- **Then Spec B** (series defaulting) **→ Spec C** (trait seed) on the cleaned library.

## Open items to confirm at implementation time

- Whether `/api/games` and `/api/games/search` already `SELECT g.*` (so `collection_name`
  rides along) or need the column added explicitly.
- The exact reusable surface of `import_scraped` for find-or-create of a constituent by
  title outside an import run (may need a small extracted helper).
- Seeding content for the committed `bundle_catalog.default.json` at A1 ship: empty (owner
  fills via the AI draft + PR) vs. a few hand-verified entries (Ezio, BioShock Collection,
  Mega Man Legacy) to exercise the path end-to-end.
