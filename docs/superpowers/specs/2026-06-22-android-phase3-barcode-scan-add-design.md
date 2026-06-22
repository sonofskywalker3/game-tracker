# Game Tracker — Android Companion Phase 3: Barcode Scan-Add (+ Text-Search Add) (Design)

**Date:** 2026-06-22
**Status:** Approved design, pre-implementation
**Parent spec:** `docs/superpowers/specs/2026-06-20-android-companion-app-design.md` (master) — §4.2 barcode flow.
**Phase 2 spec:** `docs/superpowers/specs/2026-06-21-android-phase2-app-core-design.md` (app core + VPN, shipped).
**Phase 1 plan:** `docs/superpowers/plans/2026-06-20-android-phase1-barcode-backend.md` (backend resolve chain, shipped).
**Topic:** Add a game to the library from the phone — by IGDB **text search** or **barcode scan** — behind a new **Add** tab, and close the Phase-1 carryover by populating `barcode_cache.igdb_id`.

---

## 1. Scope & Context

Phase 1 shipped the backend resolve chain (`GET /api/barcode/resolve`, `upc` on `POST /api/games`, the self-growing `barcode_cache`). Phase 2 shipped the app core (Settings, dynamic-host API client, Picks, Library, Detail) and the embedded VPN. Phase 3 adds the **Add** experience the master spec §4.1–§4.2 describes: text-search add and barcode scan-add, with the scan resolution producing one of three outcomes.

### Decisions resolved in brainstorming (2026-06-22)
- **Add entry point:** a **new 4th bottom-nav tab** "Add" (Picks / Library / Add / Settings), holding both text search and a "Scan barcode" button. (Chosen over a Library FAB.)
- **Carryover (`barcode_cache.igdb_id`):** **populate it** on `POST /api/games`, now that the app consumes the cache — so a future cache hit can carry the resolved IGDB identity, not just `game_id`.
- **Scan behavior:** **continuous auto-detect** — live camera resolves on the first valid barcode (one-shot guard, reusing the Phase-2 QR pattern), then shows the result.

### Non-goals (this phase)
- No Glance widget (Phase 4).
- No scrape/import/dedup/audit/decider-chat on mobile (master non-goals stand).
- No editing of an existing game from the Add flow beyond creating it (status edits remain on Detail).
- No offline queue — Add requires a reachable backend (same access model as the rest of the app).

### Dependency note
Phase 2 has not yet had owner on-device smoke. Phase 3 is **additive and independent** (new tab + screens), but it exercises the same shared infra (`Repository`, dynamic-host Retrofit, `UiState`, `FakeRepo`). A shared-infra defect found in Phase 2 smoke would also affect Phase 3.

---

## 2. Backend Change (Python repo) — close the carryover

Single small, additive change in `app.py` `api_create_game`. Follows project conventions (`uv`, `ruff check`, `python -m pytest`, temp-DB tests, named constants, typed signatures).

- After the best-effort IGDB enrichment block runs (which may set `games.igdb_id`), read the game's `igdb_id` and pass it to the existing `barcode.cache_put(...)` call on the **created-game** path:
  `cache_put(conn, upc, igdb_id=<game's igdb_id>, title=title, platform=platform_short, game_id=game_id)`.
- On the **already-owned (409)** path, read the existing game's `igdb_id` and include it in that path's `cache_put` too.
- `barcode.cache_put` already accepts `igdb_id` (Phase 1) — this only starts passing it. No schema change, no new endpoint.

**Tests:** extend `tests/test_api_barcode.py`:
- `POST /api/games` with `upc` for a game whose row has an `igdb_id` → the `barcode_cache` row's `igdb_id` matches.
- Already-owned (409) path with `upc` → cache row links `game_id` **and** `igdb_id` of the existing game.
- A game with no `igdb_id` (enrichment unavailable) still caches (igdb_id stays null) — no regression.

This is the only backend work in Phase 3. All other endpoints (`/api/barcode/resolve`, `/api/igdb/search`, `POST /api/games`) are unchanged and already shipped.

---

## 3. API Client Additions (Android `data/`)

Additive to the Phase-2 Retrofit/DTO layer. No change to existing DTOs except `CreateGameBody`.

### New DTOs
```
BarcodeResolveResponse(upc: String, source: String, candidates: List<BarcodeCandidate> = [], productTitle: String? = null)   // @SerialName("product_title")
BarcodeCandidate(igdbId: Int? = null, title: String? = null, platform: String? = null, coverUrl: String? = null, ownedGameId: Int? = null)
   // @SerialName: igdb_id, cover_url, owned_game_id
```
(`source` is one of `cache` | `upc_api` | `none`. `Json { ignoreUnknownKeys = true }` already set.)

### New endpoint + Repository methods
- `GameTrackerApi.resolveBarcode(@Query("upc") upc: String): BarcodeResolveResponse` → `GET api/barcode/resolve`.
- `Repository.resolveBarcode(upc): Result<BarcodeResolveResponse>` (`runCatching`).
- Add a nullable `upc: String? = null` field to the existing `CreateGameBody` (backend already reads it). Extend `Repository.createGame(...)` (or add a parameter) so the Add/Scan flows can post `title`, `coverUrl`, `platforms`, `physical`, and `upc`.

---

## 4. Add Tab (Android `ui/add/`)

New bottom-nav destination "Add" (4th tab). `AddScreen` + `AddViewModel`.

- **Text search:** a debounced `OutlinedTextField` → `Repository.igdbSearch(q)` (exists) → `UiState<List<IgdbResult>>` list (cover + name + platforms). Tapping a result calls `createGame(title=name, coverUrl, platforms, physical=false, upc=null)` → on success a confirmation (snackbar) and navigates to the new game's Detail (using the returned `game_id`).
- **Scan barcode button** → navigates to the Scan screen (`scan` route).
- **Prefill support:** the screen accepts an optional initial query (used by the Scan "no match" fallback to prefill `productTitle`).
- `AddViewModel`: `searchResults: StateFlow<UiState<List<IgdbResult>>>`, `onSearch(q)`, `add(result): Result<Int?>` (returns new game_id). TDD'd with `FakeRepo`.

Handling the existing-game case: `createGame` returns `201 {game_id}` or `409 {error, game_id}`. On 409 (already owned), surface "Already in your library" and still offer to open that game's Detail via the returned `game_id`.

---

## 5. Scan Screen (Android `ui/scan/`) — continuous auto-detect

CameraX + ML Kit barcode scanning (reusing the Phase-2 `QrScanScreen` structure: CameraX preview + `ImageAnalysis` + ML Kit, with a one-shot `fired` guard so a single decode is acted on). Configured for product formats (UPC-A/UPC-E/EAN-13/EAN-8), not just QR.

Flow (master spec §4.2), driven by `ScanViewModel` as a small state machine:
- `Scanning` → first valid barcode → `Resolving` (`Repository.resolveBarcode(upc)`).
- On result, branch by candidate ownership/source:
  - **Already owned** — a candidate has `ownedGameId != null` → state `Owned(gameId, title, platform)`. UI: "You own this — <platform>" + **View** (→ Detail) + **Scan again**.
  - **Resolved candidate(s)** — `source != none`, candidates present, none owned → state `Candidates(list, upc)`. UI: show candidate(s) (cover + title + platform) → **Add** → `createGame(title, coverUrl, platforms=[platform], physical=true, upc=upc)` → success → "Added" + open Detail.
  - **No match** — `source == none`, or `upc_api` with only a `productTitle` and no candidates → state `NoMatch(upc, productTitle?)`. UI: a "Couldn't identify it" message + **Search manually** → navigates to the Add tab prefilled with `productTitle` (if any). The manual add MUST still post the `upc` so the cache learns the mapping (the Add screen, when reached from a no-match scan, carries the pending `upc` and includes it in `createGame`).
- `Resolving`/adding failures (network) → `Error` state with **Retry**; camera-permission denial → rationale prompt (same pattern as Phase-2 QR).

`ScanViewModel` is TDD'd against `FakeRepo` for the resolve→state transitions and the add action (camera capture itself is on-device smoke). To test resolve outcomes, `FakeRepo` gains a `resolveBarcode` stub backed by a settable `BarcodeResolveResponse` (and the create path already records via `createGame`).

---

## 6. Navigation & wiring

- Bottom nav becomes 4 tabs: Picks / Library / **Add** / Settings (start destination still Picks).
- New routes: `add` (optionally `add?prefill={q}&upc={upc}` for the no-match handoff) and `scan`.
- `AppViewModelFactory` gains `AddViewModel` and `ScanViewModel` branches.
- The "Add" tab icon: a non-deprecated Material icon (e.g. `Icons.Filled.Add`).

---

## 7. Error Handling

- Unreachable backend / no VPN → `UiState.Error` with the standard "Can't reach Game Tracker — VPN connected?" copy + retry (Add search and Scan resolve).
- Resolve degrades gracefully: the backend never 500s the resolve (Phase 1); `source: none` is a normal "no match" path, not an error.
- `createGame` failures → snackbar; the user stays on a usable screen. 409 (already owned) is handled as information, not a hard error.
- Camera permission denied → explanatory prompt; nullable DTOs absorb sparse resolve payloads.

---

## 8. Testing

- **Backend (pytest, temp DB):** the three `igdb_id` cases in §2.
- **Android (JVM unit):**
  - `Repository.resolveBarcode` via MockWebServer — cache hit (owned), `upc_api` candidates, `source: none`, product-title-only no-match, HTTP error → `Result.failure`.
  - `AddViewModel` (fake repo) — search populates results; `add` posts and returns game_id; 409 surfaces "already in library".
  - `ScanViewModel` (fake repo) — resolve → `Owned` / `Candidates` / `NoMatch` transitions; confirm-candidate triggers `createGame` with `upc` + `physical=true`; no-match carries `productTitle`/`upc` to the Add handoff.
- **On-device smoke (owner):** scan a real game barcode → owned/candidate/no-match paths; text-search add; verify the added game appears in the web app/DB with the Physical tag and (for scans) a `barcode_cache` row carrying `game_id` + `igdb_id`.

---

## 9. Build Order (each independently verifiable)

1. **Backend `igdb_id` carryover** — `api_create_game` cache_put + tests (Python gates). Independently shippable.
2. **API client** — `BarcodeResolveResponse`/`BarcodeCandidate` DTOs, `resolveBarcode`, `CreateGameBody.upc` + Repository — MockWebServer tests.
3. **Add tab** — `AddViewModel` + `AddScreen` (text search + add + Scan button), nav 4th tab.
4. **Scan screen** — `ScanViewModel` state machine + `ScanScreen` (CameraX/ML Kit continuous), three-outcome handling + no-match handoff to Add.

---

## 10. Risks / Open Items

- **ML Kit product-barcode reliability** varies by lighting/box; continuous auto-detect + the one-shot guard mitigate misfires; manual-search fallback always works.
- **UPCitemdb free-tier coverage/limit** (~100/day) — mitigated by the cache (repeat scans free) and manual fallback (Phase-1 behavior; unchanged).
- **No-match → Add handoff** must thread the pending `upc` through navigation so the manual add still grows the cache — called out in §5 and tested in §8.
- **Phase 2 on-device smoke still pending** — shared infra (networking) unverified on the device; see §1.
