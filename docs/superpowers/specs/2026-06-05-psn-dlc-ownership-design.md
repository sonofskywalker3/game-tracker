# PlayStation DLC ownership via Store pages — design

**Date:** 2026-06-05
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved (recon done, mechanism confirmed); ready for implementation plan

## Goal

After a PlayStation scrape, the user's owned **add-ons** (DLC / expansions / packs)
are detected and their `dlc` rows flipped to `owned = 1`, instead of today's
behaviour where PS DLC ownership is never detected at all. This is **SP4 ("PSN
deep")** of the DLC vendor-source rework, replacing the abandoned "hidden add-on
GraphQL op" plan with the real mechanism found in recon.

Reuses the existing ownership engine: owned add-ons feed
`dlc_ownership.mark_ownership` exactly like Xbox/Nintendo/Steam — name-match to an
IGDB-sourced `dlc` row → flip owned + record the PSN id in `dlc_external_ids`;
ambiguous/unmatched → the existing review queue (never silent). Follows
`cleanup-fixes-must-be-general` (durable idempotent pipeline, no one-time DB
patches) and `canonical-rename-equality-only` (equality match; ambiguous held).

## Background (root cause, verified)

- `scrapers/playstation.py` scrapes base games only via `getPurchasedGameList`;
  `collect_addons()` is a disabled stub returning `[]` (lines 122-132). The
  pipeline therefore calls `mark_ownership([])` → nothing is ever marked owned.
- **PSN has no add-on "purchases" list / API.** Confirmed by the owner. Ownership
  is only visible on the logged-in PS **Store product pages**.

## Recon findings (CONFIRMED 2026-06-05)

`recon_psn_store.py` captured 11 logged-in store pages (`.recon/psn_store_*`),
analysed offline. Results:

1. **URL is free.** Each owned game's base-scrape `external_id` is already the full
   Store product id, e.g. `UP0082-CUSA09377_00-PT00000000000000`
   (`scraped/playstation_*.json`, persisted as `game_external_ids.external_id`,
   source `playstation`). The page is just
   `https://store.playstation.com/en-us/product/<external_id>`.
2. **One scrolled game page enumerates every add-on with ownership.** The page's
   GraphQL responses contain add-on objects with `id` (full product id), `name`,
   and `price.basePrice`. The ownership discriminator is:
   - `price.basePrice == "Purchased"` → **OWNED**
   - dollar string (e.g. `"$0.49"`, `"$29.99"`) → **not owned**
   - `"Unavailable"` / `null` → delisted / edition-bundle → **skip**
   Verified vs ground truth: DQB2 Season Pass / Modernist / Aquarium / Hotto Stuff
   = `"Purchased"`; DQB2 recipe packs = `"$0.49"`; Cyberpunk Phantom Liberty =
   `"$29.99"` (owner confirmed not owned).
3. **Must scroll first** — the add-ons section lazy-loads (FF16 unscrolled = 2 ids;
   Ys VIII scrolled = 25+). Use `scrapers.base.scroll_until_idle`.
4. **Do NOT use the resolved top-level `ctas`/`webctas` `type`.** Cyberpunk-on-disc
   base showed resolved `DOWNLOAD` yet own-SKU `ADD_TO_CART` (disc copy = no digital
   entitlement). Only the per-add-on `price.basePrice` is reliable. Corollary: a
   disc-only base game reads as not-owned digitally — expected and harmless (we only
   read add-on rows, base ownership comes from `getPurchasedGameList`).

## Scope (approved)

**Backfill the whole PS library once, then incremental.** A first run visits every
PS game; later scrapes visit only games not yet checked + newly-added ones; plus a
per-game manual "Refresh PSN DLC" trigger.

Implemented with a per-game marker `games.psn_addons_synced_at` (nullable
timestamp). The add-on pass targets PS games where it is NULL (→ first run covers
all; newly-imported games are NULL by definition) and stamps it on success. Manual
refresh nulls one game's marker (or forces it) so it is re-visited.

## Architecture

Data flow for a PlayStation scrape:

```
_run (browser open)
  collect()                      base games (unchanged)
  collect_addons(page, targets)  NEW: per target product id -> store page ->
                                 scroll_until_idle -> parse_addons -> owned add-ons
_run_pipeline (DB)
  import_games(base)             (unchanged; creates new game rows)
  run_dlc_enrichment()           (unchanged; IGDB dlc catalogue)
  mark_ownership(owned_addons)   (unchanged engine; flips owned / records psn id /
                                 review) + stamp psn_addons_synced_at on visited games
```

Key constraint honoured: scrapers never touch the DB (`scrapers/base.py` docstring).
`collect_addons` takes the **target product ids as an argument** and returns
`ScrapedGame` records; `scrape_service` (which may read the DB) decides the targets
and passes them in.

### Components

1. **`scrapers/playstation.py`**
   - `parse_addons(payloads: list[dict]) -> list[ScrapedGame]` — **pure**. Walks the
     captured GraphQL bodies, finds add-on objects (`id` matches the product-id
     regex, has `name` + `price`), emits `ScrapedGame(kind="addon", source="playstation",
     title=name, source_title=name, external_id=<add-on product id>,
     platform=<add-on `platforms`, else parent's, else DEFAULT_PLATFORM>)`
     **only** when `price.basePrice == "Purchased"`. Skips `"Unavailable"`/null/dollar.
     (`mark_ownership` matches by title, not platform, so platform is best-effort.)
     Unit-tested against sanitized `.recon/psn_store_*` fixtures.
   - `collect_addons(page, product_ids: list[str], captured: list | None = None)
     -> list[ScrapedGame]` — live shell. For each product id: `goto` the store URL,
     `scroll_until_idle(page, captured)`, snapshot the new captured bodies, run
     `parse_addons`, accumulate. Polite pacing (`REQUEST_DELAY_MS`); per-game
     try/except so one bad page doesn't abort the batch (logs + continues).
   - The base scrape's `captured` listener already records JSON/fetch bodies; the
     add-on pass reuses the same `(page, captured)` from `capturing_browser`.

2. **`scrape_service.py`**
   - In `_run`, after `collect()` and while the browser is open, for PSN: read target
     product ids from the DB (PS `game_external_ids` where the game's
     `psn_addons_synced_at IS NULL`) ∪ the just-scraped ids, call
     `collect_addons(page, targets, captured)`, and pass the owned add-ons into the
     pipeline together with the base games.
   - In `_run_pipeline`, the PSN branch already calls `mark_ownership(addons)`; it now
     receives real add-ons. After marking, stamp `psn_addons_synced_at = CURRENT_TIMESTAMP`
     for every visited game id. Progress: set phase/message with a running count
     ("checking add-ons: 37/420 games…") so the long backfill is visible in the modal.
   - Test seam: `collect_addons` injectable like the existing `collect` seam, so the
     full flow is unit-testable with a fake page.

3. **`models.py`** — add `psn_addons_synced_at TIMESTAMP` (nullable) to `games`;
   idempotent `ALTER TABLE ... ADD COLUMN` migration guarded like existing ones.

4. **Manual refresh (lightweight)** — `POST /api/games/<id>/dlc/refresh-psn` nulls the
   game's `psn_addons_synced_at` and kicks a single-game add-on pass via the existing
   background scrape machinery (reuses the awaiting-login/continue flow if a session
   isn't live). A per-game button in the modal. If the browser-session plumbing for a
   one-off proves heavy, this ships as a fast-follow; the scrape-integrated backfill is
   the core deliverable.

## Edge cases

- **Disc-only base game**: base owned via `getPurchasedGameList`; its add-ons still
  read correctly (Purchased vs price). No special handling.
- **`"Unavailable"` / delisted / edition bundles (null price)**: skipped — neither
  owned nor reviewed.
- **Free add-ons** (`"Free"`): treated as not-owned unless `"Purchased"`. (If the
  owner wants acquired-free items counted, revisit — recon had no clean free-owned
  sample.)
- **Region/locale**: use `en-us` store; `"Purchased"` is the en-us string.
- **Rate limiting / large library**: polite delay per page; per-game isolation; the
  `synced_at` marker means the expensive full pass happens once.
- **Idempotency**: `mark_ownership` is 0→1-only and idempotent; re-running is safe.

## Testing plan

- `parse_addons` pure unit tests against trimmed `.recon` fixtures: owned add-on →
  emitted; priced add-on → dropped; `"Unavailable"`/null → dropped; malformed body →
  skipped. Use DQB2 (mixed owned/priced) + Cyberpunk (not-owned expansion) captures.
- `collect_addons` with a fake page returning canned captured bodies → returns the
  expected owned `ScrapedGame`s; one failing page doesn't abort the rest.
- `scrape_service` flow test (fake `collect` + fake `collect_addons`): newly-added PS
  games get `psn_addons_synced_at` stamped; owned add-ons reach `mark_ownership`;
  summary surfaces `newly_owned` / `review`.
- Migration test: column added idempotently; existing rows NULL.
- Run via `uv run python -m pytest`; lint gate `ruff check`.

## Out of scope

Xbox/Nintendo deep add-ons (SP5/SP6), missing-DLC view (SP7). Manual-refresh UI may
land as a fast-follow if single-game browser plumbing is non-trivial.
