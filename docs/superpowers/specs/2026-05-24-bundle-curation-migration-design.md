# Bundle curation migration — design

**Date:** 2026-05-24
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

## Goal

Make `cleanup_bundles` resolve **curated** bundle phantoms automatically: migrate
the phantom's curation onto its constituents (fill-only), then delete the phantom
— instead of leaving curated phantoms in the library for manual UI handling.

A fresh re-import already never creates these phantoms (`import_games` expands a
known bundle and skips the bundle row, `import_scraped.py:299-306`). So curated
phantoms only persist because the original one-time cleanup pass kept them as a
safety guard (`_is_curated` → `kept_curated`). This work brings the live DB to the
same clean state a re-import would produce — table-driven, not a one-off SQL patch.

This follows the project principle: every cleanup is durable, general pipeline
logic plus an extensible curated table (`BUNDLE_CONTENTS`), never a one-time
`games.db` edit. See `cleanup-fixes-must-be-general`.

## Background — current live state (6 curated phantoms)

| id | phantom | meaningful curation | constituents (current) |
|---|---|---|---|
| 670 | Pikmin 1+2 Bundle | status = completed | Pikmin 1 (385), Pikmin 2 (386) — backlog |
| 570 | Nobody Saves the World + Frozen Hearth Bundle | status = playing | Nobody Saves the World (831) — backlog |
| 501 | Batman: Return to Arkham | series = Batman (5) | Arkham Asylum (833), Arkham City (834) — no series |
| 488 | Assassin's Creed Chronicles | series = Assassin's Creed (1) | Chronicles China/India/Russia (835/836/837) — no series |
| 610 | Watch Dogs 1 + 2 ... Bundle | sort_order only (positional) | Watch Dogs (827), Watch Dogs 2 (828) — fine |
| 576 | Prototype: Biohazard Bundle | sort_order only (positional) | Prototype (829), Prototype 2 (830) — fine |

`sort_order` alone tripped the `_is_curated` guard for all six (its default is
NULL), which is why they were all kept even though four carry no real curation
beyond a list position.

## Why "not a game" is the wrong tool here

Marking a bundle SKU "not a game" writes it to `excluded_games.json`. In
`import_games`, the exclusion check (`import_scraped.py:296`) runs **before** the
bundle-expansion check (`:299`). So excluding a bundle SKU would make a fresh
re-import skip it entirely and never expand it — the constituents would silently
disappear. The phantom must be **deleted**, not excluded.

## Migration rule (fill-only)

For each curated phantom whose constituents have been ensured to exist (via the
existing `import_games(synth, ...)` step in `cleanup_bundles`), resolve each
constituent title to its `game_id`, then:

| Phantom field(s) | Action |
|---|---|
| `status` (+ `started_at`, `completed_at`) | Copy onto each constituent whose `status` is still default (`backlog`). Skip any constituent already progressed. Applied to **all** at-default constituents. |
| `series_id` | Append each not-yet-in-a-series constituent to the phantom's series: set `series_id` + `series_order = MAX(series_order)+1` for that series (replicating the append logic at `app.py:654-667`). Skip constituents already in any series. |
| `sort_order`, `priority` | **Dropped** — positional/ordering hints; constituents have their own. |
| `rating`, `notes`, `hours_played` | If any is non-default → **do not delete** the phantom: keep it, report `kept_ambiguous`. A single rating/notes/hours value cannot be meaningfully split across multiple constituents; leave for the user. (None of the current 6 hit this.) |

After migration, **delete the phantom row** (FK `ON DELETE CASCADE` clears its
platform link, external id, and rating). If the phantom was `kept_ambiguous`, it
is not deleted.

"Fill-only" is the core safety property: migration never overwrites curation the
user already set on a constituent.

### DLC

DLC constituents are **dropped** from bundles — never created, never merged into
the base game. The data model tracks games; DLC ownership is not a tracked
entity, so "merge DLC into the base game" is not an operation that exists. This is
already encoded in `BUNDLE_CONTENTS` (e.g. "Frozen Hearth" omitted from the Nobody
Saves bundle, `bundles.py:28-29`). This migration concerns **base-game
constituents only**; the Nobody Saves phantom's `status=playing` migrates onto
"Nobody Saves the World" alone.

## Flag + safety (mirrors `--canonical-titles --include-flagged`)

- `--cleanup-bundles` (default): **unchanged** — uncurated phantoms deleted,
  curated kept + reported (`kept_curated`).
- `--cleanup-bundles --include-curated`: also migrate-and-delete curated phantoms;
  ambiguous ones reported `kept_ambiguous`.
- Both honor `--dry-run` (writes nothing, full report).
- Report `action` values: `deleted` (uncurated), `migrated_deleted` (with the
  fields/series migrated and to which constituents), `kept_ambiguous`,
  `kept_curated` (only when `--include-curated` is absent).
- Live apply order: fresh `games.db.bak-*` → `--dry-run` preview on real data →
  user OK → apply. Per `canonical-rename-equality-only`.

Idempotent: re-running `--include-curated` is a no-op (phantom gone; constituents
already filled, so fill-only changes nothing).

## Expected effect on the 6 (fill-only preview)

- 670 Pikmin 1+2 (completed) → Pikmin 1/2 set `completed`; phantom deleted.
- 570 Nobody Saves (playing) → Nobody Saves the World set `playing`; phantom deleted.
- 501 Batman: Return to Arkham (Batman series) → Arkham Asylum/City appended to
  Batman series; phantom deleted.
- 488 AC Chronicles (AC series) → China/India/Russia appended to AC series;
  phantom deleted.
- 610 Watch Dogs, 576 Prototype (sort_order only) → nothing to migrate; phantom
  deleted.

All six → `migrated_deleted` (or plain delete where nothing migrates).

## Code

- New helper in `import_scraped.py`, beside `_is_curated`/`_DEFAULT_CURATION`:
  `_migrate_bundle_curation(conn, bundle_id, constituent_ids, *, dry_run) -> dict`
  returning what it migrated (for the report). Pure-ish: reads phantom's
  `user_ratings`, applies the fill-only rule to the given constituents.
- `cleanup_bundles` gains an `include_curated: bool = False` param; when a phantom
  is curated and `include_curated` is set, run migration → delete (unless
  ambiguous). Default path unchanged.
- Resolve constituent title → `game_id` using the same normalized-title lookup the
  importer already uses (so it matches the rows `import_games` just ensured).
- CLI: add `--include-curated` (only meaningful with `--cleanup-bundles`).

`import_scraped.py` is currently 511 lines; this adds ~40. Extracting a
`library_filters.py` (non-game + exclusion logic) is a **separate** follow-up, out
of scope here.

## Testing (TDD, temp DB via `tests/conftest.py`)

- Fill-only: a constituent already `completed` / already in a series is **not**
  overwritten.
- `status` migrates to all at-default constituents; `started_at`/`completed_at`
  ride along.
- `series_id` migration appends with correct next `series_order`; a constituent
  already in another series is left alone.
- Ambiguous fields (non-default `rating`/`notes`/`hours_played`) → phantom
  `kept_ambiguous`, **not** deleted, no migration.
- `dry_run=True` writes nothing (phantom still present, constituents unchanged).
- Idempotent: second `include_curated` run is a no-op.
- Default (no `include_curated`): curated phantom still `kept_curated`, not deleted
  (existing behavior preserved).
- Existing tests (173) stay green.

## Future / out of scope

- **"Mark as DLC"**: a UI action (or automatic detection) to tag a row as DLC so
  DLC dupes in the dedup list can be resolved without merging into the base game.
  The user will leave DLC dupes alone for now. Not part of this work.
- **Stray standalone DLC rows** imported as their own "games" (not via a bundle):
  handled by the "not a game" exclusion path, not by this migration.
- **`library_filters.py` extraction** from `import_scraped.py` (file > 400 lines).

## Constraints

- Public repo: never commit `games.db`, `games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `excluded_games.json`, `series_patterns.json`
  (all gitignored; `series_patterns.default.json` is committed).
- Conventional commit style, no co-author trailer. Work on `main`.
- Back up `games.db` before any live mutation; `--dry-run` preview + user OK before
  applying the curated pass.
