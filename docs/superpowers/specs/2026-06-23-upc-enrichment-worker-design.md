# UPC Enrichment Worker (Spec 2) — Design

**Date:** 2026-06-23
**Status:** Approved direction (owner decisions captured), pre-implementation
**Parent:** Scan-for-Info Core (Spec 1, shipped) — `docs/superpowers/specs/2026-06-22-scan-for-info-per-platform-upc-design.md`. This is **Spec 2** on that roadmap (Spec 1 Core → **Spec 2 enrichment** → Spec 3 subscriptions catalog).
**Topic:** Proactively populate the permanent `barcode_registry` so barcode scans resolve instantly and accurately — by (1) backfilling retail UPCs for the owner's collection, and (2) making `resolve()` a pluggable multi-source chain with a first extra free source (Wikidata GTIN).

---

## 1. Context & goals

Spec 1 made scanning a "scan-for-info" experience backed by `barcode_registry` (the permanent UPC↔game store) and a resolution chain `registry → UPCitemdb /lookup → IGDB title match`. Two coverage realities remain:

1. **The owner's own ~822-game collection** has almost no UPCs in the registry yet (only organically, one row per physical scan). Until a game's UPC is known, scanning it pays the full UPCitemdb+IGDB round-trip and can mis-identify; a pre-filled registry makes owned games resolve instantly, free, and human-accurate, and makes "do I already own this?" rock-solid.
2. **Games the owner does NOT own** (the in-store buy-avoidance case) sometimes dead-end because UPCitemdb has no product for that UPC. Broad, free UPC→game data is thin, so this is best-effort.

### Owner decisions (2026-06-23, from brainstorming)
- **Goal:** both, **collection first, then coverage** (two phases).
- **Run model:** **scheduled daily drip** — a background job auto-runs a throttled batch each day the app is up, resumes until the collection is covered, then tops up new games. Hands-off.
- **Match precision:** **auto-link confident matches + a Needs-review queue** for uncertain ones (confirm/reject in the web app), mirroring the existing IGDB audit review flow.
- **Phase 2 scope:** **pluggable multi-source `resolve()` + add Wikidata GTIN now** — refactor the product lookup into an ordered source chain and add Wikidata as the first extra free source (accepting its sparse game coverage; the deliverable is the extensible seam).
- **No-match games are NOT retried automatically** — recorded once so the daily drip skips them; the owner re-triggers or scans physically if wanted.
- **Free sources only** (carried from Spec 1). No paid source. PriceCharting's paid "Legendary" UPC CSV remains the only realistic paid bulk fallback if ever wanted — out of scope.

### Non-goals
- No paid data sources.
- No automatic retry of no-match games (manual re-trigger only).
- No subscription/mobile availability catalogs (that's Spec 3).
- Not a rewrite of `resolve()`'s matching/ownership logic — only its product-lookup step becomes a source chain.
- "Daily" is best-effort: the drip runs once per calendar day **the app is running**; it is not a guaranteed 24/7 scheduler.

---

## 2. Architecture overview

Two phases, two implementation plans, both feeding `barcode_registry`:

- **Plan A — Phase 1: Collection backfill** (backend worker + schema + web review UI).
- **Plan B — Phase 2: Pluggable coverage** (multi-source `resolve()` + Wikidata GTIN source).

New/changed modules:
- `barcode.py` — add `search_products_by_name()` (UPCitemdb name-search), the confidence classifier, `lookup_wikidata_gtin()`, and refactor the product-lookup step of `resolve()` into an ordered source chain.
- `enrichment.py` (new) — the collection-backfill worker: game selection (idempotent), per-game match + classify, quota/drip bookkeeping, status object. Kept separate from `barcode.py` so the worker's batch/scheduling logic doesn't bloat the resolution module.
- `background_tasks.py` — a daily-drip launcher + status getter, mirroring the existing `run_cover_fetch_background` / `get_cover_fetch_status` pattern.
- `models.py` — `upc_review` table migration + a small enrichment-state row; registered in `migrate_db()` and `tests/conftest.py`.
- `app.py` — routes: trigger a batch, get status, list/confirm/reject review candidates.
- `templates/` — the Needs-review UI (Settings or a dedicated section).

---

## 3. Phase 1 — Collection backfill worker

### 3.1 Source: UPCitemdb name-search
`barcode.search_products_by_name(query: str, *, url=..., timeout=...) -> list[dict]` calls the UPCitemdb trial name-search endpoint (`https://api.upcitemdb.com/prod/trial/search?s=<query>`), returning `[{title, upc}, ...]`. Network/parse failures log and return `[]` (never raise), mirroring `lookup_product_title`. Each call counts against the shared ~100/day trial quota.

**⚠️ Implementation risk #1 (verify FIRST):** confirm the trial `/search` endpoint works without an API key and returns UPCs. If it does not (key required, or name-search not on the free tier), Phase 1's automatic source is blocked — STOP and surface to the owner (options: owner provides a UPCitemdb key, or fall back to manual physical scanning). Do not silently substitute a different mechanism.

### 3.2 Matching & confidence
For each owned `(game, platform)` lacking a UPC, query name-search (title, optionally with the platform word). For each returned product:
- `clean = barcode.clean_product_title(product.title)`; `parsed_platform = barcode.parse_retail_platform(product.title)`.
- **Confident** → `models.normalize_title(clean) == game.normalized_title` AND (`parsed_platform == game's short_name` OR the product names no platform): auto-link.
- **Uncertain** → title is close (e.g. normalized title contains/contained, or a single near-match) but not an exact title+platform match, or multiple plausible UPCs: enqueue for review.
- **No match** → nothing plausible: record `no_match` so the drip skips it next time.

Auto-link writes the registry via the Spec-1 helper: `registry_put(conn, upc, igdb_id=game.igdb_id, title=game.title, platform=short_name, game_id=game.id, cover_url=game.cover_url)`.

### 3.3 Idempotency, quota & drip
- **Selection (idempotent):** each batch selects owned `(game, platform)` pairs that have NO `barcode_registry` UPC for that platform AND no `upc_review` row (`pending`/`no_match`/`dismissed`) for that pair. So covered, queued, attempted, and dismissed pairs are all skipped — no duplicate work, safe to re-run.
- **Quota:** a configurable daily budget `UPC_ENRICH_DAILY_BUDGET` (default 90, hard-capped below the 100/day trial limit). The worker stops when the budget is exhausted or no eligible pairs remain. The budget counts UPCitemdb calls (name-search) made today.
- **Drip:** a daemon thread (started at app boot when Twitch/IGDB creds exist) that runs at most one batch per UTC calendar day: on each wake it compares the persisted `last_run_date`; if today hasn't run, it runs a batch, records the date + count; then sleeps a few hours and re-checks. This makes the collection drip in over ~9+ days without manual action, and tops up newly-added games thereafter.
- **Manual trigger:** a route + Settings button "Run an enrichment batch now" for testing / on-demand progress (does not bypass the daily quota cap).
- **Status:** a `get_enrichment_status()` (mirroring `get_cover_fetch_status`) exposing `running, current, total, found, queued, skipped, no_match, last_run_date, remaining_eligible, error` for live progress display (per the "live progress feedback" principle — never a static frozen-looking message).

### 3.4 Data model
New table:
```
upc_review(
    id INTEGER PK,
    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
    platform TEXT,                       -- the game's short_name this attempt targeted
    upc TEXT,                            -- candidate UPC (NULL for no_match rows)
    product_title TEXT,                  -- raw UPCitemdb product title
    cover_url TEXT,                      -- the game's cover (for the review UI)
    status TEXT CHECK(status IN ('pending','no_match','dismissed')),
    reason TEXT,                         -- why it's uncertain / no_match
    created_at TEXT
)
```
This single table is both the **review queue** (`status='pending'`) and the **dedup/attempt ledger** (`no_match`, `dismissed`). Confirmed (linked) UPCs live in `barcode_registry` (not here). Idempotent migration, registered in `migrate_db()` + `conftest.py`.

Enrichment state (last-run-date + per-day count) is stored either as a one-row `upc_enrichment_state` table or in the existing config store — implementer picks the simpler fit; it must persist across app restarts.

### 3.5 Web review UI
A Needs-review list (mirror the IGDB-audit Needs-review UI): each `pending` row shows the game (title + cover) alongside the candidate product (cleaned title, UPC, reason). Actions:
- **Confirm** → `registry_put(...)` linking the UPC to the game (+ platform, cover), then delete the review row (or mark it gone).
- **Reject** → set `status='dismissed'` (so the drip never re-surfaces that pair).

Web is the canonical editor (per `web-main-mobile-streamlined`); this review surface stays on the web. Nothing new on mobile.

---

## 4. Phase 2 — Pluggable multi-source resolve + Wikidata

### 4.1 Source chain
Refactor the single `product = lookup_product_title(upc)` step inside `resolve()` into an ordered chain of product sources, each `(upc) -> str | None` (the product/game title), tried in order until one returns a non-empty result:
```
PRODUCT_SOURCES = (lookup_product_title, lookup_wikidata_gtin, ...)   # UPCitemdb first, then Wikidata
```
`resolve()` iterates the chain; the first hit supplies the `product` title that the rest of `resolve()` already consumes (clean → parse platform → IGDB candidates → registry write). Everything downstream is unchanged. The chain is a module-level tuple so new free sources slot in trivially (extensibility is the point).

### 4.2 Wikidata GTIN source
`barcode.lookup_wikidata_gtin(upc: str, *, timeout=...) -> str | None`: query the Wikidata SPARQL endpoint for an item whose GTIN property equals the UPC and which is (an instance/subclass of) a video game, returning the item's English label. Free, keyless, no rate limit. Degrades to `None` on any failure.

**⚠️ Implementation note:** verify the exact Wikidata property id for GTIN and the SPARQL shape at implementation; coverage for video games is sparse, so this source will hit rarely — that is expected and acceptable.

---

## 5. Flow & endpoints

- **Drip (automatic):** daemon thread → `enrichment.run_batch(conn, budget)` once/day → writes registry (confident) + `upc_review` (uncertain/no_match).
- **`POST /api/enrichment/run`** — trigger a batch now (respects the daily cap). Returns started/already-running/quota-exhausted.
- **`GET /api/enrichment/status`** — the status object (§3.3) for live progress.
- **`GET /api/enrichment/review`** — list `pending` review candidates (game + candidate product).
- **`POST /api/enrichment/review/<id>/confirm`** — link the UPC to the game (registry_put) + clear the row.
- **`POST /api/enrichment/review/<id>/reject`** — mark `dismissed`.
- **`resolve()` (enhanced, Phase 2):** product lookup now iterates `PRODUCT_SOURCES`; return shape unchanged.

---

## 6. Error handling & constraints
- Every external lookup (name-search, /lookup, Wikidata) degrades to `None`/`[]`; the worker and `resolve()` never raise out to the caller (matching `barcode.py`'s contract).
- Hard daily quota stop — never exceed the trial cap (avoid an IP ban).
- Worker exceptions are logged and isolated; they never crash the Flask app (daemon thread, broad-but-logged guard at the batch boundary, specific exceptions inside).
- Named constants for the endpoint URLs, daily budget, and confidence thresholds at module scope.
- One error pattern per module (these modules degrade/return, as `barcode.py` does).

---

## 7. Testing

**pytest (all external calls mocked):**
- `search_products_by_name` parses UPCitemdb name-search results; failure → `[]`.
- Confidence classifier: confident (title+platform match) / uncertain / no_match, including the platform-absent case.
- Worker idempotency: skips pairs already in `barcode_registry`, `pending`, `no_match`, or `dismissed`; selects only eligible pairs.
- Quota cap: the batch never exceeds the daily budget.
- Auto-link writes `barcode_registry` with game_id + cover; uncertain writes a `pending` `upc_review` row; no-match writes a `no_match` row.
- Review confirm → `registry_put` + row cleared; reject → `dismissed`.
- `upc_review` migration present + idempotent; enrichment-state persists.
- Wikidata source parses a mocked SPARQL response → title; failure → `None`.
- Source chain: first source hit wins; on miss it falls through to the next; all-miss → no product (existing `source:'none'` path).

---

## 8. Build order (two plans)
1. **Plan A — Phase 1:** `upc_review` schema + enrichment state; `search_products_by_name` + confidence classifier; `enrichment.run_batch` (idempotent selection, quota); daily-drip launcher + status; enrichment routes; web Needs-review UI. Fully pytest-covered. **Verify the UPCitemdb /search keyless risk before building the worker on top of it.**
2. **Plan B — Phase 2:** refactor `resolve()` product lookup into `PRODUCT_SOURCES` chain; add `lookup_wikidata_gtin`; tests for chain + Wikidata. Smaller; independent of Plan A.

---

## 9. Risks
- **UPCitemdb name-search keyless availability (#1)** — if `/search` needs a key or isn't on the free tier, Phase 1's auto-source is blocked; verify first, surface to owner, don't silently work around.
- **Match precision** — name-search returns noisy products; the confident gate must be strict (exact normalized-title + platform) so wrong UPCs never auto-link; everything else goes to review.
- **Wikidata sparsity** — low immediate coverage for games; value is the extensible seam, not the hit rate.
- **"Daily" reliability** — the drip only advances on days the app is running; full collection coverage takes a week-plus of app uptime. Acceptable, documented.
- **Trial IP ban** — exceeding the quota risks a block; the hard cap + throttle mitigate it.
