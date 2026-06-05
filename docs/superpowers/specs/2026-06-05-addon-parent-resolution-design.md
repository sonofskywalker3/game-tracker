# Vendor-agnostic add-on → parent-game resolution (Xbox now, Nintendo-ready)

## Problem

Add-ons from store vendors enter the library as a **flat list** with no parent
reference. PSN visits each game's store page, so its add-ons arrive attached to a
parent. But **Xbox** and **Nintendo** scrape add-ons from billing / order history as
independent line items (`kind="addon"`) carrying only their own product id + title.
`dlc_ownership.mark_ownership` resolves a parent by NAME prefix (+ a PSN title-id
fallback), but vendor add-on names rarely contain the game name, so they land in the
review queue as **"no parent game"** and require manual resolution.

This produced 29 unresolved Xbox add-ons (13 of them Rock Band 4 songs, plus
Borderlands 4, Spellbreak, Dead by Daylight, etc.). Nintendo will hit the identical
wall the first time Switch DLC is scraped (0 Nintendo add-ons exist today).

The user's directive: **fix the scraper, not the database.** A rerun of the vendor
scrape must make the data correct by construction — link every owned add-on to its
parent game, creating/recording parent identity as needed — with **no babysitting**.

## Key recon finding (Xbox — confirmed)

Microsoft's **public, auth-free** catalog `displaycatalog.mp.microsoft.com` declares
each add-on's parent directly. For a Durable/Consumable product, the response's
`Product.MarketProperties[0].RelatedProducts` contains an entry with
`RelationshipType == "addOnParent"` whose `RelatedProductId` is the **base game's
Store product id**. Verified against live data:

| Add-on (product id) | `addOnParent` | Parent (type=Game) |
|---|---|---|
| Borderlands®4: Gilded Glory Pack `9MZKJPZXTVGM` | `9MX6HKF5647G` | Borderlands®4 |
| "Carry On Wayward Son" - Kansas `BXRJHMSXDCQ9` | `BNG8P3Q7C78Z` | Rock Band 4 |
| Xbox Game Pass Ultimate Pack `9P5VL9F68QC5` | `9N6L1HXN7044` | Spellbreak |

All resolved parents are `ProductType == "Game"` — there is no junk to filter; the
"Game Pass" add-on is just a Spellbreak cosmetic. The base games are mostly NOT in
the library by product id (only 89/777 games carry an Xbox id; the parents above are
not among them) because the user owns them via Game Pass/disc/free, so the link must
also bridge product-id → existing library game by **name**, then backfill the id.

Nintendo exposes **no equivalent** parent field (its price API returns price only);
its linkage needs separate recon against real `7005` add-on NSUIDs, which do not
exist in the data yet. Nintendo is therefore architected-for but its concrete
resolver is deferred (see "Nintendo").

## Goal

A **vendor-pluggable** parent-resolution layer that, at scrape time:

1. Resolves each owned add-on to its parent **game's vendor product id** (+ name/cover)
   via a per-vendor resolver.
2. Ensures the parent game exists in the library — match by product id, else by name
   (recording/backfilling the product id), else **create it** from catalog metadata.
3. Links the add-on to that parent (owned), reusing the existing ownership engine.
4. Clears any matching open `dlc_review_queue` rows, so a rerun self-heals the queue.

Hands-off by design: confident matches and missing-parent creation happen
automatically. The only accepted tradeoff is base-game entries for titles the user
owns only DLC for (e.g. free Game Pass games) — correct for a collection tracker, and
the existing dedup tool catches any accidental twin.

## Architecture

### New module `addon_parent.py`

The vendor-agnostic core. No vendor HTTP lives here.

- `@dataclass ParentRef`: `product_id: str`, `name: str | None = None`,
  `cover_url: str | None = None`. The resolved parent GAME identity for one add-on.
- `ParentResolver = Callable[[list[str]], dict[str, ParentRef | None]]` — given a list
  of add-on vendor product ids, return `{addon_product_id: ParentRef | None}`. One
  registered per source. Network I/O is the resolver's concern (injected for tests).
- `resolve_and_link(conn, source, addons, resolver, *, create_missing=True) -> ResolveReport`:
  the pipeline. For each add-on (a scrape dict with `title`, `source`, `external_id`,
  `source_title`):
  1. `pr = resolved[addon.external_id]`. If `pr is None` → add to `report.unresolved`
     (left for the existing name/title-id `mark_ownership` fallback); continue.
  2. **Find/ensure parent game_id:**
     a. **by id:** `game_external_ids WHERE source=? AND external_id=pr.product_id`.
     b. else **by name:** reuse `import_scraped`'s safe name match
        (`_safe_auto_confirm`) against `pr.name`; on a confident match, INSERT
        `game_external_ids(source, pr.product_id, pr.name)` onto that game (backfill).
     c. else if `create_missing` and `pr.name`: create the parent via
        `import_scraped.import_games(conn, [synthetic_game], source, _safe_auto_confirm)`
        (one synthetic `kind="game"` row: title=`pr.name`, external_id=`pr.product_id`,
        platform=vendor platform, cover=`pr.cover_url`) so identity/normalization/dedup
        all behave exactly like a normal game import; read back its game_id.
  3. **Link the add-on:** `dlc_ownership.apply_addon_to_parent(conn, sub, parent_id,
     parent_norm, titles, addon, dry_run=False)` (reconcile-by-id → by-name → create
     owned dlc row; records `dlc_external_ids`). 0→1 only, idempotent.
  4. On a real mark (`sub.marked or sub.already_owned`), **resolve matching open review
     rows**: `UPDATE dlc_review_queue SET resolved_at=CURRENT_TIMESTAMP WHERE source=?
     AND external_id=? AND resolved_at IS NULL AND dismissed_at IS NULL`.
  - Caller owns commit (consistent with `mark_ownership`/`resolve`).
- `@dataclass ResolveReport`: `linked: int`, `created_parents: int`,
  `backfilled_ids: int`, `review_cleared: int`, `unresolved: list[...]`,
  `linked_items: list[Match]`.

### New resolver `scrapers/xbox_catalog.py`

- `resolve_addon_parents(product_ids, *, fetch=_http_get) -> dict[str, ParentRef | None]`:
  batch-fetch via displaycatalog multi-get (`/v7.0/products?bigIds=ID1,ID2,...&
  market=US&languages=en-US&fieldsTemplate=details`, chunked to a safe batch size),
  with a per-id cache in a gitignored `.xbox_cache/` (mirrors `.steam_cache/`). For
  each add-on product, read the `addOnParent` `RelatedProductId`; fetch that parent's
  product (also cached) and accept it only when `ProductType == "Game"`, returning
  `ParentRef(parent_id, parent_name, parent_cover)`; else `None`. `fetch` is injected
  so unit tests run against saved JSON fixtures, no network.
- Constants (named): base URL, batch size, market/lang, the relationship type string
  `"addOnParent"`, the accepted parent type `"Game"`, cache dir.

### Integration — `scrape_service._run_pipeline`

For non-Steam vendors, run the new pass for the vendor's add-ons before the existing
name-based fallback:

1. After import, if a resolver is registered for `vendor` and there are add-ons:
   `report1 = addon_parent.resolve_and_link(conn, vendor, addons, resolver)`.
2. Feed `report1.unresolved` (add-ons with no catalog parent) to the existing
   `dlc_ownership.mark_ownership` for the name/title-id fallback → review queue.
3. Merge counts into the scrape summary (owned marked, parents created, review).

Resolvers are registered in one place (e.g. `addon_parent.RESOLVERS = {"xbox":
xbox_catalog.resolve_addon_parents}`); PSN/Nintendo absent for now. PSN keeps its
current title-id path via `mark_ownership` (unchanged); it MAY be migrated to a
resolver later but that is out of scope here.

### `mark_ownership` — unchanged

The existing engine remains the fallback for unresolved add-ons. No `parent_hint`
parameter is added (the two-pass split keeps `mark_ownership` untouched and avoids
coupling). This preserves all current PSN/name behavior and tests.

## Data flow (Xbox rerun)

```
billing scrape → addons[] (product id + title)
  → xbox_catalog.resolve_addon_parents(ids)  [displaycatalog, cached]
      → {addon_id: ParentRef(parent_id, name, cover)}
  → addon_parent.resolve_and_link(conn, "xbox", addons, resolver)
      parent in lib by id?  → link
      parent in lib by name? → backfill id, link
      else                   → create parent game, link
      → mark owned (dlc + dlc_external_ids) + clear review rows
  → unresolved → mark_ownership (name fallback) → review
```

After one rerun: 89→more games carry Xbox ids, missing parents (Borderlands 4, Rock
Band 4, Spellbreak, …) exist and are linked, all 13 Rock Band songs hang off one
Rock Band 4 game, the 29 review rows clear themselves.

## Nintendo (architected, resolver deferred)

Nintendo plugs into the **same** `ParentResolver` interface. No concrete resolver is
shipped now because: (a) Nintendo exposes no `addOnParent`-style field; (b) there are
zero Nintendo add-ons in the data to verify against. The expected mechanism, to recon
when Switch DLC is first scraped: for each owned Nintendo **game** NSUID (263 exist),
fetch its eShop add-on-content listing and build `{addon_nsuid: parent_game_nsuid}`,
matching owned add-on NSUIDs by id (PSN-style game→add-ons direction). When that
resolver is written and verified, registering it under `"nintendo"` is the only
change — no redesign. Until then, Nintendo add-ons fall through to the existing name
fallback (review queue), exactly as today.

## Error handling

- displaycatalog HTTP failure / non-200 / unparseable: log (specific exception:
  `httpx.HTTPError` / `requests.RequestException` / `json.JSONDecodeError`), treat
  those add-ons as **unresolved** (→ name fallback / review). Never abort the scrape.
- A resolved parent whose `create_missing` import collides or fails: log and leave the
  add-on unresolved rather than corrupting state.
- All vendor product-id string comparisons use named constants, not literals.

## Testing (no network)

- `scrapers/xbox_catalog.py`: parse saved displaycatalog fixtures → `ParentRef`
  (addOnParent extraction; non-Game parent → None; missing/garbage → None; batch
  chunking; cache hit avoids refetch). Fixtures captured from the live recon.
- `addon_parent.resolve_and_link` (temp DB + a fake resolver dict):
  parent-by-id link; parent-by-name backfill (id recorded on existing game); parent
  create (new game row + link); add-on owned dlc created with `dlc_external_ids`;
  open review row cleared on link; idempotent second run (no dup game, no dup dlc, 0→1
  only); unresolved add-on returned (not linked); source isolation.
- Integration smoke (temp DB): a vendor pass with one resolvable + one unresolved
  add-on routes correctly across the two passes.
- Run via `uv run python -m pytest`; lint `uv run ruff check` (never `ruff format`).

## Out of scope

- Nintendo concrete resolver (deferred to live recon).
- Migrating PSN title-id logic into the resolver interface (optional future cleanup).
- Steam (already id-based deep fetch).
- Any one-off DB patch — the fix is the scrape pipeline; a rerun corrects the data.
