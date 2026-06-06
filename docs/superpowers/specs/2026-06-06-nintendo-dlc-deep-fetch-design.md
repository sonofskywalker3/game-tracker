# Nintendo per-game DLC deep-fetch — design

Status: draft for review · 2026-06-06

## Goal

Resolve owned Nintendo (Switch) DLC to its parent game at scrape time, the same
way PlayStation does — by reading each owned game's DLC list rather than guessing
parents from add-on names. Nintendo order history yields a flat list of owned
items (NSUID + name + platform) with **no parent link**, and an NSUID carries no
parent information, so today most Switch DLC lands in the review queue.

Secondary goal (phase 2): populate the full official DLC catalogue per game
(owned + unowned), feeding the existing per-game DLC tab — richer than the IGDB
DLC data currently used.

## Confirmed data sources (verified live, server-side, 2026-06-06)

All three links of the chain were exercised end-to-end against Vampire Survivors
(base NSUID `70010000059002`, 7 owned DLC) using credentials harvested from a
`recon_nintendo_store.py` capture:

1. **NSUID → SKU (deterministic, no lookup):** `sku = nsuid[0] + nsuid[3] + nsuid[6:]`.
   - base `70010000059002` → `7100059002`; DLC `70050000042414` → `7500042414`.
   - The SKU is also the game's/DLC's Algolia `objectID`.
2. **SKU → slug + metadata (Algolia):** `GET https://u3b6gr4ua3-dsn.algolia.net/1/indexes/store_game_en_us/{sku}`
   with headers `X-Algolia-Application-Id: U3B6GR4UA3`, `X-Algolia-API-Key: <key>`.
   Returns `urlKey` (slug), `nsuid`, `sku`, `title`, `dlcType`, `hasDlc`. Works
   for both games and DLC. **`hasDlc` is unreliable** (returned `False` for a game
   with 7 DLC) — do NOT use it to skip work.
3. **slug → full DLC list (Next.js data):**
   `GET https://www.nintendo.com/_next/data/<buildId>/us/store/products/<slug>/dlc.json?slug=<slug>`
   → `pageProps.initialApolloState` → `Product:{sku}` entries, each with
   `{nsuid, sku, name, urlKey}`. This is the authoritative parent→DLC list.

Dead end (documented so we don't revisit): graph.nintendo.com
`ProductBySkuForDigitalPDP` does NOT include the DLC list.

Rotating bootstrap values (treated like the persisted-query hashes already in
`scrapers/nintendo.py`):
- **`buildId`** (e.g. `cOgiksR579rrYrw_1tP_2`) — changes per Nintendo deploy;
  read from `__NEXT_DATA__.buildId` on any store page.
- **Algolia search key** — public, embedded in the page; rotates. Harvest live.

## Architecture

A per-game DLC pass that runs inside the existing authenticated scrape browser
session (mirrors `playstation.collect_addons`, wired in `scrape_service._run`
after the base `collect`). Running through the logged-in Playwright `page` lets us
reuse `page.request.get(...)` (same robustness as the existing GraphQL replay) and
harvest `buildId` + the Algolia key from a real page load, sidestepping bot
detection and credential-staleness.

New module **`scrapers/nintendo_catalog.py`** (sibling of `xbox_catalog.py` /
`steam_dlc.py`; network injected so tests run offline):

- `bootstrap(page) -> Bootstrap(build_id, algolia_key)` — navigate to one eShop
  store page; read `buildId` from `__NEXT_DATA__`; capture the Algolia key from a
  store-search request header (or page config). One-time per run.
- `game_slug(sku, *, fetch) -> str | None` — Algolia `getObject`.
- `dlc_list(slug, build_id, *, fetch) -> list[DlcEntry]` — fetch `dlc.json`, parse
  `initialApolloState` into `[DlcEntry(nsuid, sku, name)]`. Pure parser
  (`parse_dlc_list(body)`) unit-tested against a captured fixture.
- `build_parent_map(game_nsuids, *, fetch) -> dict[str, ParentRef]` — for each
  owned game: NSUID→SKU→slug→`dlc.json`; emit `{dlc_nsuid: ParentRef(parent_nsuid,
  parent_name)}` for every DLC found. Responses cached on disk in `.nintendo_cache/`
  (mirrors `.xbox_cache`); a not-found slug cached as empty so it isn't refetched.

### Integration (reuses existing machinery)

`scrape_service._run`, nintendo branch (parallel to the `playstation` branch):
after `collect`, call `nintendo_catalog.build_parent_map(owned_game_nsuids)` to get
the child→parent map, then drive the **existing** `addon_parent.resolve_and_link`
with a resolver closure `lambda ids: {i: parent_map.get(i) for i in ids}`. That
path already: ensures the parent game exists (by id, else creates/backfills),
links the owned add-on via `dlc_ownership.apply_addon_to_parent`, and clears
matching review-queue rows. Add-ons with no catalogue parent fall back to the
current name matcher. **No change to `dlc_ownership` or the review flow.**

Because the bundle-import fix (shipped `a8e39fd`) restores the missing base games
(Xenoblade, etc.), parents will now exist to link onto.

### Incremental sync

Add `games.nintendo_dlc_synced_at` (mirrors `psn_addons_synced_at`). First run
fetches all owned games' DLC lists; later runs skip games already synced (and new
games are naturally unsynced). The `.nintendo_cache/` makes even a full re-run cheap.

## Phasing

- **Phase 1 (this spec's core):** `nintendo_catalog` module + `build_parent_map`
  + scrape_service wiring → owned Switch DLC linked to parents, review rows
  cleared. Pure parsers unit-tested against captured `.recon` fixtures.
- **Phase 2 (follow-up):** persist the full per-game DLC catalogue (owned +
  unowned) into the `dlc` table for the DLC tab, owned flag from the order-history
  intersection. Deferred to keep Phase 1 shippable; noted here for continuity.

## Error handling

- `buildId` / Algolia key rotation → bootstrap re-harvests per run; a 404/403 on
  the API triggers one re-bootstrap, then logs and leaves the add-on for the name
  fallback (never aborts the scrape). Matches the persisted-query-refresh posture.
- Per-game isolation: one failed `dlc.json` doesn't sink the batch (mirrors
  `playstation.collect_addons`).
- One error pattern: raise typed errors inside `_request`, caught at the pass
  boundary and logged; pure parsers return values.

## Testing

- `parse_dlc_list(body)` and `nsuid<->sku` transform: pure, unit-tested against a
  sanitized fixture cut from `.recon/nintendo_store_03_dlc.json`.
- `build_parent_map` with an injected `fetch` returning fixture bodies → asserts
  the child→parent map for Vampire Survivors' 7 DLC.
- scrape_service nintendo branch: existing test-seam style (injected collect),
  asserts `resolve_and_link` is driven with the parent map.
- Run via `uv run python -m pytest`; lint `ruff check` only.

## Open questions / risks

- **Algolia key harvest:** confirmed present in browser request headers; need to
  confirm the cleanest server-side harvest point (request-header capture during a
  store-search nav vs. parsing the page JS). Small spike at implementation start.
- **DLC `dlcType` values** other than `Individual` (e.g. season passes / bundles)
  — confirm they appear in `dlc.json` and resolve to the same parent.
- Region: en-US / `store_game_en_us` only (matches the rest of the app).
