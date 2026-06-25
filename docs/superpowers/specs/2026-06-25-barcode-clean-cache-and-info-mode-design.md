# Barcode Cleaning Fix + Side-Effect-Free Resolve + Info Mode — Design

**Date:** 2026-06-25
**Status:** Approved direction (owner decisions captured), pre-implementation
**Parent / supersedes:** Builds on `2026-06-22-scan-for-info-per-platform-upc-design.md` (Core, largely shipped) and the Spec-2 enrichment worker (`2026-06-23-upc-enrichment-worker-design.md`, shipped). **Supersedes** that Core spec's §4 line 85 ("every scan writes `barcode_registry`, including unmatched") and its §7 mobile-scan UX (5 s auto-re-arm + dismiss/X), for the reasons in §1.
**Topic:** Stop the interactive scan path from poisoning the UPC registry, make resolve read-only, fix retail-title cleaning so confident matches actually resolve, and add an **Info mode** for hands-free, registry-only continuous scanning (e.g. walking a store shelf).

---

## 1. Context & motivation

Three real, repeatable scans (Super Mario 3D All-Stars, Link's Awakening, Bravely Default II) each failed the first time, then on the *second* scan returned a name with **no cover and no game link**. Root cause, confirmed in code:

1. **`clean_product_title()` leaves noise that defeats IGDB.** It strips `"nintendo switch"` but not a bare `"Nintendo"`, and never strips standalone catalog/UPC digit runs. So `Super Mario 3D All-Stars Nintendo 045496596743` passes through unchanged, IGDB title-search returns nothing, and the scan is treated as a no-match.
2. **`resolve()` caches every scan, including no-matches** (`barcode.py:343-352`, `registry_put(... game_id=None)`), then short-circuits on that partial next time (`barcode.py:279`). That is the poisoning: scan #1 writes a cover-less guess, scan #2 serves it.

The enrichment **worker** already does this correctly — confident → `barcode_registry`, uncertain/no-match → the separate `upc_review` table, and "a failed call is never recorded as no_match (no poisoning)" (`enrichment.py:142`). The interactive `resolve()` simply never adopted that discipline. This spec brings `resolve()` in line with the worker and supersedes the older "capture every scan" intent (which is what fed the registry garbage).

### Owner decisions (2026-06-25, from brainstorming)
- **Cache rule:** auto-save **confident** matches only; never cache incomplete entries. A no-match always re-prompts manual search on the next scan (no stale short-circuit).
- **Disambiguation:** when IGDB returns **multiple** candidates, the user **picks** which one **before** the platform is resolved. Order: pick game → resolve platform (parsed if known, else user taps) → save.
- **Platform is required** on a saved registry row: use the platform parsed from the retail title when available; otherwise the user selects it.
- **Mode name:** "**Info mode**" (not "batch" / "read-only" — it still *writes* the registry, just never the library).

### Non-goals
- No raw "every UPC ever seen, even unmatched/unowned" scan-log for the future open dataset. Confident scans still grow the registry; capturing unmatched-and-unowned UPCs would need a new store and is deferred (YAGNI).
- No web changes. Web stays the canonical editor; this is backend + Android.
- No change to the enrichment worker, `upc_review`, or the per-platform format / `has_digital_market` model from the Core spec.

---

## 2. Part A — Backend (`barcode.py`, `app.py`)

### A1. Smarter retail-title cleaning (`clean_product_title`)
Two extensible additions, same lookup-table pattern as the existing `_RETAIL_NOISE_WORDS`:

- **Publisher names** — a new module-level tuple `_PUBLISHER_NOISE_WORDS` (e.g. `Nintendo`, `Sony`, `Microsoft`, `Sega`, `Capcom`, `Square Enix`, `Bandai Namco`, `Ubisoft`, `Electronic Arts`, `Activision`, `Konami`, `Atlus`). Stripped as standalone words (word-boundary), folded into the existing `_NOISE_RE` pipeline.
- **Catalog / embedded-UPC digit runs** — strip standalone digit runs of **5+ digits** via `\b\d{5,}\b`. The 5-digit floor preserves legitimate title numbers (`1942`, `1943`, `FIFA 23`, `2K23` is alphanumeric anyway). Applied as its own regex step in the pipeline, before whitespace collapse.

Outcome: `Super Mario 3D All-Stars Nintendo 045496596743` → `Super Mario 3D All-Stars`; `The Legend of Zelda: Link's Awakening 110249` → `The Legend of Zelda: Link's Awakening`.

### A2. `resolve()` becomes side-effect-free
- Remove the `registry_put(...)` call in `resolve()` (`barcode.py:346`). `resolve()` only **reads**: cache → product source → IGDB match, and returns candidates + parsed platform. It never writes.
- Because it never writes, no incomplete entry can ever be cached, and an unknown barcode always re-resolves (re-prompting search) on the next scan. This is the structural poisoning fix.
- The cache **read** at the top (`registry_get`) is unchanged: a previously-linked complete row still short-circuits to an instant answer.

### A3. Single write path: `POST /api/barcode/link`
A new endpoint is the *only* registry writer for the scan flow (the existing `POST /api/games` "Add to library" path keeps writing its own UPC row as today):

```
POST /api/barcode/link
{ upc, igdb_id, title, cover_url, platform, game_id? }  ->  200 {ok: true}
```
- Calls `barcode.registry_put(...)` (idempotent upsert on `upc`). `game_id` is optional — Info mode usually omits it (knowledge without ownership); it's set when the scanned game is already in the library (matched by normalized title) or added.
- Validates `upc` + `platform` present; `igdb_id`/`title`/`cover_url` describe the chosen game.

### A4. Resolve response (unchanged shape, no write)
`GET /api/barcode/resolve` returns the same JSON it does today (`upc, source, scanned_platform, candidates[…], product_title?`) — only the side effect is removed. Clients decide what to link.

---

## 3. Part B — Client resolution logic (normal **and** Info mode)

After `resolve()` returns, the client (Android) follows one decision tree, shared by both modes:

| Resolver outcome | Behaviour |
|---|---|
| **1 candidate, platform parsed** | Auto-call `/link` (no taps). Show the result card. |
| **1 candidate, platform unknown** | Show a platform chip-row; on tap → `/link`. |
| **>1 candidate** | Show a **picker**; user taps the game → (platform parsed? else chip-row) → `/link`. |
| **0 candidates** | **Manual search modal** (existing IGDB search, prefilled with the cleaned product title) → user picks a game → (platform) → `/link`. |

**"Complete" gate for auto-save:** a candidate is only auto-linked when it has a **cover_url** (the owner's "real match with cover art" rule). A single candidate *missing* a cover is not auto-saved — it falls through to the picker/confirm affordance so the user opts in. This keeps coverless guesses out of the registry even when IGDB returns exactly one.

Normal mode additionally shows the existing **library actions** (Add to library / Add the [platform] copy / owned banners) on the result card. Info mode shows **no library actions** — only the resolved info + the link affordances above.

The current `ScanViewModel` only ever surfaces the **top** candidate (`candidates.firstOrNull()`); the multi-candidate **picker** state is new work in this spec.

---

## 4. Part C — Info mode (Android)

### C1. Entry & scope
- A **toggle** on the scan screen labelled **"Info mode"** (persisted via DataStore so it survives navigation; default off).
- When on: registry-building only — **no** `POST /api/games`, no `game_platforms` writes, no ownership mutation. The only write is `POST /api/barcode/link`.
- A small **read-only "already owned"** indicator is shown when the resolved title matches a library game (buy-avoidance at the shelf), but no add button.

### C2. Presence-based re-arm (replaces the 5 s delay, Info mode only)
- Today: after an add, `delay(REARM_MS=5000)` then re-arm (`ScanScreen.kt:58`). In Info mode this is replaced by **barcode-presence** re-arm:
  - After a scan fires and resolves, the camera stays live and the result card shows; the scanner does **not** re-fire while a product-format barcode is still detected in frame.
  - Once a short run of frames reports **no** product-format barcode (item moved out of view), it **immediately** re-arms (`fired = false`) — ready for the next item with zero delay and zero taps.
  - A small debounce (N consecutive empty frames, e.g. 3) prevents flicker/jitter from re-arming mid-scan.
- The analyzer already receives detected codes per frame (`ScanScreen.kt:74`); it gains a "barcode present this frame" signal feeding the re-arm gate.

### C3. Card / camera layout
- The result card stays at the **bottom** with the live camera visible above it (today's `ResultCard` layout already does this). **No dismiss/X needed** in Info mode — the next scan replaces the card.
- Manual-search and the candidate picker still appear as needed; completing either returns straight to live scanning.

### C4. Normal mode unchanged
Normal (Info mode off) keeps today's behaviour: library action buttons, tap-off / 5 s auto-re-arm. Only the *backend* caching discipline (Part A) changes for both modes.

---

## 5. Components & boundaries

- **`barcode.py`** — `clean_product_title` (A1), `resolve` made read-only (A2). Pure functions, fully unit-testable without network (existing pattern).
- **`app.py`** — new `api_barcode_link` route (A3); `api_barcode_resolve` loses its write side effect (A4).
- **Android `ScanViewModel`** — gains: `infoMode` flag, a `Picker(candidates)` state, a `NeedsPlatform(candidate)` state, `link()` / `pick()` / `choosePlatform()` intents, and presence-based re-arm gating. The state machine is the main new testable unit.
- **Android `ScanScreen`** — Info-mode toggle, picker UI, platform chip-row, presence signal wired from the analyzer; conditionally hides library actions.
- **Android `Repository` / `GameTrackerApi`** — `link(upc, igdb_id, title, cover_url, platform, gameId?)` → `POST /api/barcode/link`.

---

## 6. Testing

**pytest (backend)** — `uv run python -m pytest`:
- `clean_product_title`: strips standalone publisher names; strips 5+-digit catalog/UPC runs; **preserves** short title numbers (`1942`, `FIFA 23`); the three real failing titles clean to their bare game names.
- `resolve()` writes **nothing** to `barcode_registry` (no-match, single-match, and multi-match cases) — assert row count unchanged.
- `resolve()` still **reads** an existing complete registry row and short-circuits.
- `POST /api/barcode/link`: upserts a row (idempotent on re-post); rejects missing `upc`/`platform`; stores `game_id` when supplied and NULL when omitted; second UPC for the same `game_id` coexists (multi-UPC-per-game preserved).

**Android (JVM unit)** — mirrors `ScanViewModelTest`:
- Decision tree: 1-candidate-known-platform → auto-link; 1-candidate-unknown-platform → NeedsPlatform; >1 → Picker; 0 → manual search.
- Presence re-arm: fires once while a code is present; re-arms only after N empty frames; does not double-fire on the same held barcode.
- Info mode hides library actions; normal mode shows them.

---

## 7. Build order

One implementation plan, two stages at a clean seam:
1. **Backend (A1–A4):** cleaning fix, read-only `resolve`, `/api/barcode/link`. Fully pytest-covered. Shippable on its own — it fixes the poisoning immediately for the existing app.
2. **Android (B, C):** shared resolution decision tree (picker + platform selection), then the Info-mode toggle + presence re-arm + conditional library actions. JVM-tested.

---

## 8. Risks

- **Over-stripping in cleaning** — a publisher word that's genuinely part of a title, or a 5+-digit run that's part of a name, could be removed. Mitigation: word-boundary matching, the 5-digit floor, and unit tests pinning known-good titles. Lists are extensible if a real counter-example appears.
- **Presence re-arm flicker** — ML Kit may drop/re-detect a code across frames; the N-empty-frame debounce guards against premature re-arm. Tune N on-device.
- **Extra round trip** — confident singles now take a `resolve` + a `link` call instead of one combined call. Negligible cost, and it buys a side-effect-free resolver that can't poison.
- **Wrong auto-linked single** — a confident-but-wrong top match auto-links. It is correctable (re-scan after the cleaning fix, or relink), and far better than the cover-less garbage being replaced. Multi-candidate cases require explicit user choice, limiting exposure.
