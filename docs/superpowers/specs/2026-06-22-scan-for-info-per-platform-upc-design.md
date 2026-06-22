# Scan-for-Info + Per-Platform UPC Registry (Core) — Design

**Date:** 2026-06-22
**Status:** Approved direction (owner decisions captured), pre-implementation
**Parent:** Android companion app (Phases 1–3 shipped) + the 2026-06-22 UI-polish pass. This is the **Core** of a larger UPC/ownership effort.
**Topic:** Turn "scan to add" into a **scan-for-info** experience backed by a permanent, per-platform **UPC registry**, with cross-platform + multi-pack ownership awareness, per-platform physical/digital format, and stubbed `mobile` / `subscription` platform categories.

---

## 1. Context & goals

On-device testing turned the barcode scanner from "add a game" into a richer need: **before buying a game in a store, tell me whether I already own it — on any platform, in any format, including as part of a multi-pack.** Two supporting needs fell out of that:

1. A **permanent UPC↔game registry** (not a flippant cache) that captures *every* scan — the seed for a future crowdsourced open video-game-UPC dataset.
2. Better **scan matching** — today a scan of "Paper Mario: The Thousand-Year Door" returns every IGDB version (GameCube original, Switch remake, fan games, ROM hacks) because platform is discarded.

### Owner decisions (2026-06-22, from brainstorming)
- **Pricing (retail/digital price display): dropped.** Out of scope entirely.
- **UPC population:** organic growth (every scan) + best-effort free-source enrichment — but the enrichment worker is **Spec 2**, not Core. No paid source now (PriceCharting Legendary CSV noted as the only realistic paid bulk-UPC fallback if ever wanted).
- **Roadmap:** Spec 1 = this Core → Spec 2 = enrichment worker → Spec 3 = subscriptions catalog.
- **Multi-pack ownership:** in Core.
- **Subscriptions + mobile categories:** **stub** them in Core (categories + seed list + picker), no availability catalog yet.
- **Rename** `barcode_cache` → `barcode_registry` to signal permanence.

### Non-goals (Core)
- No price display (dropped).
- No UPC back-fill worker (Spec 2).
- No subscription/mobile **availability catalogs** or scraping (Spec 3+); Core only adds the categories, seeds, and pickers.
- Not a web redesign — web gets the minimal editors needed to set per-platform format and manage the new categories (web remains the canonical editor; mobile stays a streamlined companion).

---

## 2. Data model

### 2.1 Rename `barcode_cache` → `barcode_registry`
A guarded migration: `ALTER TABLE barcode_cache RENAME TO barcode_registry` **only if** the old table exists and the new one does not (preserving every row); fresh DBs create `barcode_registry` directly. Helper renames: `cache_get/cache_put` → `registry_get/registry_put`; `migrate_barcode_cache` → `migrate_barcode_registry`. Update references in `barcode.py`, `app.py`, `models.py`, `tests/`, `conftest.py`.

Schema unchanged otherwise:
`barcode_registry(upc PK, igdb_id, title, platform, game_id → games(id) ON DELETE SET NULL, confirmed_at)`.
Because `upc` is the key, **one game can have many rows** — one per platform's physical edition — which *is* the per-platform UPC model. `registry_upcs_for_game(conn, game_id)` returns `[{upc, platform}]`.

**Semantic split (load-bearing):** `barcode_registry` = "what game is this UPC" (knowledge); `game_platforms` = "what I own" (ownership). They never auto-imply each other.

### 2.2 Per-platform format
- `game_platforms` gains `format TEXT CHECK(format IN ('physical','digital')) DEFAULT NULL`.
- Backfill existing rows from each game's current per-game `physical` flag: `format = 'physical'` where `games.physical = 1`, else `'digital'`. (Owner corrects on web.)
- The legacy per-game `physical` flag stays for now (legacy-add UX); the per-platform `format` is the new source of truth for display. No destructive removal in Core.

### 2.3 `platforms.has_digital_market`
- `platforms` gains `has_digital_market INTEGER NOT NULL DEFAULT 0`.
- Seed by category as the default: `modern_console`, `pc`, `mobile`, `subscription` → 1; `legacy_console` → 0.
- **Per-platform overrides** (the seed is a default, not a rule): legacy platforms that *did* have a digital store are set to 1 explicitly — `3DS`, `WiiU`, `PS3`, `X360`, `Vita`, `PSP` (extensible). Pure-cartridge/disc legacy (`SNES`, `N64`, `GC`, `Genesis`, …) stay 0. The override list lives next to the seed so it's editable in one place.
- **Display rule:** the "(Physical/Digital)" qualifier is shown only when `has_digital_market = 1`. So `SNES` renders bare; `PS5` renders `PS5 (Physical)`; `3DS` renders `3DS (Digital)`.

### 2.4 New platform categories (stub) — `mobile`, `subscription`
Mirror the existing `LEGACY_PLATFORM_SEED` pattern with new seeds (idempotent, `INSERT OR IGNORE` by `short_name`):
- **mobile** (`category='mobile'`, `has_digital_market=1`): `iOS`, `Android`.
- **subscription** (`category='subscription'`, `has_digital_market=1`): `Xbox Game Pass`, `PS Plus`, `Nintendo Switch Online`, `EA Play`, `Ubisoft+`, `Amazon Luna`. Extensible — add to the seed tuple.

These are pickable like owning a platform. **No availability catalog in Core** — selecting a subscription records that you have it; "show games available on it" is Spec 3.

---

## 3. Platform-aware scan matching

Today `resolve()` calls `igdb_match.candidates_for(title, set(), …)` with an **empty** platform set, so platform-overlap scoring never fires and `clean_product_title()` discards the platform. Fix:

1. **Parse platform** during title cleanup — the cleaner already matches platform words; capture which matched and map retail phrase → app `short_name` (e.g. "Nintendo Switch" → `Switch`) → IGDB platform id via `igdb_match.platform_ids_for`.
2. **Feed it to the matcher** so platform overlap boosts the right entry, and **filter candidates to that platform** when known (drop entries not released on it).
3. **Exclude fan/mod content** by IGDB `game_type` — keep main game / remake / remaster / port / bundle / expanded; drop mod / fork / fan-game / episode / season / update. (Exact enum values verified at implementation against the live `game_type` field already fetched in `fetch_candidates`.)
4. When the retail title names **no** platform, fall back to today's title-only behavior (no platform filter, no game-type drop beyond existing scoring).

The parsed platform is reused downstream: it's the platform the UPC is stored under in `barcode_registry` and the default platform for "Add".

**Status note (2026-06-22):** only the *first half* of this is shipped — `clean_product_title()` strips retail noise so common titles now match (Mario Kart, Animal Crossing). The platform **parse + filter + game-type exclusion** described here is **not built yet**; it is the fix for "finds every version of Paper Mario / TTYD" and for baking platform specifics into the result. It lands with Core.

**Coverage gaps are separate from matching.** Some UPCs are simply absent from UPCitemdb (observed: certain Nintendo titles, e.g. *Link's Awakening*), so resolution returns *no product at all* — no amount of match-tuning helps. Those degrade to manual search (cleaned prefill) today, and are mitigated by (a) the multi-source resolver / enrichment worker in **Spec 2**, and (b) organic growth as scans are confirmed. Core should treat "no product found" as a first-class state in the scan-for-info UI (offer manual search), not a dead end.

---

## 4. Scan-for-info screen + ownership semantics

Scanning lands on an **info view** (not a straight add). It resolves UPC → game, then shows the game and ownership across platforms, with actions scaled to the situation. Ownership is determined by **title matching** against the library, so it works immediately with no UPC data.

**Every scan writes `barcode_registry`** — including a scan that matches no game (store `upc + product_title`, `game_id` NULL). Nothing is lost.

Ownership states (per resolved single title):
- **Not owned (any platform):** game info + **Add to library** (defaults to the parsed/scanned platform, `format='physical'`).
- **Owned on a different platform (buy-avoidance):** prominent banner listing owned platforms with format qualifiers — *"You already own this on PS5 (Physical)"* / *"on PC (Digital) and PS5 (Physical)"*. Optional **Add the [Switch] copy** (adds that platform + `format` + UPC to the existing game).
- **Owned on the scanned platform:** *"You already own this on Switch (Digital) ✓"* — no add.

Format qualifiers follow §2.3 (only for `has_digital_market` platforms).

---

## 5. Multi-pack / collection ownership

When the resolved game is a bundle/collection (IGDB bundle `game_type`, or has known `collection_name`/constituents), resolve its **constituent titles** (existing `igdb_match.bundle_constituents` reverse lookup) and check the library for each.

Scan-for-info then reports constituent ownership, e.g. scanning **Mega Man X Legacy Collection**:
> *"This collection includes Mega Man X, X2, X3… — you already own Mega Man X on SNES and Mega Man X2 on Switch (Digital)."*

So you can avoid re-buying a compilation whose parts you already have. Same title-matching + format-qualifier rules as §4.

---

## 6. Flow & endpoints

- **`GET /api/barcode/resolve`** (enhanced) returns, per candidate: `igdb_id, title, platform (parsed), cover_url, owned_game_id, owned_platforms[] ({short_name, format})`, and for bundles `constituents[] ({title, owned_game_id, owned_platforms[]})`. Records the scan in `barcode_registry` every call (incl. unmatched).
- **Add new:** existing `POST /api/games` with `{title, cover_url, platforms:[scanned], physical:true, upc}` — extended to write `game_platforms.format='physical'` for the scanned platform.
- **Add a platform to an existing game:** `PUT /api/games/<id>` extended to accept a single `{platform, format, upc}` add (append to `game_platforms` with format; write the UPC row in `barcode_registry`) — without disturbing the existing full-`platforms`-replace path used by the web.
- **Web (canonical editor):** minimal UI to set per-platform `format` and to manage `mobile`/`subscription` category membership. Mobile shows format read-only and uses it in scan-for-info.

---

## 7. Mobile scan UX

- **Remove** the "Scan again" / "Scan another" buttons.
- After a successful scan-add, show the confirmation, then **auto-re-arm the scanner ~5 s later** (hands-free continuous scanning). State machine: `Scanning → Resolving → Result/Added → (5s) → Scanning`.
- The post-scan **found-games list / info modal has an X / Cancel** that dismisses and returns to live scanning immediately.
- **Tapping off the modal** dismisses it **and** re-arms scan mode.
- Move the **Scan entry** from under the Add search bar to a **bottom-center round FAB** with the barcode icon.

Detail-screen platform display stays **read-only** (per the web-main / mobile-streamlined principle); per-platform format editing lives on the web.

---

## 8. Testing

**pytest (backend):**
- `barcode_registry` migration renames in place, preserving rows; fresh DB creates it.
- `clean_product_title` returns both cleaned title *and* parsed platform; platform→short_name→IGDB id mapping.
- Platform-filtered + game-type-filtered matching: a Switch scan excludes wrong-platform entries and mod/fan/ROM-hack `game_type`s; no-platform falls back to title-only.
- Every resolve writes `barcode_registry`, including unmatched (null `game_id`, stored `product_title`).
- Per-platform `format` migration backfill; `has_digital_market` seed; qualifier suppression for non-digital platforms (helper that formats an owned-platform label).
- Multi-pack constituent ownership resolution.
- New `mobile` + `subscription` category seeds present and idempotent.
- Add-a-platform-to-existing-game path writes `game_platforms.format` + a registry row, leaving the web full-replace path intact.

**Android (unit):**
- ScanViewModel state machine: not-owned / other-platform / same-platform / multi-pack result states; auto re-arm after add; cancel/dismiss → scanning.
- DTOs parse `owned_platforms`/`constituents`; FakeRepo extensions; Add-screen FAB entry.

---

## 9. Build order (likely two implementation plans)

Core is large; expect the plan to split into two stages:
1. **Foundations (backend/web):** registry rename; per-platform `format` + backfill; `has_digital_market`; `mobile`/`subscription` seeds; platform-aware matching; enhanced `resolve()`; add/add-platform endpoints; web format/category editors. Fully pytest-covered.
2. **Mobile scan-for-info:** scan-for-info screen + ownership/multi-pack display + the §7 UX (auto re-arm, FAB, cancel/dismiss) on top of the foundations.

If the single plan proves too big, split at this seam.

---

## 10. Risks

- **`game_type` enum drift** — verify exact IGDB values at implementation (already fetched; just need the keep/drop sets).
- **Platform parsing gaps** — some retail titles omit/garble the platform; we fall back to title-only and never hard-fail a scan.
- **Format backfill is a guess** (per-game `physical` → per-platform) — owner corrects on web; not destructive.
- **Multi-pack matching** depends on IGDB bundle data quality; when constituents can't be resolved, degrade to single-title behavior.
- **Stub categories** must not imply availability — selecting a subscription records ownership of the *subscription*, not the games, until Spec 3.
