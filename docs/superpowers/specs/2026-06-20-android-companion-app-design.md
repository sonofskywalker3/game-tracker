# Game Tracker — Native Android Companion App (Design)

**Date:** 2026-06-20
**Status:** Approved design, pre-implementation
**Topic:** A native Android app to view the library, manage Picks, and add games via search/barcode, connecting to the existing Flask backend over an embedded WireGuard VPN.

---

## 1. Goal & Context

The Game Tracker is a Flask web app (SQLite `games.db`, IGDB enrichment, browser-based
vendor scraping, an Anthropic-powered decider). The owner wants to use it from a phone
when sitting down to game — primarily to **see what to play next (Picks)** and to **add
physical games quickly** (search or barcode scan) — without opening a browser or
fiddling with a separate VPN app.

The decision (after exploration) is a **native Android app**, explicitly *not* a PWA or
web wrapper. The native route is chosen for: a first-class camera/barcode experience
(ML Kit), a real home-screen widget, and an embedded VPN so it's a single self-contained
app.

### Non-goals
- No scraping, bulk import, IGDB audit, dedup, or other library-management workflows on
  mobile. Those stay on the desktop/web app.
- No decider **chat** on mobile (Picks are editable, but the AI conversation is web-only).
- No public internet exposure. Access is private, VPN-gated, single-user.

---

## 2. Architecture

```
┌─────────────────────────┐         WireGuard (embedded,         ┌──────────────────────────┐
│  Android app (Kotlin /  │         per-app split tunnel)        │  Flask backend (existing) │
│  Jetpack Compose)       │  ─────────────────────────────────▶ │  on home host or Pi       │
│                         │         JSON over HTTP (LAN IP)      │                           │
│  • Picks (home, edit)   │                                      │  • SQLite games.db        │
│  • Library browse/search│                                      │  • IGDB / decider / scrape│
│  • Game detail          │                                      │  • NEW: barcode_cache     │
│  • Add (search/barcode) │                                      │  • NEW: /api/barcode/*    │
│  • Glance widget        │                                      │                           │
└─────────────────────────┘                                      └──────────────────────────┘
```

The app is a **thin client**. The Flask backend remains the single source of truth — it
owns the data and all heavy logic. The app holds **no local game database**; it fetches
and writes state through the JSON API. A working VPN tunnel is required for the app to
function (the accepted access model).

The backend stays on whichever home host the owner chooses (current desktop, or a
Raspberry Pi 5 booted from SSD). That choice is independent of this app and only affects
the backend base URL configured in Settings.

---

## 3. Backend Additions (Python repo)

Small, additive changes in the existing Flask codebase. Follows project conventions:
`uv`, `ruff check`, `python -m pytest`, named constants, typed signatures, tests against a
temp DB (never the live `games.db`).

### 3.1 `barcode_cache` table (migration in `migrate_db()`)
| column        | type    | notes                                            |
|---------------|---------|--------------------------------------------------|
| `upc`         | TEXT PK | scanned UPC/EAN digits                           |
| `igdb_id`     | INTEGER | resolved IGDB id (nullable)                      |
| `title`       | TEXT    | resolved/confirmed title                         |
| `platform`    | TEXT    | platform short_name if known (nullable)          |
| `game_id`     | INTEGER | local games.id once added/owned (nullable)       |
| `confirmed_at`| TEXT    | ISO timestamp when a human confirmed the mapping |

This is the **self-growing, free, games-specific UPC database**: every confirmed scan
writes a row, so repeat scans of that barcode are instant, free, and human-accurate —
no rate limit, no fuzzy parsing.

### 3.2 `GET /api/barcode/resolve?upc=<digits>`
Resolution chain, in order:
1. **Local cache** — `barcode_cache` hit → return immediately (source: `cache`).
2. **Free UPC API** — UPCitemdb trial endpoint
   (`https://api.upcitemdb.com/prod/trial/lookup?upc=`), no API key, ~100 lookups/day.
   Take the product title, run it through the existing IGDB match/search to get
   candidate game(s) (source: `upc_api`).
3. **Miss** — return an empty candidate list with a flag telling the app to fall back to
   prefilled text search (source: `none`).

Response also includes an **ownership check**: for each candidate, whether a matching
game already exists in `games` (reuses the same normalized-title dedup logic as
`POST /api/games`). This powers the "do I already own this?" answer.

```json
{
  "upc": "711719541028",
  "source": "upc_api",
  "candidates": [
    {"igdb_id": 119171, "title": "Marvel's Spider-Man 2", "platform": "ps5",
     "cover_url": "https://…", "owned_game_id": 42}
  ]
}
```

Network/parse failures are caught and degrade to `source: "none"` (never 500 the scan
flow). Uses `requests` with an explicit timeout; logs via `logging`.

### 3.3 Persisting confirmations
A confirmed scan must write the `upc → game` mapping. Implemented by adding an optional
`upc` field to the existing **`POST /api/games`** body: when present, after the game is
created (or matched as already-owned), upsert a `barcode_cache` row linking `upc` →
`game_id`/`igdb_id`/`title`/`platform` with `confirmed_at`. No separate confirm endpoint
needed. Adding via barcode also sets `physical: true` (existing behavior → 'Physical' tag).

### 3.4 Reused endpoints (no change)
- `GET /api/games` — library list (covers, status, platforms, tags).
- `GET /api/games/<id>` — game detail.
- `GET /api/igdb/search?q=` — text search-to-add (name, slug, cover_url, platforms).
- `POST /api/games` — add game (title, cover_url, platforms[], physical, +new upc).
- `GET /api/slots` — full slate state (slot defs + current games + candidates).
- `POST /api/slots/<id>/pin` — assign any game to a slot (game_id + goal).
- `POST /api/slots/<id>/outcome` — beat / complete / dropped / swap.
- `PATCH /api/slots/<id>/goal` — edit goal text.
- `POST /api/slots/reorder` — reorder slots.

---

## 4. Android App

**Stack:** Kotlin, Jetpack Compose (UI), Retrofit + OkHttp (networking),
Coil (image loading), CameraX + ML Kit barcode scanning, Glance (home-screen widget),
`com.wireguard.android:tunnel` (embedded VPN). Min SDK 26+, JDK 17 (present).

### 4.1 Screens & navigation
Bottom navigation with **Picks as the start destination**.

- **Picks (home / launch screen)**
  - Top: swipeable **carousel ("merry-go-round")** of current slot games (cover + label + goal).
  - Below: the full slot list. **Editable, no chat:**
    - Assign any game to a slot (opens a library search picker → `pin`).
    - Apply outcome: Beat / 100% (complete) / Dropped / Swap → `outcome`.
    - Edit the slot's goal text → `goal`.
    - Reorder slots → `reorder`.
- **Library** — cover grid, search-as-you-type (filters the `/api/games` list client-side
  or via query), filters for platform and status (backlog/playing/beaten/etc.).
- **Game detail** — cover, platforms, status, hours, rating, owned DLC. Read-only in v1
  (status editing is a candidate follow-up, not in scope now).
- **Add** —
  - Text search (IGDB) → tap a result → add.
  - **Barcode scan** (see §4.2).
- **Settings** — backend **base URL** (e.g. `http://192.168.1.x:5000`), VPN status/toggle,
  connection test.

### 4.2 Barcode scan flow
CameraX preview + ML Kit barcode scanning → decoded UPC → `GET /api/barcode/resolve`.
Three outcomes (covering the owner's "both equally" use case — add *and* own-check):
- **Already owned** → show the matching library entry ("You own this — <platform>").
- **Resolved candidate(s)** → present for confirm; on confirm → `POST /api/games`
  with `upc` + `physical: true` (adds + caches the mapping).
- **No match** (`source: none`) → drop into the text-search screen, prefilled if any
  product text was returned, for manual pick. The manual pick still posts with the `upc`
  so the cache learns it.

### 4.3 Connectivity & embedded VPN
- The app bundles the **Firewalla-generated WireGuard config** and runs a **per-app,
  split-tunnel `VpnService`** via the official WireGuard tunnel library — no separate VPN
  app. Only Game Tracker's traffic routes through the tunnel
  (`addAllowedApplication`).
- A **foreground service** owns the tunnel lifecycle; the app brings it up on launch.
- **One-time** Android system VPN-consent dialog on first run (OS-level, unavoidable).
- **"Always-on VPN for Game Tracker"** enabled (one-time toggle in Android settings) so
  the **widget can refresh in the background**. Without always-on, the widget shows the
  last cached picks until the app is next opened — acceptable degraded behavior.
- **No in-app login** in v1: the private VPN is the auth boundary (matches the
  single-user, no-public-exposure decision). A token-based auth is a future addition only
  if the service is ever exposed beyond the VPN.
- WireGuard keys are embedded in the app build (acceptable for a personal app on the
  owner's own device). If Firewalla rotates keys, the config is updated and the app
  rebuilt.

### 4.4 Home-screen widget (Glance)
A **Glance AppWidget** showing current slot games as a glanceable carousel/list (cover +
label). Tapping a game deep-links into the app's Picks screen. **Read-glance only** — no
editing from the wallpaper. Ships inside the app; appears in the launcher's widget picker
after install (no separate download). Background data refresh depends on the always-on
VPN (§4.3).

---

## 5. Repo Layout & Build

- New **`android/`** subfolder inside the Game Tracker repo (kept with the backend; the
  Python `ruff`/`pytest` gates ignore it). Commit to `main` (no feature branches, per
  owner convention).
- Build via the **Gradle wrapper** (`./gradlew installDebug`) — no system Gradle
  dependency. **JDK 17 is installed.** `ANDROID_HOME` will be wired to the owner's
  existing Android SDK and the `adb` path located during setup (the owner has adb/debugging
  ready).
- **Updates = rebuild + `adb install`** (the accepted native-app tradeoff vs. a website's
  instant refresh).

---

## 6. Error Handling

- **Backend:** barcode resolve catches network/parse errors (`requests.Timeout`,
  `requests.RequestException`, `KeyError`/`ValueError` on parse) and degrades to
  `source: none` — the scan flow never 500s. All secret-adjacent/operational output via
  `logging`. UPCitemdb base URL and rate-limit note live in `config.py`.
- **App:** no VPN / unreachable backend → clear "Can't reach Game Tracker — VPN
  connected?" state with a retry, not a crash. Resolve/add failures show a toast/snackbar
  and leave the user on a usable screen. Camera permission denial → explanatory prompt.

---

## 7. Testing

- **Backend (pytest, temp DB):**
  - `barcode_cache` migration creates the table.
  - Resolve: cache hit; UPC-API hit (mocked HTTP) → candidates; miss → `source: none`;
    ownership flag correctness; network failure → graceful `none`.
  - `POST /api/games` with `upc` upserts the cache row and links `game_id`.
- **App:** unit tests for the Retrofit API client and the barcode-resolution view-model
  (mock the API). Camera scanning and the Glance widget are hardware/launcher-dependent →
  validated by manual smoke on the device.
- Run gates: `uv run python -m pytest` and `ruff check` for the Python side.

---

## 8. Phasing (each independently shippable)

1. **Backend** — `barcode_cache` migration + `GET /api/barcode/resolve` + `upc` field on
   `POST /api/games`, with tests.
2. **App core** — project scaffold, Settings (base URL), API client, **Picks home
   (editable)** + carousel, Library browse/search/filter, Game detail. Embedded WireGuard
   VPN + foreground service land here so the app can reach the backend.
3. **Barcode scan-add** — CameraX + ML Kit → resolve chain → confirm/own/fallback.
4. **Home-screen widget** — Glance carousel + deep-link; validate background refresh under
   always-on VPN.

---

## 9. Key Risks / Open Items

- **Embedded VPN complexity** is the biggest new surface (tunnel lib + `VpnService` +
  foreground service + always-on). Isolated to §4.3; validated in Phase 2 before the
  widget depends on it.
- **UPCitemdb coverage/rate limit** (~100/day free, games coverage varies). Mitigated by
  the local cache (repeat scans free) and the manual-search fallback (always works).
- **Widget background refresh** is contingent on always-on VPN; documented degraded
  behavior (stale-until-open) if the owner doesn't enable it.
- **adb/SDK path** not yet on PATH; resolved during Phase 2 setup (JDK 17 already present).
