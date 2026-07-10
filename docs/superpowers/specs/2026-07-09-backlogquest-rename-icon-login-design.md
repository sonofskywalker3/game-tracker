# BacklogQuest Android: full rename, launcher icon, zero-touch login, VPN removal

**Date:** 2026-07-09 · **Status:** design approved by owner (verbally, in session) · **Scope:** `android/` only — no backend changes.

## Why

The companion app still ships as "Game Tracker" with the default Android robot icon, a
manual URL + password Settings screen, and a dead WireGuard VPN stack. The product is now
BacklogQuest, hosted at https://backlogquest.xyz. This is a single-user personal build, so
login friction can be eliminated entirely.

## 1. VPN dead-code removal (do FIRST — before the rename touches every file)

Delete, then verify the build:

- `vpn/` package (`VpnController.kt` and friends)
- `QrScanScreen` + its nav route (existed only to scan WireGuard config QR codes)
- Manifest: the `GoBackend$VpnService` override block, `FOREGROUND_SERVICE*` +
  `POST_NOTIFICATIONS` permissions if only the VPN used them, `usesCleartextTraffic`
  (cloud is HTTPS-only)
- `build.gradle.kts`: `libs.wireguard.tunnel` dependency (and its libs.versions.toml entries)
- `SettingsScreen`'s `onNavigateToQrScan` hook (the whole screen dies in §3 anyway)
- Any VPN-related tests; camera/CameraX + ML Kit stay (barcode scanning is a live feature)

## 2. Full rename → BacklogQuest

- Kotlin package `com.gametracker.companion` → `com.backlogquest.companion`
  (move all source dirs incl. test/androidTest, rewrite package/import lines)
- `build.gradle.kts`: `namespace` and `applicationId` → `com.backlogquest.companion`
  (app ID change = fresh install identity; owner manually uninstalls the old app from the
  Galaxy Tab A9+; nothing was ever installed on the phone under the old ID that matters)
- Class/theme renames: `GameTrackerApi` → `BacklogQuestApi`, `GameTrackerTheme` →
  `BacklogQuestTheme`, `Theme.GameTracker` → `Theme.BacklogQuest` (themes.xml),
  `GameTrackerDark` → `BacklogQuestDark`
- `strings.xml`: `app_name` = `BacklogQuest`
- User-facing strings: every "Game Tracker" (e.g. "Can't reach Game Tracker") → "BacklogQuest"

## 3. Launcher icon (design FINAL — approved v5 "app palette" colorway)

NES-Zelda-style sword plunged point-down through a 2-case backlog stack.

Palette: tile/background `#181A22`; blade `#E6E6EC` with ridge `#B7B9C6`; guard `#8B93FF`
(indigo) with small pointed claws at the guard's outer ends curving up toward the grip;
grip `#3A3F80` with two `#FFB74D` amber stripes; stack bars `#8B93FF` @85% and `#5A61B8` @90%.

Canonical approved artwork (viewBox 0 0 100 100; also saved in
`.superpowers/brainstorm/4012585-1783648405/content/waiting.html` and `icon-sword-v5.html`):

```svg
<rect width="100" height="100" rx="22" fill="#181A22"/>
<rect x="16" y="52" width="68" height="13" rx="5" fill="#8B93FF" opacity="0.85"/>
<rect x="22" y="71" width="56" height="13" rx="5" fill="#5A61B8" opacity="0.9"/>
<rect x="43" y="3" width="14" height="23" rx="4" fill="#3A3F80"/>
<rect x="43" y="9" width="14" height="5" fill="#FFB74D"/>
<rect x="43" y="18" width="14" height="5" fill="#FFB74D"/>
<path d="M20 30 C20 27 23 26 26 26 L74 26 C77 26 80 27 80 30 L80 34 C80 37 77 38 74 38 L26 38 C23 38 20 37 20 34 Z" fill="#8B93FF"/>
<path d="M20 33 C18.5 27.5 18.5 21 22 15 C25.5 18.5 27.5 23 28 29 C25 27.5 22.5 29.5 20 33 Z" fill="#8B93FF"/>
<path d="M80 33 C81.5 27.5 81.5 21 78 15 C74.5 18.5 72.5 23 72 29 C75 27.5 77.5 29.5 80 33 Z" fill="#8B93FF"/>
<rect x="41" y="38" width="18" height="37" fill="#E6E6EC"/>
<path d="M41 75 L41 78 L50 91 L59 78 L59 75 Z" fill="#E6E6EC"/>
<path d="M50 41 L50 83" stroke="#B7B9C6" stroke-width="3" fill="none"/>
```

Implementation: **adaptive icon** (minSdk 26, so no legacy PNGs):

- `res/drawable/ic_launcher_foreground.xml` — sword + stack as a VectorDrawable, scaled
  into the adaptive safe zone (center 66/108dp circle; launchers mask the outer ~18%)
- `res/drawable/ic_launcher_background.xml` (or color resource) — solid `#181A22`
- `res/drawable/ic_launcher_monochrome.xml` — single-color sword+stack for Android 13+ themed icons
- `res/mipmap-anydpi-v26/ic_launcher.xml` + `ic_launcher_round.xml` adaptive-icon XML
- Manifest: `android:icon="@mipmap/ic_launcher"` `android:roundIcon="@mipmap/ic_launcher_round"`
- Save the full-tile SVG as `docs/branding/backlogquest-icon.svg` in the repo — future web
  favicon + brand mark (web currently has NO favicon at all)

## 4. Zero-touch login (single-user build — owner's explicit call)

- **Delete the Settings screen entirely**: URL field/save/test, password sign-in form,
  its ViewModel state for those, the nav destination + any bottom-bar/menu entry
- Base URL: keep hardcoded `DEFAULT_BASE_URL = "https://backlogquest.xyz"` (already the
  default in `SettingsStore.kt`); remove the DataStore URL override plumbing
- Password: baked at build time — `android/local.properties` (gitignored; key ALREADY
  ADDED: `backlogquest.password=…`) → read in `build.gradle.kts` →
  `buildConfigField("String", "APP_PASSWORD", …)` (enable `buildFeatures { buildConfig = true }`)
- Auth flow: OkHttp `Authenticator` (or interceptor): on 401 / missing token →
  `POST /login` JSON `{"password": BuildConfig.APP_PASSWORD}` → store returned bearer
  token (existing DataStore token storage) → retry original request; single-flight so
  concurrent 401s log in once. Wrong baked password ⇒ normal "can't reach" UI error path,
  never a crash loop (cap retry: one login attempt per request)
- Keep the existing bearer-token header plumbing; only its acquisition becomes automatic
- Fail fast at build: if `backlogquest.password` is missing, `assembleDebug` fails with a
  clear message rather than baking an empty string

## 5. Verification

1. `cd android && .\gradlew.bat testDebugUnitTest` — all tests green (currently 116;
   update packages/imports; delete VPN tests; add authenticator tests: 401→login→retry,
   single-flight, bad-password no-loop)
2. `.\gradlew.bat assembleDebug` builds
3. Install on owner's phone (`adb -s R5GL11FYRGE install -r …`), verify: BacklogQuest
   name + sword icon in launcher; app opens → auto-login → real library loads from
   https://backlogquest.xyz with **no login UI ever shown**
4. Owner uninstalls the stale `com.gametracker.companion` from the tablet

## Context / constraints (house rules)

- Work directly on `main`, push when green; no branches/PRs
- Live server + real `games.db` are never touched by tests; this work is `android/` only
- Backend `/login` contract (do not change): JSON POST `{"password"}` → `{"token"}`;
  verified working 2026-07-09 via form-path curl (HTTP 302)
- Owner's password lives in his password manager + `android/local.properties` only —
  never in git, never in memory files
