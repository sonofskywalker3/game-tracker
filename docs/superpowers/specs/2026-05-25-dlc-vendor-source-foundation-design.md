# DLC vendor-source foundation (SP1) — design

**Date:** 2026-05-25
**Branch:** main (work directly on main, per repo workflow)
**Status:** approach approved; ready for implementation plan

This is the **first spec of the DLC source-of-truth rework**. It opens with the
settled architecture decision and the sub-project roadmap (so later specs share
context), then details **SP1** in full. SP1 is the only part to be implemented
from this spec; SP2–SP7 get their own specs.

## The decision: vendor store is the source of truth

The DLC feature was redirected because IGDB's DLC catalogue is demonstrably
incomplete (e.g. a Switch scrape captured 37 owned add-ons but marked 0; Vampire
Survivors has 7 owned DLC and 0 IGDB rows). Research across every realistic
catalogue source confirmed there is **no single cross-platform source more
complete than IGDB** — the only *complete* sources are the vendor stores
themselves, each single-platform:

| Source | Cross-platform? | Indie DLC completeness (Vampire Survivors) | Verdict |
|---|---|---|---|
| IGDB (current) | Yes (all) | ~0 (models DLC as separate game/bundle records) | Keep as identity/cover spine + fallback |
| Steam `appdetails.dlc[]` | PC only | 8/8 (it *is* the store) | Primary for Steam |
| RAWG `/games/{id}/additions` | Yes | 2/8 (worse than IGDB) | Drop |
| Giant Bomb | Yes (sparse) | game→DLC link broken | Drop |
| MobyGames | Yes | weak + now paid ($13/mo) | Drop |
| Wikidata / PCGamingWiki | Yes / PC | very sparse / unstructured | Drop |
| PlayStation `metGetAddOnsByTitleId` | PS only | store = truth | Primary for PS |
| Xbox DisplayCatalog | Xbox/PC only | store = truth, linkage fiddly | Supplement |
| Nintendo eShop | Switch only | store = truth, needs page scrape | Supplement (hardest) |

**Chosen direction (approved):** the **vendor store the game is owned on is the
source of truth** for its DLC (catalogue *and* ownership). IGDB stays the
cross-platform identity/cover spine and a fallback catalogue. The user's library
spans **PlayStation, Xbox, Nintendo, and Steam**.

**"No junk rows" bar (approved):** a DLC row may exist when it is either (a) a
**verified owned add-on** from a vendor scrape (real store id + the store's real
title + provably owned), or (b) a **deep-fetched catalogue entry**. Matching
**prefers the store's product id**; a name-based link is only ever a *proposal*
routed to interactive resolution — never applied silently.

## Roadmap (approved sequence)

1. **SP1 — Foundation + owned-add-on fix (Nintendo & Xbox).** *(this spec)*
   Multi-source DLC model + id-first ownership engine; records verified owned
   add-ons as authoritative rows; fixes "37 owned, 0 marked" with data already
   scraped. Offline-testable.
2. **SP2 — Steam vendor (end-to-end).** Owned appids via the official API +
   `appdetails.dlc[]` catalogue; ownership by appid set-intersection. First vendor
   with a complete catalogue; proves the deep-fetch path and unlocks missing-DLC.
3. **SP3 — Per-game modal rework + interactive conflict resolution.** Repurpose
   the cover-URL field into a generalized source-of-truth link; separate "change
   cover image" button; dedup-style modal that resolves SP1's queued parent-link
   conflicts; auto-populate DLC on manual add.
4. **SP4 — PlayStation deep add-ons** (recon-gated). `metGetAddOnsByTitleId` per
   title → full PS catalogue + ownership (the "2-stage deep scrape").
5. **SP5 — Xbox deep add-ons.** DisplayCatalog per title → full Xbox catalogue.
6. **SP6 — Nintendo deep add-ons.** eShop product-page scrape → full Nintendo
   catalogue. Hardest (no API).
7. **SP7 — Missing-DLC view** (catalogue − owned) to drive collection completion.
   (Supersedes the earlier "no persistent DLC page" stance.)

Direction "unmatched → refresh from source → re-match" is realized incrementally:
SP1 re-runs to pick up newly-confirmed links; it becomes a true per-game "refresh
from source" once each vendor's deep-fetch (SP4–6) exists. The already-shipped
scrape-result list + hero `dlc_owned/dlc_total` tile are kept.

---

# SP1 — Foundation + owned-add-on fix (Nintendo & Xbox)

## Goal

Make scraped owned add-ons actually mark owned for the two vendors we already
capture (Nintendo, Xbox). Replace the IGDB-name-only matching engine with a
vendor-authoritative, id-aware one that **stops** the "parent has no dlc / no name
match → reported, nothing marked" behaviour (the cause of 37→0) and instead
**records the owned add-on as a real, vendor-sourced DLC row carrying its real
store id**.

## Scope / deferred

In scope: the new data model, the rewritten ownership engine, pipeline/reporting
wiring, migration, and tests. Nintendo and Xbox only (the vendors whose add-ons
already reach the pipeline tagged `kind="addon"`).

Deferred to later SPs (so SP1 stays shippable and fully offline-testable):

- The per-game deep-fetch / full catalogue, and Steam (SP2, SP4–6).
- The dedup-style **resolution modal** (SP3). SP1 only *reports* uncertain items;
  you fix them in the existing DLC tab for now.
- The modal source-of-truth-link / cover-button rework (SP3).
- The missing-DLC view (SP7).
- **Per-platform ownership.** Ownership stays a single game-level `dlc.owned`
  flag (own it on any platform = owned). Per-platform ownership is a possible
  later refinement; YAGNI here.

## Background (verified facts)

- `dlc` schema (`models.py:175-186`): `id, game_id, name, igdb_id, kind,
  owned (0/1), source DEFAULT 'igdb', created_at`, `UNIQUE(game_id, name)`,
  `FOREIGN KEY(game_id) → games(id)`. **No vendor product id is stored** — only
  `igdb_id`. Matching is purely name-based today (`dlc_ownership.py`).
- `game_external_ids` (`models.py:104-112`): `game_id, source, external_id,
  source_title, created_at` — the established per-vendor id pattern. Written on
  import at `import_scraped.py:248-252`.
- `ScrapedGame` (`scrapers/base.py:62-72`) carries `title, platform, source,
  external_id, cover_url, source_title, status_hint, kind` (`"game"|"addon"`,
  default `"game"`).
- Add-on capture already works for two vendors: **Nintendo** emits `7005` NSUIDs
  as `kind="addon"` with the NSUID as `external_id`; **Xbox** emits
  `Durable`/`Consumable` items as `kind="addon"` with `productId` as
  `external_id` (`scrapers/nintendo.py`, `scrapers/xbox.py:41-72`).
  **PlayStation** does not scrape add-ons yet (`getPurchasedGameList` returns
  base games only; `external_id = productId or titleId`,
  `scrapers/playstation.py:54`).
- **No vendor exposes a parent-game pointer for an add-on.** The only link a
  purchased-library scrape gives is the add-on title, which embeds the parent name
  as a prefix. Parent resolution is therefore necessarily name-based until the
  deep-fetch SPs.
- Pipeline (`import_scraped.main`, `import_scraped.py:632-663`): rows are
  partitioned by `kind`; `kind=="addon"` rows are collected into `all_addons` and
  passed (as raw dicts carrying `external_id`/`source`/`title`) to
  `dlc_ownership.mark_ownership`. The web pipeline does the same in
  `scrape_service._run_pipeline` (`scrape_service.py:77-138`): import games →
  `run_dlc_enrichment` → `mark_ownership`, then builds a `summary`.
- `mark_ownership` today (`dlc_ownership.py:156-190`) reads only `addon["title"]`,
  matches by name (equality → containment), flips `owned` 0→1 on existing rows,
  holds ambiguous/containment-only, reports unmatched, and **inserts nothing**.
  Reusable normalization: `models.match_key` / `normalize_title` / `clean_title`;
  `dlc_ownership.parent_of` (longest normalized-title prefix).

## Data model

New child table, mirroring `game_external_ids` (chosen over columns on `dlc` so
one DLC row can carry ids from several stores — the cross-platform / deep-fetch
future):

```sql
CREATE TABLE IF NOT EXISTS dlc_external_ids (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dlc_id       INTEGER NOT NULL,
    source       TEXT    NOT NULL,   -- 'nintendo' | 'xbox' | 'playstation' | 'steam'
    external_id  TEXT    NOT NULL,   -- the store's add-on id (NSUID / productId / appid)
    source_title TEXT,               -- the full vendor add-on string, for provenance
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, external_id),
    FOREIGN KEY (dlc_id) REFERENCES dlc(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dlc_ext_dlc ON dlc_external_ids(dlc_id);
```

- `UNIQUE(source, external_id)` makes id-reconciliation and idempotent re-scrapes
  trivial (same add-on id always resolves to the same DLC row).
- `ON DELETE CASCADE` removes a DLC's ids with the DLC.
- `dlc.source` starts carrying vendor labels (`'nintendo'`/`'xbox'`/…) for rows
  created from a vendor scrape, alongside the existing `'igdb'`/`'manual'`.

No other schema change. The single game-level `dlc.owned` flag is unchanged.
Migration is purely additive (see Migration).

## The id-first ownership engine (`dlc_ownership.py`, rewritten)

`mark_ownership(conn, addons, *, dry_run=False)` — `addons` are scrape dicts (or
objects) carrying `title`, `source` (vendor), and `external_id`. Per add-on:

1. **Resolve parent** with `parent_of` (longest normalized-title prefix —
   unchanged). Unique match → *confident*. A cross-game tie, or no prefix → push a
   `Match` to the **review queue** (`reason` = "ambiguous parent" / "no parent
   game"); write nothing.
2. **On a confident parent, find-or-create the DLC row** in this order:
   - **By id** — a `dlc` row already linked to `(source, external_id)` via
     `dlc_external_ids` → ensure `owned=1` (idempotent re-scrape;
     `already_owned` if it was already 1, else counts as `reconciled`).
   - **By name equality** — a `dlc` row under the parent whose
     `normalize_title(name)` equals the add-on **remainder** (the normalized
     add-on title minus the parent prefix) → *reconcile*: insert its
     `dlc_external_ids` row and set `owned=1`. (`reconciled`.) This is how an
     existing IGDB row gains its vendor id and gets marked owned.
   - **Create** — otherwise `INSERT` a new `dlc` row (`name` = cleaned remainder,
     falling back to the full add-on title; `kind`; `source` = vendor;
     `owned=1`) and its `dlc_external_ids` row. (`created`.) **This is the new
     behaviour that fixes the IGDB-missing cases.**
3. The old **containment-only "hold"** path is removed (it caused the false
   holds). Equality-reconcile-or-create is simpler and authoritative — the
   vendor's own title wins.

Ownership is **0→1 only** (the vendor is the source of truth) and the whole pass
is idempotent. Trade-off (accepted, documented): a DLC manually un-owned but owned
per the vendor is re-flipped on the next scrape; revisit with a provenance/override
column only if it proves annoying.

**Name cleanup for created rows:** `name` is the add-on's `source_title` with the
parent game's display-title prefix stripped when it is a clean prefix (so
"Sample Game - Season Pass" → "Season Pass"), else the full `source_title`. The
full vendor string is always preserved in `dlc_external_ids.source_title`.

**Pure helpers stay unit-testable** (`parent_of`, the remainder computation, the
equality test). The reconcile/create writes are temp-DB tested.

## Integration & CLI (`import_scraped.py`, `scrape_service.py`)

- Pipeline order is unchanged: `import_games` → `run_dlc_enrichment` (IGDB stays,
  now framed as the **fallback** catalogue so equality-reconcile can find existing
  rows) → `mark_ownership` (vendor-authoritative; creates the rest). The
  games/addons partition already exists; the engine simply starts reading
  `external_id`/`source` off the add-on dicts.
- `mark_ownership` drops the `include_flagged` parameter. The
  `--apply-flagged-ownership` CLI flag is **removed** — its only job was applying
  containment "holds," a category that no longer exists (an uncertain-parent item
  cannot be auto-applied; it has no known parent until SP3's modal).
  `--no-ownership` and `--dry-run` are kept.

## Reporting

`OwnershipReport` is reshaped:

```python
@dataclass
class OwnershipReport:
    created:       int = 0          # new vendor-sourced rows (owned)
    reconciled:    int = 0          # existing rows that gained an id + flipped owned
    already_owned: int = 0          # id already present and already owned
    marked:        int = 0          # created + reconciled (rows newly set owned)
    marked_items:  list[Match] = field(default_factory=list)  # newly owned, for the result list
    review:        list[Match] = field(default_factory=list)  # uncertain-parent add-ons
```

- `_log_ownership` (CLI) prints `created / reconciled / already owned / review`
  counts plus the review listing (in the style of the existing "FUZZY — needs your
  review" block).
- `scrape_service._run_pipeline`'s `summary` maps to the new fields:
  `owned_marked ← marked`, add `created`, `review ← review`. The already-shipped
  scrape-result UI keeps working unchanged in spirit — its `added_dlc`
  (`created_at >= run_started`) query now also catches the newly-created vendor
  rows, showing them with an owned ✓; `newly_owned ← marked_items`; the `review`
  block replaces the old held/unmatched block. The hero `dlc_owned/dlc_total` tile
  reflects created+owned rows automatically.

## Testing (TDD, offline)

Pure / unit (no network, no live DB):
- `parent_of` — existing cases stay green (unique prefix; longest wins;
  same-game/different-platform not ambiguous; cross-game tie → AMBIGUOUS; no
  prefix → None).
- remainder computation and the cleaned-`name` derivation (prefix stripped vs.
  full title fallback).

Temp-DB (the engine):
- **equality-reconcile**: an IGDB row present under the parent → the owned add-on
  attaches `(source, external_id)` and flips `owned=1` (`reconciled`).
- **create-when-missing**: a confident parent with no matching row → a new vendor
  `dlc` row `owned=1` plus its `dlc_external_ids` row (`created`).
- **id-idempotency**: a second identical run creates/flips nothing and adds no
  duplicate `dlc` or `dlc_external_ids` rows.
- **uncertain parent** (cross-game tie / no prefix) → `review`, nothing written.
- **0→1 only**: a row already `owned` stays owned (`already_owned`); a manually
  un-owned row is re-flipped (documented vendor-as-truth behaviour).
- **dry_run** writes nothing.

Integration (temp DB, IGDB fetch mocked):
- import a game → enrich (mocked) → feed a matching owned add-on → its row is
  created/owned; an add-on under an ambiguous parent goes to `review`; a re-run
  changes nothing.

Scraper parse: existing Nintendo/Xbox `parse_orders` add-on tests stay green
(`70050000000003` → `kind="addon"`; Xbox `Durable` → `kind="addon"`). Old
containment-hold tests in the ownership suite are removed/rewritten to the new
report shape.

Existing suite stays green otherwise.

## Migration

`models.migrate_db` adds `dlc_external_ids` (and its index) idempotently. Existing
`dlc` rows and `owned` flags are untouched (including any owned set by the old
containment engine — harmless and additive). Re-running `migrate_db` is a no-op.
Test: an existing DB migrates with no data loss; a second migrate changes nothing.

## Constraints

- Public repo: never commit `games.db`/`games.db.bak*`, `.recon/`, `scraped/`,
  `.pw-profile/`, `config.json`, `.igdb_token.json`, `excluded_games.json`
  (gitignored).
- Conventional commits, no co-author trailer. Work on `main`.
- Run tests with `uv run python -m pytest`; lint gate `uv run ruff check` only
  (the repo is not `ruff format`-clean; match the hand-aligned style).
- Engine + orchestration are pure / temp-DB testable; the suite runs offline.
  Back up `games.db` before any live ownership run (the pipeline already does this
  via `scrape_service.backup_db`).
