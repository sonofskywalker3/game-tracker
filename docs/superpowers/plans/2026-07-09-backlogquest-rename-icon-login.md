# BacklogQuest Android: Rename + Icon + Zero-Touch Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Android companion app as "BacklogQuest" — VPN dead code deleted, full package/brand rename, adaptive sword launcher icon, and fully automatic login against https://backlogquest.xyz with no login UI.

**Architecture:** Four sequential refactors of the existing Kotlin/Compose app: (1) delete the WireGuard VPN stack, (2) mechanical package + brand rename, (3) add an adaptive launcher icon (VectorDrawables from the approved SVG), (4) replace the Settings screen with an OkHttp `Authenticator` that exchanges a build-time-baked password for a bearer token on 401. Base URL becomes a hardcoded constant; DataStore keeps only the token.

**Tech Stack:** Kotlin 2.0 / Jetpack Compose / OkHttp 4 + Retrofit 2 / kotlinx.serialization / DataStore / Gradle 8 (AGP 8.7, minSdk 26).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-09-backlogquest-rename-icon-login-design.md` (main @ c796367). All copy/values below come from it verbatim.
- Scope: `android/` + `docs/branding/` only. **No backend changes.** ruff/pytest untouched.
- Work directly on `main`; push when green. No branches/PRs.
- Tests: `cd android` then `.\gradlew.bat testDebugUnitTest` (Windows). Baseline: **116 tests, 17 files**. Build check: `.\gradlew.bat assembleDebug`.
- Never touch the live server or real `games.db` from tests. (The final on-device smoke DOES hit the live site as a normal client — that's the point.)
- Subagents (if used): never run `git stash` or any git working-tree state commands; never touch the live app/DB.
- Secret hygiene: the password lives ONLY in `android/local.properties` (key `backlogquest.password`, ALREADY present, gitignored via `android/.gitignore:3`). Never echo it, never commit it, never write it to any other file.
- Backend `/login` contract (do not change): `POST /login` JSON `{"password": "…"}` → `200 {"token": "…"}`; wrong password → 401.
- App IDs: old `com.gametracker.companion` → new `com.backlogquest.companion`. App label: `BacklogQuest`.
- Owner's phone for install: `adb -s R5GL11FYRGE` (already authorized).
- Palette (spec-final): tile `#181A22`, blade `#E6E6EC`, ridge `#B7B9C6`, guard/stack1 `#8B93FF`, stack2 `#5A61B8`, grip `#3A3F80`, amber stripes `#FFB74D`.

## Test-count ledger

| After task | Expected tests |
|---|---|
| baseline | 116 |
| Task 1 (delete WgConfigParserTest, 5) | 111 |
| Task 5 (delete SettingsViewModelTest 6 + 2 login tests in RepositoryTest) | 103 |
| Task 6 (delete dynamic-host test in RepositoryTest) | 102 |
| Task 7 (add 4 TokenAuthenticator tests) | 106 |

---

### Task 1: VPN dead-code removal

**Files:**
- Delete: `android/app/src/main/java/com/gametracker/companion/vpn/VpnController.kt`
- Delete: `android/app/src/main/java/com/gametracker/companion/vpn/WgConfig.kt`
- Delete: `android/app/src/main/java/com/gametracker/companion/vpn/WgConfigStore.kt`
- Delete: `android/app/src/main/java/com/gametracker/companion/ui/vpn/QrScanScreen.kt`
- Delete: `android/app/src/test/java/com/gametracker/companion/vpn/WgConfigParserTest.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/AppContainer.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt:91-131`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/settings/SettingsScreen.kt:34-38`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/picks/PicksScreen.kt:38`
- Modify: `android/app/src/main/AndroidManifest.xml`
- Modify: `android/app/build.gradle.kts:58`
- Modify: `android/gradle/libs.versions.toml:17,48`

**Interfaces:**
- Consumes: nothing.
- Produces: `AppContainer` without `wgConfigStore`/`vpnController` properties; `SettingsScreen()` with no parameters; nav graph without the `vpn-scan` route.

- [ ] **Step 1: Delete the VPN files**

```powershell
git rm "android/app/src/main/java/com/gametracker/companion/vpn/VpnController.kt" `
       "android/app/src/main/java/com/gametracker/companion/vpn/WgConfig.kt" `
       "android/app/src/main/java/com/gametracker/companion/vpn/WgConfigStore.kt" `
       "android/app/src/main/java/com/gametracker/companion/ui/vpn/QrScanScreen.kt" `
       "android/app/src/test/java/com/gametracker/companion/vpn/WgConfigParserTest.kt"
```

- [ ] **Step 2: Strip VPN from AppContainer.kt**

Remove these lines (leaving the rest untouched):

```kotlin
import com.gametracker.companion.vpn.VpnController
import com.gametracker.companion.vpn.WgConfigStore
```
and
```kotlin
    val wgConfigStore: WgConfigStore = WgConfigStore(appContext)
    val vpnController: VpnController = VpnController(appContext, wgConfigStore)
```

- [ ] **Step 3: Strip the vpn-scan route from Nav.kt**

Replace the `composable("settings")` block (lines 91-96) with:

```kotlin
            composable("settings") {
                com.gametracker.companion.ui.settings.SettingsScreen()
            }
```

Delete the whole `composable("vpn-scan") { … }` block (lines 122-131).

- [ ] **Step 4: Drop the dead params from SettingsScreen.kt**

Replace lines 34-38:

```kotlin
@Composable
fun SettingsScreen() {
```

Also remove the now-unused import `androidx.navigation.NavBackStackEntry` (line 30).

- [ ] **Step 5: Drop the VPN mention from PicksScreen.kt line 38**

```kotlin
                        Text("Can't reach Game Tracker")
```

- [ ] **Step 6: Clean the manifest**

In `android/app/src/main/AndroidManifest.xml`:
- Delete the three permission lines: `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_SPECIAL_USE`, `POST_NOTIFICATIONS` (lines 8-10). Keep `INTERNET`, `ACCESS_NETWORK_STATE`, `CAMERA`.
- Delete `android:usesCleartextTraffic="true"` from the `<application>` tag (line 18).
- Delete the entire commented `GoBackend$VpnService` `<service>` block including the comment above it (lines 40-59).
- The `xmlns:tools` attribute on `<manifest>` is now unused — delete it too.

Resulting manifest:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.CAMERA" />

    <application
        android:name=".App"
        android:allowBackup="false"
        android:label="@string/app_name"
        android:supportsRtl="true"
        android:theme="@style/Theme.GameTracker">

        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <receiver
            android:name=".widget.PicksWidgetReceiver"
            android:exported="false">
            <intent-filter>
                <action android:name="android.appwidget.action.APPWIDGET_UPDATE" />
            </intent-filter>
            <meta-data
                android:name="android.appwidget.provider"
                android:resource="@xml/picks_widget_info" />
        </receiver>
    </application>
</manifest>
```

- [ ] **Step 7: Remove the wireguard dependency**

`android/app/build.gradle.kts`: delete line 58 `implementation(libs.wireguard.tunnel)`.
`android/gradle/libs.versions.toml`: delete line 17 `wireguard = "1.0.20260102"` and line 48 `wireguard-tunnel = { module = "com.wireguard.android:tunnel", version.ref = "wireguard" }`.

- [ ] **Step 8: Verify nothing VPN-ish remains, tests green, APK builds**

```powershell
# Grep source only (build/ dirs have stale copies)
Get-ChildItem android/app/src -Recurse -File | Select-String -Pattern "vpn|wireguard|QrScan" -SimpleMatch:$false
```
Expected: no output.

```powershell
cd android
.\gradlew.bat testDebugUnitTest
.\gradlew.bat assembleDebug
```
Expected: `BUILD SUCCESSFUL`, 111 tests, 0 failures.

- [ ] **Step 9: Commit**

```powershell
git add -A
git commit -m "refactor(android): remove dead WireGuard VPN stack"
```

---

### Task 2: Full rename → BacklogQuest

**Files:**
- Move: `android/app/src/main/java/com/gametracker/` → `android/app/src/main/java/com/backlogquest/` (all sources)
- Move: `android/app/src/test/java/com/gametracker/` → `android/app/src/test/java/com/backlogquest/`
- Rename: `…/data/GameTrackerApi.kt` → `…/data/BacklogQuestApi.kt`
- Modify: every `.kt` under `android/app/src` (package/import/class/string rewrite)
- Modify: `android/app/build.gradle.kts:9,13`
- Modify: `android/app/src/main/res/values/strings.xml`
- Modify: `android/app/src/main/res/values/themes.xml`
- Modify: `android/app/src/main/AndroidManifest.xml` (theme name)
- Modify: `android/settings.gradle.kts:15`

**Interfaces:**
- Consumes: Task 1's cleaned tree.
- Produces: package `com.backlogquest.companion`; interface `BacklogQuestApi` (same methods as old `GameTrackerApi`); composable `BacklogQuestTheme`; theme `Theme.BacklogQuest`; app label `BacklogQuest`. All later tasks use these names.

- [ ] **Step 1: git mv the source trees and the API file**

```powershell
git mv "android/app/src/main/java/com/gametracker" "android/app/src/main/java/com/backlogquest"
git mv "android/app/src/test/java/com/gametracker" "android/app/src/test/java/com/backlogquest"
git mv "android/app/src/main/java/com/backlogquest/companion/data/GameTrackerApi.kt" "android/app/src/main/java/com/backlogquest/companion/data/BacklogQuestApi.kt"
```

- [ ] **Step 2: Rewrite packages, class names, and user-facing strings in one sweep**

```powershell
Get-ChildItem "android/app/src" -Recurse -File -Include *.kt,*.xml | ForEach-Object {
  $t = Get-Content $_.FullName -Raw
  $new = $t -creplace 'com\.gametracker\.companion','com.backlogquest.companion' `
            -creplace 'GameTrackerApi','BacklogQuestApi' `
            -creplace 'GameTrackerTheme','BacklogQuestTheme' `
            -creplace 'GameTrackerDark','BacklogQuestDark' `
            -creplace 'Theme\.GameTracker','Theme.BacklogQuest' `
            -creplace 'Game Tracker','BacklogQuest'
  if ($new -cne $t) { Set-Content $_.FullName $new -NoNewline }
}
```

This covers: all `package`/`import` lines, `BacklogQuestApi` (interface + `buildApi` + `FakeRepo`), `BacklogQuestTheme`/`BacklogQuestDark` (Theme.kt + MainActivity), `Theme.BacklogQuest` (themes.xml + manifest), `app_name` in strings.xml → `BacklogQuest`, and every "Can't reach Game Tracker" → "Can't reach BacklogQuest" (5 ViewModels + PicksScreen + Theme.kt comment).

- [ ] **Step 3: Update Gradle identity**

`android/app/build.gradle.kts` line 9 and 13:

```kotlin
    namespace = "com.backlogquest.companion"
```
```kotlin
        applicationId = "com.backlogquest.companion"
```

`android/settings.gradle.kts` line 15:

```kotlin
rootProject.name = "BacklogQuest"
```

- [ ] **Step 4: Verify zero leftovers, tests green, APK builds**

```powershell
Get-ChildItem android/app/src -Recurse -File | Select-String -CaseSensitive -Pattern "gametracker|GameTracker|Game Tracker"
```
Expected: no output.

```powershell
cd android
.\gradlew.bat testDebugUnitTest
.\gradlew.bat assembleDebug
```
Expected: `BUILD SUCCESSFUL`, 111 tests, 0 failures.

- [ ] **Step 5: Commit**

```powershell
git add -A
git commit -m "refactor(android): full rename com.gametracker.companion -> com.backlogquest.companion"
```

---

### Task 3: Adaptive launcher icon + branding SVG

NES-Zelda-style sword through a 2-case backlog stack. Artwork coordinates are the spec's 0-100 viewBox, scaled 0.66× and offset +21 into the 108dp adaptive viewport so everything sits inside the 66dp safe-zone circle (verified: farthest point is the wide stack bar corner at r≈24.5 < 33).

**Files:**
- Create: `docs/branding/backlogquest-icon.svg`
- Create: `android/app/src/main/res/values/ic_launcher_background.xml`
- Create: `android/app/src/main/res/drawable/ic_launcher_foreground.xml`
- Create: `android/app/src/main/res/drawable/ic_launcher_monochrome.xml`
- Create: `android/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml`
- Create: `android/app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml`
- Modify: `android/app/src/main/AndroidManifest.xml` (`<application>` icon attrs)

**Interfaces:**
- Consumes: Task 2's manifest (theme `Theme.BacklogQuest`).
- Produces: `@mipmap/ic_launcher` + `@mipmap/ic_launcher_round`; repo brand mark at `docs/branding/backlogquest-icon.svg`.

- [ ] **Step 1: Save the canonical SVG (spec artwork verbatim, wrapped in an svg root)**

`docs/branding/backlogquest-icon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
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
</svg>
```

- [ ] **Step 2: Background color resource**

`android/app/src/main/res/values/ic_launcher_background.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">#181A22</color>
</resources>
```

- [ ] **Step 3: Foreground VectorDrawable**

`android/app/src/main/res/drawable/ic_launcher_foreground.xml` — same draw order as the SVG; rounded rects converted to arc paths; `opacity` → `fillAlpha`; the ridge is a stroked path:

```xml
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">
    <group
        android:scaleX="0.66"
        android:scaleY="0.66"
        android:translateX="21"
        android:translateY="21">
        <path android:fillColor="#8B93FF" android:fillAlpha="0.85"
            android:pathData="M21,52 L79,52 A5,5 0 0 1 84,57 L84,60 A5,5 0 0 1 79,65 L21,65 A5,5 0 0 1 16,60 L16,57 A5,5 0 0 1 21,52 Z"/>
        <path android:fillColor="#5A61B8" android:fillAlpha="0.9"
            android:pathData="M27,71 L73,71 A5,5 0 0 1 78,76 L78,79 A5,5 0 0 1 73,84 L27,84 A5,5 0 0 1 22,79 L22,76 A5,5 0 0 1 27,71 Z"/>
        <path android:fillColor="#3A3F80"
            android:pathData="M47,3 L53,3 A4,4 0 0 1 57,7 L57,22 A4,4 0 0 1 53,26 L47,26 A4,4 0 0 1 43,22 L43,7 A4,4 0 0 1 47,3 Z"/>
        <path android:fillColor="#FFB74D" android:pathData="M43,9 L57,9 L57,14 L43,14 Z"/>
        <path android:fillColor="#FFB74D" android:pathData="M43,18 L57,18 L57,23 L43,23 Z"/>
        <path android:fillColor="#8B93FF"
            android:pathData="M20,30 C20,27 23,26 26,26 L74,26 C77,26 80,27 80,30 L80,34 C80,37 77,38 74,38 L26,38 C23,38 20,37 20,34 Z"/>
        <path android:fillColor="#8B93FF"
            android:pathData="M20,33 C18.5,27.5 18.5,21 22,15 C25.5,18.5 27.5,23 28,29 C25,27.5 22.5,29.5 20,33 Z"/>
        <path android:fillColor="#8B93FF"
            android:pathData="M80,33 C81.5,27.5 81.5,21 78,15 C74.5,18.5 72.5,23 72,29 C75,27.5 77.5,29.5 80,33 Z"/>
        <path android:fillColor="#E6E6EC" android:pathData="M41,38 L59,38 L59,75 L41,75 Z"/>
        <path android:fillColor="#E6E6EC" android:pathData="M41,75 L41,78 L50,91 L59,78 L59,75 Z"/>
        <path android:strokeColor="#B7B9C6" android:strokeWidth="3"
            android:pathData="M50,41 L50,83"/>
    </group>
</vector>
```

- [ ] **Step 4: Monochrome VectorDrawable (Android 13+ themed icons)**

`android/app/src/main/res/drawable/ic_launcher_monochrome.xml` — identical geometry, single white silhouette, no alpha, no ridge stroke:

```xml
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">
    <group
        android:scaleX="0.66"
        android:scaleY="0.66"
        android:translateX="21"
        android:translateY="21">
        <path android:fillColor="#FFFFFF"
            android:pathData="M21,52 L79,52 A5,5 0 0 1 84,57 L84,60 A5,5 0 0 1 79,65 L21,65 A5,5 0 0 1 16,60 L16,57 A5,5 0 0 1 21,52 Z"/>
        <path android:fillColor="#FFFFFF"
            android:pathData="M27,71 L73,71 A5,5 0 0 1 78,76 L78,79 A5,5 0 0 1 73,84 L27,84 A5,5 0 0 1 22,79 L22,76 A5,5 0 0 1 27,71 Z"/>
        <path android:fillColor="#FFFFFF"
            android:pathData="M47,3 L53,3 A4,4 0 0 1 57,7 L57,22 A4,4 0 0 1 53,26 L47,26 A4,4 0 0 1 43,22 L43,7 A4,4 0 0 1 47,3 Z"/>
        <path android:fillColor="#FFFFFF"
            android:pathData="M20,30 C20,27 23,26 26,26 L74,26 C77,26 80,27 80,30 L80,34 C80,37 77,38 74,38 L26,38 C23,38 20,37 20,34 Z"/>
        <path android:fillColor="#FFFFFF"
            android:pathData="M20,33 C18.5,27.5 18.5,21 22,15 C25.5,18.5 27.5,23 28,29 C25,27.5 22.5,29.5 20,33 Z"/>
        <path android:fillColor="#FFFFFF"
            android:pathData="M80,33 C81.5,27.5 81.5,21 78,15 C74.5,18.5 72.5,23 72,29 C75,27.5 77.5,29.5 80,33 Z"/>
        <path android:fillColor="#FFFFFF" android:pathData="M41,38 L59,38 L59,75 L41,75 Z"/>
        <path android:fillColor="#FFFFFF" android:pathData="M41,75 L41,78 L50,91 L59,78 L59,75 Z"/>
    </group>
</vector>
```

- [ ] **Step 5: Adaptive icon XMLs**

`android/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml` AND (identical content) `android/app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@drawable/ic_launcher_foreground"/>
    <monochrome android:drawable="@drawable/ic_launcher_monochrome"/>
</adaptive-icon>
```

- [ ] **Step 6: Point the manifest at the icon**

In `<application>` (which currently has NO icon attribute), add:

```xml
        android:icon="@mipmap/ic_launcher"
        android:roundIcon="@mipmap/ic_launcher_round"
```

- [ ] **Step 7: Verify build**

```powershell
cd android
.\gradlew.bat assembleDebug
```
Expected: `BUILD SUCCESSFUL` (aapt2 validates the vectors and adaptive-icon XML).

- [ ] **Step 8: Commit**

```powershell
git add -A
git commit -m "feat(android): BacklogQuest adaptive sword launcher icon + branding SVG"
```

---

### Task 4: Build-time password (BuildConfig + fail-fast)

**Files:**
- Modify: `android/app/build.gradle.kts`

**Interfaces:**
- Consumes: `android/local.properties` key `backlogquest.password` (present, gitignored).
- Produces: `BuildConfig.APP_PASSWORD` (String) in package `com.backlogquest.companion`, used by Task 7. Configuration fails with a clear message if the key is missing/blank.

- [ ] **Step 1: Read the key and bake it**

At the top of `android/app/build.gradle.kts` (above `plugins {}` won't work for imports in .kts — put the `import` as line 1, before `plugins`):

```kotlin
import java.util.Properties
```

After the `plugins {}` block:

```kotlin
// Zero-touch login: the owner's password is baked at build time from the
// gitignored android/local.properties (key backlogquest.password). Personal
// single-user build — never commit or log it.
val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}
val appPassword: String = localProperties.getProperty("backlogquest.password")?.takeIf { it.isNotBlank() }
    ?: error("backlogquest.password is missing from android/local.properties — add it before building (it is never committed).")
```

Inside `defaultConfig {}` add:

```kotlin
        buildConfigField(
            "String", "APP_PASSWORD",
            "\"${appPassword.replace("\\", "\\\\").replace("\"", "\\\"")}\"",
        )
```

Change `buildFeatures { compose = true }` to:

```kotlin
    buildFeatures {
        compose = true
        buildConfig = true
    }
```

- [ ] **Step 2: Verify the fail-fast path (with guaranteed restore)**

```powershell
Copy-Item android/local.properties android/local.properties.bak
(Get-Content android/local.properties) | Where-Object { $_ -notmatch '^backlogquest\.password=' } | Set-Content android/local.properties
cd android; .\gradlew.bat help; cd ..
Move-Item android/local.properties.bak android/local.properties -Force
```
Expected: `gradlew.bat help` FAILS with `backlogquest.password is missing from android/local.properties`. The `Move-Item` restores the original file — run it unconditionally.

- [ ] **Step 3: Verify the happy path and that the field exists (without printing it)**

```powershell
cd android
.\gradlew.bat assembleDebug
Get-ChildItem app/build/generated -Recurse -Filter BuildConfig.java | ForEach-Object { Select-String -Path $_.FullName -Pattern "APP_PASSWORD" -Quiet }
```
Expected: `BUILD SUCCESSFUL`, then `True`. Do NOT cat the BuildConfig file.

- [ ] **Step 4: Commit (verify no secret staged first)**

```powershell
git add android/app/build.gradle.kts
git diff --cached | Select-String -Pattern "backlogquest.password=" 
git commit -m "feat(android): bake login password into BuildConfig from local.properties, fail-fast if absent"
```
Expected: the Select-String prints nothing (the diff only references the KEY name, never a value).

---

### Task 5: Delete the Settings screen + manual-login plumbing

**Files:**
- Delete: `android/app/src/main/java/com/backlogquest/companion/ui/settings/SettingsScreen.kt`
- Delete: `android/app/src/main/java/com/backlogquest/companion/ui/settings/SettingsViewModel.kt`
- Delete: `android/app/src/test/java/com/backlogquest/companion/ui/SettingsViewModelTest.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/ui/Nav.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/ui/common/AppViewModelFactory.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/data/BacklogQuestApi.kt` (drop `login` endpoint; KEEP `LoginBody`/`LoginResponse` DTOs — Task 7's authenticator uses them)
- Modify: `android/app/src/main/java/com/backlogquest/companion/data/Repository.kt` (drop `login`)
- Modify: `android/app/src/test/java/com/backlogquest/companion/ui/FakeRepo.kt` (drop `login` override)
- Modify: `android/app/src/test/java/com/backlogquest/companion/data/RepositoryTest.kt` (drop the two login tests)

**Interfaces:**
- Consumes: Task 2's names (`BacklogQuestApi`).
- Produces: 3-tab nav (picks/library/add). `Repository` without `login`. `LoginBody(password)` / `LoginResponse(token)` remain in `BacklogQuestApi.kt` for Task 7.

- [ ] **Step 1: Delete the screen, ViewModel, and its test**

```powershell
git rm "android/app/src/main/java/com/backlogquest/companion/ui/settings/SettingsScreen.kt" `
       "android/app/src/main/java/com/backlogquest/companion/ui/settings/SettingsViewModel.kt" `
       "android/app/src/test/java/com/backlogquest/companion/ui/SettingsViewModelTest.kt"
```

- [ ] **Step 2: Remove the Settings tab and route from Nav.kt**

- Delete import `androidx.compose.material.icons.filled.Settings`.
- `TABS` becomes:

```kotlin
private val TABS = listOf(
    Tab("picks", "Picks", Icons.Filled.Home),
    Tab("library", "Library", Icons.AutoMirrored.Filled.List),
    Tab("add", "Add", Icons.Filled.Add),
)
```

- Delete the `composable("settings") { … }` block entirely.

- [ ] **Step 3: Remove SettingsViewModel from AppViewModelFactory.kt**

Delete the import `com.backlogquest.companion.ui.settings.SettingsViewModel` and the first `when` branch:

```kotlin
            modelClass.isAssignableFrom(SettingsViewModel::class.java) ->
                SettingsViewModel(c.settings, c.repository) as T
```

- [ ] **Step 4: Drop the manual login endpoint**

`BacklogQuestApi.kt`: delete the two lines

```kotlin
    @POST("login")
    suspend fun login(@Body body: LoginBody): LoginResponse
```

(keep the `@Serializable data class LoginBody` / `LoginResponse` declarations at the top of the file — add this comment above them:)

```kotlin
// Used by TokenAuthenticator's raw OkHttp /login call (the endpoint is
// deliberately absent from the Retrofit interface — auth is automatic).
```

`Repository.kt`: delete the `login` function (lines 5-8, the doc comment and body).

`FakeRepo.kt`: delete the `override suspend fun login(...)` line pair.

`RepositoryTest.kt`: delete tests `login_posts_password_and_returns_token` and `login_wrong_password_is_failure`.

- [ ] **Step 5: Tests green, APK builds**

```powershell
cd android
.\gradlew.bat testDebugUnitTest
.\gradlew.bat assembleDebug
```
Expected: `BUILD SUCCESSFUL`, 103 tests, 0 failures.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "refactor(android): delete Settings screen and manual login UI"
```

---

### Task 6: Hardcode the base URL (slim SettingsStore + Networking)

**Files:**
- Modify: `android/app/src/main/java/com/backlogquest/companion/data/SettingsStore.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/data/Networking.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/AppContainer.kt`
- Modify: `android/app/src/test/java/com/backlogquest/companion/data/RepositoryTest.kt`

**Interfaces:**
- Consumes: Task 5's tree (no more `setBaseUrl` callers).
- Produces: `SettingsStore` = token-only (`authToken: Flow<String>`, `authTokenBlocking(): String`, `setAuthToken(token: String)`); `buildApi(client: OkHttpClient, json: Json, baseUrl: String = DEFAULT_BASE_URL): BacklogQuestApi`; `dynamicHostInterceptor` GONE. Task 7 relies on these exact signatures.

- [ ] **Step 1: Slim SettingsStore.kt to token-only**

Full new content:

```kotlin
package com.backlogquest.companion.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking

/** Single-user cloud build: the backend address is fixed. */
const val DEFAULT_BASE_URL = "https://backlogquest.xyz"

interface SettingsStore {
    /** Bearer token from a successful /login, attached to every API request.
     *  Empty string means "not signed in (yet)" — TokenAuthenticator fills it. */
    val authToken: Flow<String>
    fun authTokenBlocking(): String
    suspend fun setAuthToken(token: String)
}

private val Context.dataStore by preferencesDataStore(name = "settings")
private val AUTH_TOKEN_KEY = stringPreferencesKey("auth_token")

class DataStoreSettings(private val context: Context) : SettingsStore {
    override val authToken: Flow<String> =
        context.dataStore.data.map { it[AUTH_TOKEN_KEY] ?: "" }

    override fun authTokenBlocking(): String = runBlocking { authToken.first() }

    override suspend fun setAuthToken(token: String) {
        context.dataStore.edit { it[AUTH_TOKEN_KEY] = token.trim() }
    }
}
```

- [ ] **Step 2: Simplify Networking.kt**

Full new content (drops `dynamicHostInterceptor`; Retrofit gets the real base URL):

```kotlin
package com.backlogquest.companion.data

import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory

fun appJson(): Json = Json { ignoreUnknownKeys = true; explicitNulls = false }

/** Attaches the stored bearer token as `Authorization: Bearer <token>` to every
 *  request when signed in. When no token is stored yet the request is sent
 *  unchanged; the server's 401 then triggers TokenAuthenticator's auto-login. */
fun authInterceptor(settings: SettingsStore): Interceptor = Interceptor { chain ->
    val token = settings.authTokenBlocking()
    val req = chain.request()
    val authed = if (token.isNotEmpty())
        req.newBuilder().header("Authorization", "Bearer $token").build()
    else req
    chain.proceed(authed)
}

fun buildApi(client: OkHttpClient, json: Json, baseUrl: String = DEFAULT_BASE_URL): BacklogQuestApi =
    Retrofit.Builder()
        .baseUrl(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/")
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(BacklogQuestApi::class.java)
```

- [ ] **Step 3: Update AppContainer.kt**

Remove the `dynamicHostInterceptor` import and its `.addInterceptor(...)` line:

```kotlin
    private val client: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(authInterceptor(settings))
        .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
        .build()
```

- [ ] **Step 4: Update RepositoryTest.kt**

- `FakeSettings` becomes token-only:

```kotlin
private class FakeSettings(initialToken: String = "") : SettingsStore {
    private val tokenState = MutableStateFlow(initialToken)
    override val authToken: Flow<String> = tokenState
    override fun authTokenBlocking(): String = tokenState.value
    override suspend fun setAuthToken(token: String) { tokenState.value = token }
}
```

- `setUp` builds the API against the mock server directly:

```kotlin
    @Before fun setUp() {
        server = MockWebServer()
        server.start()
        val client = OkHttpClient.Builder().build()
        repo = Repository(buildApi(client, appJson(), server.url("/").toString()))
    }
```

- Delete the test `dynamic_host_uses_current_setting` (the base-URL param now covers routing; nothing dynamic remains).
- Update the two auth-interceptor tests to drop `dynamicHostInterceptor`:

```kotlin
    @Test fun auth_interceptor_adds_bearer_header_when_token_present() = runTest {
        val settings = FakeSettings(initialToken = "tok-xyz")
        val client = OkHttpClient.Builder().addInterceptor(authInterceptor(settings)).build()
        val authedRepo = Repository(buildApi(client, appJson(), server.url("/").toString()))
        server.enqueue(MockResponse().setBody("[]"))
        authedRepo.games()
        assertEquals("Bearer tok-xyz", server.takeRequest().getHeader("Authorization"))
    }

    @Test fun auth_interceptor_omits_header_when_no_token() = runTest {
        val settings = FakeSettings(initialToken = "")
        val client = OkHttpClient.Builder().addInterceptor(authInterceptor(settings)).build()
        val plainRepo = Repository(buildApi(client, appJson(), server.url("/").toString()))
        server.enqueue(MockResponse().setBody("[]"))
        plainRepo.games()
        assertEquals(null, server.takeRequest().getHeader("Authorization"))
    }
```

- [ ] **Step 5: Tests green, APK builds**

```powershell
cd android
.\gradlew.bat testDebugUnitTest
.\gradlew.bat assembleDebug
```
Expected: `BUILD SUCCESSFUL`, 102 tests, 0 failures.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "refactor(android): hardcode https://backlogquest.xyz base URL, token-only SettingsStore"
```

---

### Task 7: TokenAuthenticator (TDD) + wiring

**Files:**
- Create: `android/app/src/main/java/com/backlogquest/companion/data/Auth.kt`
- Create: `android/app/src/test/java/com/backlogquest/companion/data/TokenAuthenticatorTest.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/AppContainer.kt`

**Interfaces:**
- Consumes: `SettingsStore` (token-only, Task 6), `buildApi(client, json, baseUrl)`, `authInterceptor(settings)`, `LoginBody`/`LoginResponse` (in `BacklogQuestApi.kt`), `BuildConfig.APP_PASSWORD` (Task 4), `DEFAULT_BASE_URL`.
- Produces: `class TokenAuthenticator(settings: SettingsStore, password: String, baseUrl: String = DEFAULT_BASE_URL, loginClient: OkHttpClient = OkHttpClient(), json: Json = appJson()) : okhttp3.Authenticator`.

Semantics (spec §4): on 401, single-flight login with the baked password → store token → retry the original request once with the fresh token. OkHttp invokes `Authenticator` below application interceptors, so the retry must (and does) set its own `Authorization` header. Wrong password ⇒ `/login` returns 401 ⇒ authenticator returns `null` ⇒ the original 401 surfaces through the existing `Result`-failure path ("Can't reach BacklogQuest") — exactly one login attempt per request, never a loop.

- [ ] **Step 1: Write the failing tests**

`android/app/src/test/java/com/backlogquest/companion/data/TokenAuthenticatorTest.kt`:

```kotlin
package com.backlogquest.companion.data

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private class FakeSettings(initialToken: String = "") : SettingsStore {
    private val tokenState = MutableStateFlow(initialToken)
    override val authToken: Flow<String> = tokenState
    override fun authTokenBlocking(): String = tokenState.value
    override suspend fun setAuthToken(token: String) { tokenState.value = token }
}

class TokenAuthenticatorTest {
    private lateinit var server: MockWebServer

    @Before fun setUp() { server = MockWebServer(); server.start() }
    @After fun tearDown() { server.shutdown() }

    private fun base() = server.url("/").toString().trimEnd('/')

    private fun repo(settings: SettingsStore): Repository {
        val client = OkHttpClient.Builder()
            .addInterceptor(authInterceptor(settings))
            .authenticator(TokenAuthenticator(settings, "pw-baked", baseUrl = base()))
            .build()
        return Repository(buildApi(client, appJson(), base()))
    }

    @Test fun fresh_401_logs_in_stores_token_and_retries() = runTest {
        val settings = FakeSettings()
        server.enqueue(MockResponse().setResponseCode(401))            // API rejects (no token yet)
        server.enqueue(MockResponse().setBody("""{"token":"t1"}"""))   // /login succeeds
        server.enqueue(MockResponse().setBody("[]"))                   // retried API call
        val result = repo(settings).games()
        assertTrue(result.isSuccess)
        assertEquals("t1", settings.authTokenBlocking())
        assertEquals(null, server.takeRequest().getHeader("Authorization")) // original, tokenless
        val login = server.takeRequest()
        assertEquals("/login", login.path)
        assertTrue(login.body.readUtf8().contains("\"password\":\"pw-baked\""))
        assertEquals("Bearer t1", server.takeRequest().getHeader("Authorization"))
    }

    @Test fun wrong_password_gives_up_without_looping() = runTest {
        val settings = FakeSettings()
        server.enqueue(MockResponse().setResponseCode(401))            // API rejects
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"invalid password"}"""))
        val result = repo(settings).games()
        assertTrue(result.isFailure)
        assertEquals(2, server.requestCount)                           // exactly one login attempt
        assertEquals("", settings.authTokenBlocking())
    }

    @Test fun reuses_token_already_refreshed_by_a_concurrent_request() {
        val settings = FakeSettings(initialToken = "fresh")
        val auth = TokenAuthenticator(settings, "pw-baked", baseUrl = base())
        val retry = auth.authenticate(null, response401(token = "stale"))
        assertEquals("Bearer fresh", retry?.header("Authorization"))
        assertEquals(0, server.requestCount)                           // no /login call
    }

    @Test fun gives_up_after_one_authenticated_retry() {
        val settings = FakeSettings(initialToken = "t1")
        val auth = TokenAuthenticator(settings, "pw-baked", baseUrl = base())
        val first = response401(token = "t1")
        val second = response401(token = "t1", prior = first)
        assertNull(auth.authenticate(null, second))
        assertEquals(0, server.requestCount)
    }

    private fun response401(token: String?, prior: Response? = null): Response {
        val req = Request.Builder().url(server.url("/api/games"))
            .apply { if (token != null) header("Authorization", "Bearer $token") }
            .build()
        return Response.Builder().request(req).protocol(Protocol.HTTP_1_1)
            .code(401).message("Unauthorized")
            .apply { if (prior != null) priorResponse(prior) }
            .build()
    }
}
```

- [ ] **Step 2: Run to verify failure**

```powershell
cd android
.\gradlew.bat testDebugUnitTest
```
Expected: FAILS to compile — `Unresolved reference: TokenAuthenticator`.

- [ ] **Step 3: Implement Auth.kt**

`android/app/src/main/java/com/backlogquest/companion/data/Auth.kt`:

```kotlin
package com.backlogquest.companion.data

import java.io.IOException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.Route

/** Zero-touch auth: on any 401, exchange the build-time password for a bearer
 *  token, persist it, and retry the original request with the new token.
 *  Single-flight: concurrent 401s serialize on this object's lock and reuse
 *  the first refresh instead of logging in again. A wrong baked password makes
 *  /login itself 401 → we return null → the original failure surfaces through
 *  the normal Result/error path (one login attempt per request, never a loop). */
class TokenAuthenticator(
    private val settings: SettingsStore,
    private val password: String,
    private val baseUrl: String = DEFAULT_BASE_URL,
    private val loginClient: OkHttpClient = OkHttpClient(),
    private val json: Json = appJson(),
) : Authenticator {

    override fun authenticate(route: Route?, response: Response): Request? {
        if (responseCount(response) >= MAX_ATTEMPTS_PER_REQUEST) return null
        val failedToken = response.request.header(AUTH_HEADER)?.removePrefix(BEARER_PREFIX)
        val token = synchronized(this) {
            val current = settings.authTokenBlocking()
            if (current.isNotEmpty() && current != failedToken) current
            else login()?.also { fresh -> runBlocking { settings.setAuthToken(fresh) } }
        } ?: return null
        return response.request.newBuilder()
            .header(AUTH_HEADER, "$BEARER_PREFIX$token")
            .build()
    }

    /** POST /login {"password": …} → token, or null on any failure (wrong
     *  password, unreachable, malformed payload). Null = give up cleanly. */
    private fun login(): String? = try {
        val body = json.encodeToString(LoginBody.serializer(), LoginBody(password))
            .toRequestBody(JSON_MEDIA_TYPE.toMediaType())
        loginClient.newCall(Request.Builder().url("$baseUrl/login").post(body).build())
            .execute().use { resp ->
                val text = resp.body?.string()
                if (!resp.isSuccessful || text == null) null
                else json.decodeFromString(LoginResponse.serializer(), text).token
            }
    } catch (e: IOException) {
        null
    } catch (e: SerializationException) {
        null
    }

    private fun responseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) { count++; prior = prior.priorResponse }
        return count
    }

    private companion object {
        /** 1 original try + 1 retry with a freshly acquired token. */
        const val MAX_ATTEMPTS_PER_REQUEST = 2
        const val AUTH_HEADER = "Authorization"
        const val BEARER_PREFIX = "Bearer "
        const val JSON_MEDIA_TYPE = "application/json"
    }
}
```

- [ ] **Step 4: Run tests to verify pass**

```powershell
.\gradlew.bat testDebugUnitTest
```
Expected: `BUILD SUCCESSFUL`, 106 tests, 0 failures.

- [ ] **Step 5: Wire it into AppContainer.kt**

Full new content:

```kotlin
package com.backlogquest.companion

import android.content.Context
import com.backlogquest.companion.data.DataStoreScheduleSnapshotStore
import com.backlogquest.companion.data.DataStoreSettings
import com.backlogquest.companion.data.Repository
import com.backlogquest.companion.data.ScheduleSnapshotStore
import com.backlogquest.companion.data.SettingsStore
import com.backlogquest.companion.data.TokenAuthenticator
import com.backlogquest.companion.data.appJson
import com.backlogquest.companion.data.authInterceptor
import com.backlogquest.companion.data.buildApi
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor

class AppContainer(private val appContext: Context) {
    val settings: SettingsStore = DataStoreSettings(appContext)

    private val client: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(authInterceptor(settings))
        .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
        .authenticator(TokenAuthenticator(settings, BuildConfig.APP_PASSWORD))
        .build()

    val repository: Repository = Repository(buildApi(client, appJson()))
    val scheduleSnapshotStore: ScheduleSnapshotStore = DataStoreScheduleSnapshotStore(appContext)
}
```

- [ ] **Step 6: Full tests + build**

```powershell
.\gradlew.bat testDebugUnitTest
.\gradlew.bat assembleDebug
```
Expected: `BUILD SUCCESSFUL`, 106 tests, 0 failures.

- [ ] **Step 7: Commit**

```powershell
git add -A
git commit -m "feat(android): zero-touch login via OkHttp TokenAuthenticator (401 -> /login -> retry)"
```

---

### Task 8: Final verification + push + on-device smoke

**Files:** none (verification only).

- [ ] **Step 1: Clean full run**

```powershell
cd android
.\gradlew.bat testDebugUnitTest assembleDebug
```
Expected: `BUILD SUCCESSFUL`, 106 tests.

- [ ] **Step 2: Push**

```powershell
git push origin main
```

- [ ] **Step 3: Install on the phone**

```powershell
adb -s R5GL11FYRGE install -r android/app/build/outputs/apk/debug/app-debug.apk
adb -s R5GL11FYRGE shell pm list packages | Select-String "gametracker|backlogquest"
```
Expected: `Success`; package list shows `com.backlogquest.companion`. If the OLD `com.gametracker.companion` shows on the phone, uninstall it (`adb -s R5GL11FYRGE uninstall com.gametracker.companion`) — owner said nothing on the phone under the old ID matters. (The TABLET's old app is the owner's job.)

- [ ] **Step 4: On-device smoke — launcher identity**

```powershell
adb -s R5GL11FYRGE shell am start -n com.backlogquest.companion/.MainActivity
adb -s R5GL11FYRGE exec-out screencap -p > "$env:TEMP\bq-app.png"
```
Inspect the screenshot: app opens straight to Picks/Library chrome — NO login UI, NO Settings tab. Then go to the launcher (`adb shell input keyevent KEYCODE_HOME`, open app drawer manually or screenshot the home screen after placing the icon) and verify the label reads **BacklogQuest** with the sword-through-stack icon.

- [ ] **Step 5: On-device smoke — auto-login against the live cloud**

Fresh install = empty token, so the first API call 401s and must auto-login:

```powershell
adb -s R5GL11FYRGE logcat -c
adb -s R5GL11FYRGE shell am force-stop com.backlogquest.companion
adb -s R5GL11FYRGE shell am start -n com.backlogquest.companion/.MainActivity
Start-Sleep -Seconds 6
adb -s R5GL11FYRGE logcat -d | Select-String "okhttp" | Select-String "backlogquest.xyz|401|login|200"
adb -s R5GL11FYRGE exec-out screencap -p > "$env:TEMP\bq-library.png"
```
Expected in logcat: a 401 on the first `/api/...` call, a `POST https://backlogquest.xyz/login` → 200, then 200s on API calls. Screenshot shows the real library/picks content loaded from the cloud. **Never screenshot or log the password — it appears in no UI.**

- [ ] **Step 6: Report**

Summarize to the owner: tests count, install result, screenshots, and remind him the tablet's old "Game Tracker" app is his to uninstall.
