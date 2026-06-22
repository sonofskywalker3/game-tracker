# Game Tracker — Android Companion Phase 2: App Core + Embedded VPN (Design)

**Date:** 2026-06-21
**Status:** Approved design, pre-implementation
**Parent spec:** `docs/superpowers/specs/2026-06-20-android-companion-app-design.md` (master design, approved)
**Topic:** The native Android app's first shippable slice — project scaffold, Settings,
Retrofit API client, the editable **Picks** home + carousel, **Library** browse/search/filter,
interactive **Game detail**, and the embedded **WireGuard VPN** (built last, non-blocking).

---

## 1. Scope & Context

Phase 1 (backend barcode endpoints) is shipped and pushed. Phase 2 builds the app itself.
The master spec §4 locks the architecture: a **native Kotlin/Jetpack Compose** thin client
that talks to the existing Flask backend's JSON API, with an **embedded WireGuard VPN** as the
private transport. This phase delivers everything except barcode scanning (Phase 3) and the
Glance widget (Phase 4).

### Decisions resolved in brainstorming (2026-06-21)
- **VPN sequencing:** *Decoupled, VPN last.* The app is built and verified against the backend
  over the home **LAN** (phone → desktop on WiFi) first. The embedded VPN is the final Phase 2
  task, so nothing blocks while the owner's Firewalla config is being set up.
- **WireGuard pairing:** *Firewalla generates, app imports via QR.* Firewalla Purple's built-in
  WireGuard VPN Server produces a per-device client profile shown as a **QR code**; the app
  scans it (with paste-`.conf` as a fallback) and stores it on-device. No keys baked into the
  build; no chicken-and-egg wait.
- **Picks carousel:** *Full-bleed swipe deck* — one large cover centered at a time with a peek
  of neighbors, swipe between slots, page indicator.
- **Game detail:** *Interactive status change allowed* — the one scope addition over the master
  spec (master said read-only v1). Hitting an existing endpoint, low cost, matches the core
  "sitting down to play → mark it playing/beaten" moment.

### Non-goals (this phase)
- No barcode scanning (Phase 3), no Glance widget (Phase 4).
- No scrape/import/dedup/audit/decider-chat on mobile (master non-goals stand).
- No in-app login — the private VPN remains the auth boundary.
- No local game database — the app holds no persistent game store; it fetches via the API.
  (Only Settings — base URL, WG config — is persisted on-device.)

### Backend impact
**None.** Every endpoint Phase 2 consumes already exists (verified in `app.py`). The deferred
carryover (populate `barcode_cache.igdb_id` on `POST /api/games`) is **not** addressed here; it
moves to Phase 3, where the app first reads the cache. `game_id` remains the load-bearing link.

---

## 2. Architecture (app internals)

```
Compose UI (screens)
      │  observes StateFlow
ViewModels (one per screen)  ──►  Repository  ──►  Retrofit GameTrackerApi  ──►  Flask JSON API
      │                                │                                          (LAN now,
   UI state                       Settings (DataStore: baseUrl, wgConfig)          VPN later)
```

- **Single-module** `android/` app. **Manual DI** via one `AppContainer` (built in `Application`)
  — no Hilt; a one-user app doesn't need the codegen overhead. ViewModels receive the
  `Repository`; `Repository` receives the `GameTrackerApi` + `SettingsStore`.
- **Retrofit base URL is dynamic.** OkHttp uses an interceptor (or a per-call `@Url`/host
  selector) that reads the current base URL from `SettingsStore`, so changing the URL in
  Settings — or later pointing at the VPN endpoint — needs no app restart.
- **Each ViewModel** exposes a sealed `UiState` (`Loading | Success(data) | Empty | Error(msg)`).
  Every screen renders all four states; no screen can show a blank/crash on a backend miss.
- **Error model:** network/HTTP failures surface as `UiState.Error` with a retry affordance and,
  when the host is unreachable, the "Can't reach Game Tracker — VPN connected?" message from the
  master spec §6.

### Module/package layout (under `android/app/src/main/java/com/gametracker/companion/`)
- `data/` — `GameTrackerApi` (Retrofit interface), DTOs, `Repository`, `SettingsStore`.
- `ui/picks/`, `ui/library/`, `ui/detail/`, `ui/settings/` — each a Composable screen + ViewModel.
- `ui/common/` — shared composables (cover image, state scaffolding, error/empty/loading).
- `vpn/` — WireGuard config parse/import + tunnel `VpnService` + foreground service (last task).
- `di/AppContainer.kt`, `App.kt` (`Application`), `MainActivity.kt`, `Nav.kt` (NavHost).

### Build / tooling
- **Gradle wrapper** (`./gradlew installDebug`), JDK 17 (present). Min SDK 26, target current.
- App id: **`com.gametracker.companion`**.
- Setup task wires **`ANDROID_HOME`** to the owner's existing SDK and locates **`adb`** (owner
  confirms USB debugging is ready). Python `ruff`/`pytest` gates ignore `android/`.

---

## 3. API Client (the contract the app is built against)

Retrofit interface + kotlinx-serialization DTOs matching the **verified** backend shapes.
All endpoints are existing; no backend change.

| Method | Endpoint | Use | Response (key fields) |
|--------|----------|-----|-----------------------|
| GET | `/api/games` | Library list (supports `?status=&platform=&search=&sort=&order=`) | `[{id,title,cover_url,status,rating,priority,hours_played,platforms[],categories[],tags[],physical,series_name}]` |
| GET | `/api/games/{id}` | Detail | game fields + `status,rating,hours_played,notes,platforms[]{id,name,short_name},tags[],dlc[]{id,name,kind,owned,source},external_ids{}` |
| PUT | `/api/games/{id}` | **Change status** (and rating/notes/hours if added later) | `{success:true}`; body e.g. `{"status":"playing"}` |
| GET | `/api/igdb/search?q=` | Add-via-search (used by the slot-pin picker / future Add) | `[{name,slug,cover_url,igdb_url,platforms[]}]` |
| POST | `/api/games` | Add a game (text-search result) | `201 {game_id}` / `409 {error,game_id}` |
| GET | `/api/slots` | Full slate | `{slots:[{id,label,goal,sort_order,current_game,candidates[]}],recently_finished:[]}` |
| POST | `/api/slots/{id}/pin` | Assign game to slot | body `{game_id, goal?}` → `{ok:true}` |
| POST | `/api/slots/{id}/outcome` | Beat/complete/dropped/swap | body `{outcome, chase?, new_goal?}` → `{ok:true}` |
| PATCH | `/api/slots/{id}/goal` | Edit slot goal | body `{goal}` → `{ok:true}` |
| POST | `/api/slots/reorder` | Reorder slots | body `{slot_ids:[...]}` → `{success:true}` |

**Status enum** (the detail dropdown, sourced from the web UI): `backlog, playing, parked,
completed, 100, dropped, wishlist`. Display mapping: `100` → "100%", `completed` → "complete",
others title-cased. Outcome → status mapping lives server-side (`beat`→`completed`,
`complete`→`100`, `dropped`→`dropped`); the app just sends the outcome verb.

**DTO nullability note:** `current_game`, `cover_url`, `rating`, `hours_played`, `goal`,
`series_name`, and most `ur.*` fields are nullable — DTOs model them as nullable Kotlin types so
a fresh DB or an unrated game never crashes deserialization.

---

## 4. Screens

### 4.1 Picks (home / start destination)
- **Carousel:** `HorizontalPager` of slots that have a `current_game`. Each page: full-bleed
  cover, slot `label`, and `goal`, with a peek of neighbors and a page indicator. Tapping a page
  surfaces that slot's actions (same as its row below).
- **Slot list:** every slot as a row. Editable, **no chat**:
  - **Assign** → opens a library search picker (typeahead over `/api/games` or
    `/api/games/search`) → `POST /api/slots/{id}/pin`.
  - **Outcome** → Beat / 100% / Dropped / Swap → `POST /api/slots/{id}/outcome`. (Beat offers
    the "chase" option the backend supports; Swap frees the slot.)
  - **Edit goal** → inline text → `PATCH /api/slots/{id}/goal`.
  - **Reorder** → drag handles → `POST /api/slots/reorder` with the new `slot_ids` order.
  - **Empty slot** (no `current_game`) → shows top `candidates`; tap one to pin it.
- After any mutation the screen re-fetches `/api/slots` (single source of truth; no optimistic
  local model to drift).

### 4.2 Library
- `LazyVerticalGrid` of cover cards (title fallback when `cover_url` is null).
- **Search-as-you-type** (debounced; `?search=` server-side or client filter of the loaded list).
- **Filter chips:** platform (`?platform=short_name`) and status (`?status=`). Tap a card → detail.

### 4.3 Game detail (interactive)
- Cover, title, platforms, hours, rating, owned **DLC** list (read-only list).
- **Status control:** a dropdown/segmented control over the status enum → `PUT /api/games/{id}`
  with `{status}`. On success, reflect the new status; if the new status is a finished one the
  backend auto-frees any slot — the Picks screen re-fetches when next shown.
- Everything else on detail stays read-only in this phase (rating/notes editing is a later add).

### 4.4 Settings
- **Backend base URL** field (DataStore-persisted; sensible default `http://<LAN-IP>:5000`).
- **Test connection** button → GET `/api/games` (or a cheap call) → success/failure toast.
- **VPN section** (populated by the VPN task): import-config entry point, tunnel status, toggle.

---

## 5. Embedded VPN (final Phase 2 task — isolated, non-blocking)

Built last so the rest of Phase 2 ships over LAN regardless of Firewalla readiness.

- **Config import:** a Settings action launches a CameraX preview + ML Kit **QR scanner** that
  reads the Firewalla-generated WireGuard profile QR (a QR-encoded `.conf`). A **paste-`.conf`**
  text fallback covers the case where scanning is inconvenient. The parsed config
  (`[Interface]` private key/address/DNS, `[Peer]` public key/endpoint/allowed-IPs) is stored
  on-device (DataStore/encrypted prefs). Private key never leaves the phone; nothing secret is in
  the build.
- **Tunnel:** `com.wireguard.android:tunnel` backend driving a **foreground `VpnService`**.
  **Per-app split tunnel** via `addAllowedApplication(<this app>)` so only Game Tracker's traffic
  routes through Firewalla. One-time OS VPN-consent dialog on first bring-up.
- **Always-on note:** documented one-time Android toggle ("Always-on VPN for Game Tracker") that
  the Phase 4 widget's background refresh will depend on. Not required for Phase 2's foreground
  use; called out so it's not a surprise later.
- **Settings surface:** connection state (connected/connecting/down) + a manual toggle.
- **Verification:** with the VPN up and the phone off the home WiFi (cellular), the same screens
  reach the backend through the tunnel — manual on-device smoke.

---

## 6. Error Handling

- **Unreachable backend / no VPN:** `UiState.Error` with the "Can't reach Game Tracker — VPN
  connected?" copy and a retry button. Never a crash or blank screen.
- **Mutation failures** (pin/outcome/goal/reorder/status): snackbar/toast, leave the user on a
  usable screen, and re-fetch to resync truth.
- **Deserialization:** nullable DTOs (see §3) absorb missing/optional fields.
- **VPN:** config parse failure → explanatory error on the import screen; camera-permission
  denial → rationale prompt; tunnel bring-up failure → status reflects "down" with a retry.

---

## 7. Testing

- **Unit (JVM):**
  - `Repository` + `GameTrackerApi` against **MockWebServer** — happy path + error/empty/null
    fields for each endpoint group (games, detail, slots, igdb search, mutations).
  - Each ViewModel with a **fake Repository** — state transitions
    (`Loading→Success/Empty/Error`), and that mutations trigger a re-fetch.
  - WireGuard `.conf` parser — valid config, missing sections, malformed input → typed error.
- **Manual on-device smoke** (run/verify skills, real device over LAN, then over VPN): launch,
  Picks carousel + each slot action, Library search/filter, detail status change round-trips to
  the DB, Settings connection test, and (VPN task) QR import + tunnel up off-WiFi.
- **Python side:** unchanged; `uv run python -m pytest` and `ruff check` still green (no backend
  edits). The `android/` tree is outside the Python gates.

---

## 8. Build Order (each independently verifiable against the live backend over LAN)

1. **Scaffold + tooling** — `android/` Gradle project, `ANDROID_HOME`/`adb` wired, app builds and
   installs an empty Compose shell on the device.
2. **Settings + API client** — DataStore base URL, dynamic-host Retrofit, `Repository`, DTOs,
   Test-connection. (First real backend round-trip.)
3. **Picks** — slots fetch, full-bleed carousel, slot list, pin/outcome/goal/reorder.
4. **Library** — grid, search, platform/status filters.
5. **Game detail** — read view + status change.
6. **Embedded VPN** — QR/paste import, parser, tunnel `VpnService`, foreground service, Settings
   status; verify off-WiFi.

---

## 9. Risks / Open Items

- **Embedded VPN** is the biggest new surface (tunnel lib + `VpnService` + foreground service +
  QR parse). Isolated to §5, built last, validated before Phase 4's widget depends on it.
- **Firewalla QR contents** assumed to be a standard QR-encoded WireGuard `.conf`; the
  paste-`.conf` fallback de-risks any format surprise. Confirmed at the VPN task.
- **adb/SDK path** not yet on PATH — resolved in build-order step 1 (JDK 17 already present).
- **Dynamic base URL host-switch** in OkHttp/Retrofit needs care (interceptor vs. host selector);
  picked and unit-tested in step 2.
