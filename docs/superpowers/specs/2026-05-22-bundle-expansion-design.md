# Bundle expansion — design

**Date:** 2026-05-22
**Branch:** TBD (feature branch off main)
**Status:** approach approved; ready for implementation plan

## Goal

A purchased *bundle* (a single store product that grants several separately
released games) should not appear in the library as its own phantom game. Buying
it means owning its constituents. Expand known bundles into their individual
games and remove the phantom bundle entry.

Trigger case: the Nintendo import created `"Edna & Harvey" Bundle` (id 803) as a
third game alongside the two games it grants, which were already in the library
(`Edna & Harvey: Harvey's New Eyes` 277, `...the Breakout` 278).

Bundles are not Nintendo-only — Xbox and PSN have them too — so the mechanism is
vendor-agnostic, keyed by `(source, external_id)`.

## Non-goals

- Title normalization (leading `(English)`, casing like "the Breakout", leading
  quotes). That is the **next** track, handled separately after this.
- Auto-discovering bundle contents from vendor APIs. Contents come from a small
  curated map (only ~8 entries; distinguishing a true bundle from a single
  compilation needs human judgment anyway).
- Expanding single-product compilations/remasters that are played as one product
  (Halo MCC, Bioshock Collection, GTA Trilogy, Uncharted/Castlevania/Mega Man
  Legacy collections, etc.). These deliberately stay one game.

## Mechanism

### 1. Curated map (`bundles.py`)

`import_scraped.py` is ~345 lines; adding bundle data + logic would push it past
the 400-line split threshold. Put the bundle map and its pure helpers in a new
`bundles.py`.

```python
# (source, external_id) -> constituent canonical titles
BUNDLE_CONTENTS: dict[tuple[str, str], tuple[str, ...]] = { ... }
```

Constituent titles are matched through the importer's existing cascade
(`normalized_title`), so casing/punctuation need not be exact. For constituents
that do not yet exist, the title is the display title for the created game
(`clean_title` applies).

`expand_bundle(source, external_id) -> tuple[str, ...] | None` returns the
constituent titles for a known bundle, else `None`.

### 2. Expand on import

In `import_games`, before the normal match/create for a scraped game: if
`expand_bundle(source, external_id)` returns constituents, import each
constituent instead (synthesized game dict: `title`, `platform` = the bundle's
platform, `source`, `external_id=None`, `cover_url=None`), running each through
the normal cascade (match existing → add platform link + ownership; missing →
create). The bundle itself is never created.

Idempotent: a re-scrape re-encounters the bundle's id, re-expands, and the
constituents match by title — no phantom is ever (re)created. The bundle's own
external_id is intentionally dropped (the `(source, external_id)` PK can only map
one id to one game; the map, not a stored id, guarantees idempotency).

### 3. One-time cleanup of already-imported phantoms

The prior import already created phantom bundle rows. Expose a one-shot pass
(flag on `import_scraped.py`, `--cleanup-bundles`, honoring `--dry-run`):

For each `(source, external_id)` in the map that exists as a game row:
1. Expand its constituents (ensure they exist + own them on the bundle's
   platform) — same core as the import path.
2. Delete the phantom bundle row **only if it carries no user curation**;
   FK `ON DELETE CASCADE` removes its platform link, external id, and rating.
   "Uncurated" = `user_ratings` is default: status `backlog`, `rating`/`notes`/
   `series_id`/`started_at`/`completed_at`/`sort_order` all NULL, `hours_played`
   0, `priority` 5.
3. If curated (e.g. `Pikmin 1+2 Bundle` id 670 / `Portal: Companion Collection`
   804 are low ids that may predate the import and carry status), do NOT delete —
   report it for manual handling.

### Map contents (approved)

| (source, external_id) | bundle | constituents |
|---|---|---|
| nintendo, 70070000014767 | "Edna & Harvey" Bundle | Edna & Harvey: Harvey's New Eyes; Edna & Harvey: the Breakout - Anniversary Edition |
| nintendo, 70070000018036 | Pikmin 1+2 Bundle | Pikmin 1; Pikmin 2 |
| nintendo, 70070000025556 | SteamWorld Heist II & SteamWorld Build Bundle | SteamWorld Heist II; SteamWorld Build |
| nintendo, 70070000013722 | Portal: Companion Collection | Portal; Portal 2 |
| xbox, BTNQR63WQV3G | Watch Dogs 1 + Watch Dogs 2 Gold Editions Bundle | Watch Dogs; Watch Dogs 2 |
| xbox, BR64DHW9XK6B | Prototype Biohazard Bundle | Prototype; Prototype 2 |
| xbox, 9P6KBLVP8V3G | Nobody Saves the World + Frozen Hearth Bundle | Nobody Saves the World |
| xbox, C4DQHRNN1ZN5 | Borderlands: The Handsome Collection | Borderlands 2; Borderlands: The Pre-Sequel |

First four: all constituents already exist (clean expand, zero new games).
Last four: create the missing constituents (Watch Dogs ×2, Prototype ×2, Nobody
Saves the World ×1, Borderlands: The Pre-Sequel ×1; "Nobody Saves… Frozen
Hearth" is DLC and is dropped). Note `Bio Prototype` (207) is a *different* game,
not a Prototype constituent.

## Testing

- `expand_bundle`: returns constituents for a mapped id, `None` otherwise.
- Import path (in-memory DB, existing conftest): a scraped bundle expands to
  constituents (existing matched, missing created), the bundle game is never
  created, and a second import is idempotent.
- Cleanup path: an uncurated phantom bundle is expanded then deleted; a curated
  one is expanded but kept and reported. `--dry-run` writes nothing.
- Existing 39 tests stay green.

## Constraints

- Public repo: never commit `games.db`, `games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json` (all gitignored).
- Conventional commit style, no co-author trailer.
- `bundles.py` keeps `import_scraped.py` under the 400-line split threshold.
