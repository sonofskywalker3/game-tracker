# DLC ownership visibility — design

**Date:** 2026-05-25
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

## Goal

Make scraped DLC ownership real and visible:

1. **PSN owned add-on capture** — the PlayStation scraper detects owned add-ons so
   `mark_ownership` can flip their DLC to owned (today PSN scrapes only base games,
   so PSN DLC always shows 0 owned). *Recon-gated* (needs one user capture).
2. **Scrape result list** — after a scrape, show the DLC it imported this run, each
   with an owned / not-owned badge (replacing the bare "Refresh to see them"). Per
   user: a per-scrape view, **not** a persistent DLC page; **view-only**.
3. **Hero DLC tracker** — an `<owned>/<total> DLC` tile on the library hero bar.

Context that prompted this: a PSN scrape reported "130 DLC added, 0 owned". The 130
is IGDB's *catalogue* of DLC that exists for the user's games (not an ownership
claim); "0 owned" is because PSN ownership was never captured — not because the user
owns none. Features 1–3 close that gap.

## Background (verified facts)

- `dlc` table: `id, game_id, name, igdb_id, kind, owned (0/1), source, created_at
  (DEFAULT CURRENT_TIMESTAMP)`, `UNIQUE(game_id, name)` (`models.py`).
- Enrichment: `import_scraped.run_dlc_enrichment` → `igdb_dlc.enrich_missing`
  (returns totals `{games, matched, added, errors}`); `igdb_dlc.merge_dlc` inserts
  new DLC and returns `{added, existing}`.
- Ownership: `dlc_ownership.mark_ownership(conn, addons) -> OwnershipReport`
  (`marked`, `already_owned`, `held: list[Match]`, `unmatched: list[Match]`); a
  `Match` has `action, addon_title, game_id, dlc_id, reason`. It only sets
  `owned` 0→1 for confident matches; add-ons are scrape rows tagged `kind="addon"`.
- Scrape pipeline: `scrape_service._run_pipeline(conn, vendor, games)` runs
  import → enrich → ownership and returns a `summary` dict (counts only) stored in
  the scrape state; the Add Game modal polls `/api/scrape/status` and renders the
  summary at `phase="complete"` (`templates/base.html` `renderScrapeStatus`).
- Hero bar: `templates/index.html` `renderHeroStats(stats)` renders tiles from
  `/api/stats`; `/api/stats` (`app.py:1362`) returns `total_games`, `by_status`,
  etc. (no DLC counts today).
- PSN scraper: `scrapers/playstation.py` `collect(page, captured)` pages
  `getPurchasedGameList` (base games only); `collect_addons` is a no-op stub. The
  add-on operation name / persisted-query hash / response shape are unknown and
  must come from a live recon (`.recon/playstation.responses.jsonl`).
- Recon mode exists: `scrape_libraries.py --recon --vendor playstation` opens a
  headed browser and records all JSON responses to
  `.recon/playstation.responses.jsonl`.

## Feature 1: PSN owned add-on capture (recon-gated)

**Step A (user, one-time):** run `python scrape_libraries.py --recon --vendor
playstation`, log in, and open the **Add-ons / full purchase history** view so the
add-on API request is captured to `.recon/playstation.responses.jsonl`.

**Step B (implementation):** from that capture, identify the add-on GraphQL
operation (possibly `getPurchasedGameList` with an add-on/category filter, or a
distinct op), its persisted-query `sha256Hash`, and the response shape. Then in
`scrapers/playstation.py`:
- `parse_addons(items) -> list[ScrapedGame]` (pure) — map add-on items to
  `ScrapedGame(kind="addon", ...)`, unit-tested against a sanitized
  `tests/fixtures/playstation_addons_sample.json`.
- `collect_addons(page, captured) -> list[ScrapedGame]` — page the add-on op
  (symmetric to `collect`), returning tagged add-ons; returns `[]` if the op can't
  be reached (no regression).
- Extend `collect(page, captured)` to **return base games + add-ons combined**
  (games default `kind="game"`, add-ons `kind="addon"`). This keeps the vendor
  `collect` interface uniform (one list), so `scrape_service._run` and the CLI need
  no changes — the existing partition + `mark_ownership` handle the rest.

No data-model or matching changes: once PSN emits add-ons, the existing engine
flips matching DLC to owned exactly as it does for Nintendo/Xbox.

## Feature 2: scrape result list (imported DLC + owned status)

**Pipeline records the actual items** (in `scrape_service._run_pipeline`):
- Capture `run_started = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]`
  before enrichment (same clock/format as `dlc.created_at`).
- After the pipeline, build:
  - `added_dlc`: `SELECT g.title, d.name, d.kind, d.owned FROM dlc d JOIN games g
    ON g.id = d.game_id WHERE d.created_at >= ? ORDER BY g.title, d.name` — the DLC
    inserted this run, with current owned status.
  - `newly_owned`: from a new `OwnershipReport.marked_items: list[Match]` (the
    add-ons whose DLC flipped 0→1 this run); resolve each to `{game, name}` via its
    `game_id`/`dlc_id`. (Covers re-scrapes where the DLC row pre-existed and only
    ownership changed.)
  - `review`: `held + unmatched` Matches → `{title: addon_title, reason}`.
- Add `added_dlc`, `newly_owned`, `review` to the returned `summary` (alongside the
  existing counts). `mark_ownership` gains `marked_items` (append each applied
  Match; existing counts unchanged).

**UI** (`templates/base.html`, the scrape panel): at `phase="complete"`, below the
one-line summary, render an **expandable, scrollable results block**:
- "Imported DLC (N)" grouped by game title; each row `name <kind badge>` + an
  **owned ✓ / not-owned** indicator.
- "Marked owned this run (M)" — `game — name` lines (only if non-empty).
- "Needs review (K)" — `addon_title [reason]` lines (only if non-empty).
- View-only (mark owned in a game's DLC tab as today). Collapsed by default behind
  a "show details" toggle since it can be long (e.g., 130 rows).

## Feature 3: hero DLC tracker tile

- `/api/stats` (`app.py`): add `dlc_total = SELECT COUNT(*) FROM dlc` and
  `dlc_owned = SELECT COUNT(*) FROM dlc WHERE owned = 1`.
- `renderHeroStats` (`templates/index.html`): add a tile
  `['dlc', `${stats.dlc_owned||0}/${stats.dlc_total||0}`, 'DLC']`, after the
  existing tiles. Display-only; refreshes with the rest.

`total` = all DLC rows in the library (the available catalogue across owned games);
`owned` = rows flagged owned (manual + scrape-detected).

## Testing (TDD, offline)

- `mark_ownership` (temp DB): `marked_items` contains the applied Match(es); counts
  unchanged; held/unmatched still reported.
- `_run_pipeline` (temp DB, enrich mocked): after a run, `summary["added_dlc"]`
  lists the inserted DLC with owned status; an add-on that flips ownership appears
  in `summary["newly_owned"]`; held/unmatched appear in `summary["review"]`;
  `run_started`/`created_at` filtering returns only this run's rows.
- `/api/stats` (client/temp DB): returns `dlc_total` and `dlc_owned` with seeded
  dlc rows (some owned).
- PSN `parse_addons` (Feature 1, after recon): sanitized add-on fixture → add-on
  `ScrapedGame`s with `kind="addon"`; `collect` returns games + add-ons combined.
- Existing suite stays green. The live PSN add-on scrape + the modal JS are
  verified manually (no real browser in tests).

## Sequencing

Build **Features 2 and 3 now** (fully offline + testable). Build **Feature 1**
after the user provides the PSN recon capture (its parser/op/hash come from that
file). After Feature 1, PSN owned counts populate and both the hero tile and the
scrape result reflect real ownership.

## Out of scope

- A persistent/global DLC page or nav entry (explicitly not wanted).
- Marking owned from the scrape result (stays in the game's DLC tab).
- Per-platform DLC breakdown on the hero bar.
- Nintendo/Xbox already capture add-ons; unchanged here.

## Constraints

- Public repo: never commit `games.db`/`games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `.igdb_token.json` (gitignored). The PSN add-on
  fixture committed to `tests/fixtures/` must be sanitized (no real account ids).
- Conventional commits, no co-author trailer. Work on `main`.
- Run tests with `uv run python -m pytest`; lint gate `uv run ruff check` (the repo
  does not use `ruff format`; match the hand-aligned style).
- App runs with `use_reloader=False` (do not re-enable; it kills the scrape thread).
