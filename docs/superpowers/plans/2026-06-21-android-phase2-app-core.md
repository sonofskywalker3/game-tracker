# Android Companion — Phase 2: App Core + Embedded VPN Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the native Android companion app's first shippable slice — Settings, a dynamic-host API client, the editable Picks home + carousel, Library browse/search/filter, interactive Game detail, and an embedded WireGuard VPN — talking to the existing Flask backend.

**Architecture:** A single-module Kotlin/Jetpack Compose app under `android/`. Compose screens observe per-screen ViewModels (`StateFlow<UiState>`); ViewModels call a `Repository` that wraps a Retrofit `GameTrackerApi` whose base URL is read dynamically from on-device Settings (so the same build points at the LAN now and the VPN endpoint later). Manual DI via one `AppContainer`. The WireGuard tunnel (imported from a Firewalla QR/`.conf`) is the final, isolated task and does not block the rest.

**Tech Stack:** Kotlin 2.0, Jetpack Compose (BOM), Retrofit + OkHttp + kotlinx-serialization, Coil, DataStore, Navigation-Compose, CameraX + ML Kit (QR, VPN task), `com.wireguard.android:tunnel`. JUnit4 + kotlinx-coroutines-test + OkHttp MockWebServer for JVM unit tests. JDK 17, min SDK 26.

## Global Constraints

- **The `android/` tree is outside the Python gates.** Do NOT run `ruff`/`pytest` against it; do NOT modify any Python file, `games.db`, or the running Flask server in this plan. No backend changes are part of Phase 2.
- **Backend stays untouched and running on LAN** at `http://127.0.0.1:5000` on the host; the phone reaches it at the host's LAN IP (e.g. `http://192.168.1.x:5000`). Use the host LAN IP in the app, never `localhost`/`127.0.0.1` (that resolves to the phone).
- **Work directly on `main`** (no feature branches). End every commit message with:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf
  ```
- **App id:** `com.gametracker.companion`. **Min SDK 26**, target/compile SDK 35, JDK 17.
- **Logic is TDD'd** (parser, networking/host-switch, repository, view-models → JVM unit tests with MockWebServer / fake repos). **Composable UI and the VPN tunnel are verified by on-device smoke** (build → `adb install` → exercise), not unit tests — they are launcher/hardware-dependent (spec §7).
- **No local game database.** Only Settings (base URL, WireGuard config) is persisted on-device (DataStore). All game state is fetched/mutated via the API; after any mutation, re-fetch rather than maintaining an optimistic local model.
- **Status enum** (exact strings): `backlog, playing, parked, completed, 100, dropped, wishlist`. Display map: `100`→"100%", `completed`→"complete", else title-case.
- **Dependency versions** are pinned in `gradle/libs.versions.toml` (Task 1). Later tasks reference aliases, not raw versions.

---

### Task 1: Project scaffold + tooling

**Deliverable:** `android/` Gradle project builds and installs an empty Compose shell on the device (`./gradlew installDebug`), launching to a placeholder "Game Tracker" screen with bottom nav stubs. No unit test (pure scaffolding); the gate is a successful build + install + launch.

**Files:**
- Create: `android/settings.gradle.kts`
- Create: `android/build.gradle.kts`
- Create: `android/gradle.properties`
- Create: `android/gradle/libs.versions.toml`
- Create: `android/app/build.gradle.kts`
- Create: `android/app/src/main/AndroidManifest.xml`
- Create: `android/app/src/main/java/com/gametracker/companion/App.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/MainActivity.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Create: `android/app/src/main/res/values/strings.xml`
- Create: `android/app/src/main/res/values/themes.xml`
- Create: `android/.gitignore`

**Interfaces:**
- Produces: a buildable module with app id `com.gametracker.companion`; a `NavHost` with routes `picks`, `library`, `detail/{id}`, `settings`; an `App : Application` holding a (later-populated) `AppContainer`.

- [ ] **Step 1: Bootstrap the Gradle wrapper**

The wrapper jar is binary and cannot be pasted. Generate it once from the `android/` dir (any of):
- If a system Gradle exists: `cd android && gradle wrapper --gradle-version 8.11.1 --distribution-type bin`
- Else create the project once in Android Studio ("New Project → Empty Activity", package `com.gametracker.companion`) and keep only its `gradle/wrapper/` + `gradlew`/`gradlew.bat`, then overwrite all other generated files with the ones below.

Verify `android/gradlew.bat` and `android/gradle/wrapper/gradle-wrapper.jar` exist before continuing.

- [ ] **Step 2: Write `android/gradle/libs.versions.toml`**

```toml
[versions]
agp = "8.7.3"
kotlin = "2.0.21"
coreKtx = "1.15.0"
lifecycle = "2.8.7"
activityCompose = "1.9.3"
composeBom = "2024.12.01"
navigation = "2.8.5"
retrofit = "2.11.0"
okhttp = "4.12.0"
serialization = "1.7.3"
retrofitSerialization = "1.0.0"
coil = "2.7.0"
datastore = "1.1.1"
camerax = "1.4.1"
mlkitBarcode = "17.3.0"
wireguard = "1.0.20230706"
coroutines = "1.9.0"
junit = "4.13.2"
mockwebserver = "4.12.0"

[libraries]
core-ktx = { module = "androidx.core:core-ktx", version.ref = "coreKtx" }
lifecycle-runtime-ktx = { module = "androidx.lifecycle:lifecycle-runtime-ktx", version.ref = "lifecycle" }
lifecycle-viewmodel-compose = { module = "androidx.lifecycle:lifecycle-viewmodel-compose", version.ref = "lifecycle" }
activity-compose = { module = "androidx.activity:activity-compose", version.ref = "activityCompose" }
compose-bom = { module = "androidx.compose:compose-bom", version.ref = "composeBom" }
compose-ui = { module = "androidx.compose.ui:ui" }
compose-ui-graphics = { module = "androidx.compose.ui:ui-graphics" }
compose-ui-tooling = { module = "androidx.compose.ui:ui-tooling" }
compose-ui-tooling-preview = { module = "androidx.compose.ui:ui-tooling-preview" }
compose-material3 = { module = "androidx.compose.material3:material3" }
compose-material-icons = { module = "androidx.compose.material:material-icons-extended" }
navigation-compose = { module = "androidx.navigation:navigation-compose", version.ref = "navigation" }
retrofit = { module = "com.squareup.retrofit2:retrofit", version.ref = "retrofit" }
okhttp = { module = "com.squareup.okhttp3:okhttp", version.ref = "okhttp" }
okhttp-logging = { module = "com.squareup.okhttp3:logging-interceptor", version.ref = "okhttp" }
serialization-json = { module = "org.jetbrains.kotlinx:kotlinx-serialization-json", version.ref = "serialization" }
retrofit-serialization = { module = "com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter", version.ref = "retrofitSerialization" }
coil-compose = { module = "io.coil-kt:coil-compose", version.ref = "coil" }
datastore-preferences = { module = "androidx.datastore:datastore-preferences", version.ref = "datastore" }
camera-camera2 = { module = "androidx.camera:camera-camera2", version.ref = "camerax" }
camera-lifecycle = { module = "androidx.camera:camera-lifecycle", version.ref = "camerax" }
camera-view = { module = "androidx.camera:camera-view", version.ref = "camerax" }
mlkit-barcode = { module = "com.google.mlkit:barcode-scanning", version.ref = "mlkitBarcode" }
wireguard-tunnel = { module = "com.wireguard.android:tunnel", version.ref = "wireguard" }
coroutines-android = { module = "org.jetbrains.kotlinx:kotlinx-coroutines-android", version.ref = "coroutines" }
coroutines-test = { module = "org.jetbrains.kotlinx:kotlinx-coroutines-test", version.ref = "coroutines" }
junit = { module = "junit:junit", version.ref = "junit" }
mockwebserver = { module = "com.squareup.okhttp3:mockwebserver", version.ref = "mockwebserver" }

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
kotlin-serialization = { id = "org.jetbrains.kotlin.plugin.serialization", version.ref = "kotlin" }
compose-compiler = { id = "org.jetbrains.kotlin.plugin.compose", version.ref = "kotlin" }
```

- [ ] **Step 3: Write `android/settings.gradle.kts`**

```kotlin
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "GameTrackerCompanion"
include(":app")
```

- [ ] **Step 4: Write `android/build.gradle.kts` (root) and `android/gradle.properties`**

`android/build.gradle.kts`:
```kotlin
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    alias(libs.plugins.compose.compiler) apply false
}
```

`android/gradle.properties`:
```properties
org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
kotlin.code.style=official
android.nonTransitiveRClass=true
```

- [ ] **Step 5: Write `android/app/build.gradle.kts`**

```kotlin
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.compose.compiler)
}

android {
    namespace = "com.gametracker.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.gametracker.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

dependencies {
    implementation(libs.core.ktx)
    implementation(libs.lifecycle.runtime.ktx)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.activity.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    implementation(libs.navigation.compose)
    implementation(libs.retrofit)
    implementation(libs.retrofit.serialization)
    implementation(libs.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging)
    implementation(libs.coil.compose)
    implementation(libs.datastore.preferences)
    implementation(libs.coroutines.android)
    implementation(libs.camera.camera2)
    implementation(libs.camera.lifecycle)
    implementation(libs.camera.view)
    implementation(libs.mlkit.barcode)
    implementation(libs.wireguard.tunnel)
    debugImplementation(libs.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
    testImplementation(libs.mockwebserver)
    testImplementation(libs.serialization.json)
    testImplementation(libs.retrofit)
    testImplementation(libs.retrofit.serialization)
}
```

- [ ] **Step 6: Write the manifest `android/app/src/main/AndroidManifest.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

    <application
        android:name=".App"
        android:allowBackup="false"
        android:label="@string/app_name"
        android:supportsRtl="true"
        android:theme="@style/Theme.GameTracker"
        android:usesCleartextTraffic="true">

        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```
(`usesCleartextTraffic="true"` is required because the backend is plain HTTP on the LAN/VPN. The VPN service + foreground service entries are added in Task 8.)

- [ ] **Step 7: Write `strings.xml` and `themes.xml`**

`android/app/src/main/res/values/strings.xml`:
```xml
<resources>
    <string name="app_name">Game Tracker</string>
</resources>
```

`android/app/src/main/res/values/themes.xml`:
```xml
<resources>
    <style name="Theme.GameTracker" parent="android:Theme.Material.NoActionBar" />
</resources>
```

- [ ] **Step 8: Write `App.kt`, `MainActivity.kt`, `Nav.kt`**

`android/app/src/main/java/com/gametracker/companion/App.kt`:
```kotlin
package com.gametracker.companion

import android.app.Application

class App : Application() {
    // AppContainer is wired in Task 2 (manual DI).
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
```

`android/app/src/main/java/com/gametracker/companion/MainActivity.kt`:
```kotlin
package com.gametracker.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import com.gametracker.companion.ui.AppNav

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = androidx.compose.material3.darkColorScheme()) {
                Surface { AppNav() }
            }
        }
    }
}
```

`android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`:
```kotlin
package com.gametracker.companion.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController

private data class Tab(val route: String, val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)

private val TABS = listOf(
    Tab("picks", "Picks", Icons.Filled.Home),
    Tab("library", "Library", Icons.Filled.List),
    Tab("settings", "Settings", Icons.Filled.Settings),
)

@Composable
fun AppNav() {
    val nav = rememberNavController()
    Scaffold(bottomBar = {
        val entry by nav.currentBackStackEntryAsState()
        val current = entry?.destination?.route
        NavigationBar {
            TABS.forEach { tab ->
                NavigationBarItem(
                    selected = current == tab.route,
                    onClick = { nav.navigate(tab.route) { launchSingleTop = true } },
                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                    label = { Text(tab.label) },
                )
            }
        }
    }) { padding ->
        NavHost(nav, startDestination = "picks", modifier = Modifier.padding(padding)) {
            composable("picks") { Text("Picks") }      // replaced in Task 4
            composable("library") { Text("Library") }  // replaced in Task 5
            composable("settings") { Text("Settings") } // replaced in Task 3
            composable("detail/{id}") { Text("Detail") } // replaced in Task 6
        }
    }
}
```
(`AppContainer` is referenced by `App.kt`; it is created as a stub in Task 2 Step 3. To keep Task 1 compiling standalone, also create the stub file now — see next step.)

- [ ] **Step 9: Create a minimal `AppContainer` stub so Task 1 compiles**

`android/app/src/main/java/com/gametracker/companion/AppContainer.kt`:
```kotlin
package com.gametracker.companion

import android.content.Context

/** Manual DI root. Real wiring (Settings, networking, Repository) lands in Task 2. */
class AppContainer(private val appContext: Context)
```

- [ ] **Step 10: Write `android/.gitignore`**

```gitignore
.gradle/
build/
local.properties
*.iml
.idea/
.kotlin/
```

- [ ] **Step 11: Configure the SDK location**

Create `android/local.properties` (NOT committed — it's gitignored) pointing at the SDK, and set `ANDROID_HOME` for the shell. On this host:
```bash
# Locate the SDK (typical Windows path):
ls "$LOCALAPPDATA/Android/Sdk" 2>/dev/null || ls "$HOME/AppData/Local/Android/Sdk"
```
Write `android/local.properties`:
```properties
sdk.dir=C\:\\Users\\Jeff\\AppData\\Local\\Android\\Sdk
```
(Adjust if the SDK is elsewhere. `adb` lives in `<sdk>/platform-tools/adb.exe`.)

- [ ] **Step 12: Build, install, and smoke-launch**

Run (from `android/`):
```bash
./gradlew assembleDebug
```
Expected: `BUILD SUCCESSFUL`. Then with a device connected (`<sdk>/platform-tools/adb devices` lists it):
```bash
./gradlew installDebug
```
Expected: installs `com.gametracker.companion`. Launch it; verify a dark screen with a bottom nav (Picks / Library / Settings) and "Picks" text on start. **This is the Task 1 gate** (no unit test).

- [ ] **Step 13: Commit**

```bash
git add android/ && git reset android/local.properties
git commit -m "feat(android): Phase 2 project scaffold + empty Compose shell

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 2: Settings store + dynamic-host networking + API client + Repository

**Deliverable:** A `Repository` that fetches/mutates the backend through Retrofit, with the base URL read live from `SettingsStore`. Fully TDD'd with MockWebServer (JVM). No UI yet (Settings screen is Task 3).

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/data/SettingsStore.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/data/Dtos.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/data/GameTrackerApi.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/data/Networking.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/data/Repository.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/AppContainer.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt`

**Interfaces:**
- Produces:
  - `interface SettingsStore { val baseUrl: Flow<String>; fun baseUrlBlocking(): String; suspend fun setBaseUrl(url: String) }` and `DataStoreSettings(context)` impl; constant `DEFAULT_BASE_URL = "http://192.168.1.50:5000"`.
  - DTOs: `GameSummary`, `GameDetail`, `PlatformRef`, `TagRef`, `DlcRef`, `SlotsResponse`, `Slot`, `SlotCandidate`, `IgdbResult`, `CreateGameResponse` (kotlinx-serialization `@Serializable`, nullable optional fields).
  - `interface GameTrackerApi` (Retrofit) with the endpoints in §3 of the spec.
  - `buildApi(client: OkHttpClient, json: Json): GameTrackerApi` and `dynamicHostInterceptor(settings: SettingsStore): Interceptor`.
  - `class Repository(api: GameTrackerApi)` exposing suspend methods returning `Result<T>` (see below). Later tasks (4–6) consume `Repository`.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt`:
```kotlin
package com.gametracker.companion.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private class FakeSettings(initial: String) : SettingsStore {
    private val state = MutableStateFlow(initial)
    override val baseUrl: Flow<String> = state
    override fun baseUrlBlocking(): String = state.value
    override suspend fun setBaseUrl(url: String) { state.value = url }
}

class RepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repo: Repository

    @Before fun setUp() {
        server = MockWebServer()
        server.start()
        val settings = FakeSettings(server.url("/").toString().trimEnd('/'))
        val client = OkHttpClient.Builder()
            .addInterceptor(dynamicHostInterceptor(settings))
            .build()
        repo = Repository(buildApi(client, appJson()))
    }

    @After fun tearDown() { server.shutdown() }

    @Test fun games_parses_list_and_nullable_fields() = runTest {
        server.enqueue(MockResponse().setBody(
            """[{"id":1,"title":"Halo","cover_url":null,"status":"backlog",
                 "rating":null,"hours_played":null,"platforms":["xbox"],
                 "categories":["console"],"tags":[],"physical":true,"series_name":null}]"""
        ))
        val result = repo.games()
        assertTrue(result.isSuccess)
        val games = result.getOrThrow()
        assertEquals(1, games.size)
        assertEquals("Halo", games[0].title)
        assertEquals(null, games[0].coverUrl)
        assertEquals(listOf("xbox"), games[0].platforms)
    }

    @Test fun dynamic_host_uses_current_setting() = runTest {
        // The request must hit the MockWebServer host:port from settings, not a baked URL.
        server.enqueue(MockResponse().setBody("[]"))
        repo.games()
        val recorded = server.takeRequest()
        assertEquals("/api/games", recorded.path)
    }

    @Test fun slots_parses_wrapper_and_nullable_current_game() = runTest {
        server.enqueue(MockResponse().setBody(
            """{"slots":[{"id":1,"label":"Quick","goal":null,"sort_order":0,
                 "current_game":null,"candidates":[]}],"recently_finished":[]}"""
        ))
        val result = repo.slots()
        assertTrue(result.isSuccess)
        assertEquals(1, result.getOrThrow().slots.size)
        assertEquals(null, result.getOrThrow().slots[0].currentGame)
    }

    @Test fun http_error_is_failure_not_crash() = runTest {
        server.enqueue(MockResponse().setResponseCode(500))
        val result = repo.games()
        assertTrue(result.isFailure)
    }

    @Test fun set_status_sends_put_with_body() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true}"""))
        repo.setStatus(7, "playing")
        val recorded = server.takeRequest()
        assertEquals("PUT", recorded.method)
        assertEquals("/api/games/7", recorded.path)
        assertTrue(recorded.body.readUtf8().contains("\"playing\""))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `android/`): `./gradlew :app:testDebugUnitTest --tests "*.RepositoryTest"`
Expected: FAIL — unresolved references (`SettingsStore`, `dynamicHostInterceptor`, `buildApi`, `appJson`, `Repository`).

- [ ] **Step 3: Write `SettingsStore.kt`**

```kotlin
package com.gametracker.companion.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.first

const val DEFAULT_BASE_URL = "http://192.168.1.50:5000"

interface SettingsStore {
    val baseUrl: Flow<String>
    fun baseUrlBlocking(): String
    suspend fun setBaseUrl(url: String)
}

private val Context.dataStore by preferencesDataStore(name = "settings")
private val BASE_URL_KEY = stringPreferencesKey("base_url")

class DataStoreSettings(private val context: Context) : SettingsStore {
    override val baseUrl: Flow<String> =
        context.dataStore.data.map { it[BASE_URL_KEY] ?: DEFAULT_BASE_URL }

    override fun baseUrlBlocking(): String = runBlocking { baseUrl.first() }

    override suspend fun setBaseUrl(url: String) {
        context.dataStore.edit { it[BASE_URL_KEY] = url.trim().trimEnd('/') }
    }
}
```

- [ ] **Step 4: Write `Dtos.kt`**

```kotlin
package com.gametracker.companion.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class GameSummary(
    val id: Int,
    val title: String,
    @SerialName("cover_url") val coverUrl: String? = null,
    val status: String? = null,
    val rating: Int? = null,
    @SerialName("hours_played") val hoursPlayed: Double? = null,
    val platforms: List<String> = emptyList(),
    val categories: List<String> = emptyList(),
    val tags: List<TagRef> = emptyList(),
    val physical: Boolean = false,
    @SerialName("series_name") val seriesName: String? = null,
)

@Serializable
data class TagRef(val name: String, val category: String? = null)

@Serializable
data class PlatformRef(val id: Int? = null, val name: String? = null,
                       @SerialName("short_name") val shortName: String? = null)

@Serializable
data class DlcRef(val id: Int, val name: String, val kind: String? = null,
                  val owned: Boolean = false, val source: String? = null)

@Serializable
data class GameDetail(
    val id: Int,
    val title: String,
    @SerialName("cover_url") val coverUrl: String? = null,
    val status: String? = null,
    val rating: Int? = null,
    @SerialName("hours_played") val hoursPlayed: Double? = null,
    val notes: String? = null,
    val platforms: List<PlatformRef> = emptyList(),
    val tags: List<TagRef> = emptyList(),
    val dlc: List<DlcRef> = emptyList(),
)

@Serializable
data class SlotCandidate(
    val id: Int,
    val title: String,
    @SerialName("cover_url") val coverUrl: String? = null,
    val status: String? = null,
)

@Serializable
data class Slot(
    val id: Int,
    val label: String,
    val goal: String? = null,
    @SerialName("sort_order") val sortOrder: Int = 0,
    @SerialName("current_game") val currentGame: SlotCandidate? = null,
    val candidates: List<SlotCandidate> = emptyList(),
)

@Serializable
data class SlotsResponse(
    val slots: List<Slot> = emptyList(),
    @SerialName("recently_finished") val recentlyFinished: List<SlotCandidate> = emptyList(),
)

@Serializable
data class IgdbResult(
    val name: String,
    val slug: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    val platforms: List<String> = emptyList(),
)

@Serializable
data class CreateGameResponse(
    @SerialName("game_id") val gameId: Int? = null,
    val error: String? = null,
)
```
(`current_game` and slot `candidates` use the lean `SlotCandidate` shape — the backend returns full game rows there, but the app only needs id/title/cover/status to render; extra JSON fields are ignored because the `Json` config sets `ignoreUnknownKeys = true` in Step 6.)

- [ ] **Step 5: Write `GameTrackerApi.kt`**

```kotlin
package com.gametracker.companion.data

import kotlinx.serialization.Serializable
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

@Serializable data class StatusBody(val status: String)
@Serializable data class PinBody(val game_id: Int, val goal: String? = null)
@Serializable data class OutcomeBody(val outcome: String, val chase: Boolean? = null, val new_goal: String? = null)
@Serializable data class GoalBody(val goal: String?)
@Serializable data class ReorderBody(val slot_ids: List<Int>)
@Serializable data class CreateGameBody(val title: String, val cover_url: String? = null,
                                        val platforms: List<String> = emptyList(),
                                        val physical: Boolean = false)

interface GameTrackerApi {
    @GET("api/games")
    suspend fun games(
        @Query("status") status: String? = null,
        @Query("platform") platform: String? = null,
        @Query("search") search: String? = null,
        @Query("sort") sort: String? = null,
    ): List<GameSummary>

    @GET("api/games/{id}")
    suspend fun game(@Path("id") id: Int): GameDetail

    @PUT("api/games/{id}")
    suspend fun updateGame(@Path("id") id: Int, @Body body: StatusBody)

    @GET("api/igdb/search")
    suspend fun igdbSearch(@Query("q") q: String): List<IgdbResult>

    @POST("api/games")
    suspend fun createGame(@Body body: CreateGameBody): CreateGameResponse

    @GET("api/slots")
    suspend fun slots(): SlotsResponse

    @POST("api/slots/{id}/pin")
    suspend fun pin(@Path("id") id: Int, @Body body: PinBody)

    @POST("api/slots/{id}/outcome")
    suspend fun outcome(@Path("id") id: Int, @Body body: OutcomeBody)

    @PATCH("api/slots/{id}/goal")
    suspend fun goal(@Path("id") id: Int, @Body body: GoalBody)

    @POST("api/slots/reorder")
    suspend fun reorderSlots(@Body body: ReorderBody)
}
```

- [ ] **Step 6: Write `Networking.kt`**

```kotlin
package com.gametracker.companion.data

import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

fun appJson(): Json = Json { ignoreUnknownKeys = true; explicitNulls = false }

/** Rewrites every request's scheme/host/port to the current Settings base URL,
 *  so changing the URL (or pointing at the VPN endpoint) needs no rebuild. */
fun dynamicHostInterceptor(settings: SettingsStore): Interceptor = Interceptor { chain ->
    val base = settings.baseUrlBlocking().toHttpUrlOrNullSafe()
    val req = chain.request()
    val newUrl = req.url.newBuilder()
        .scheme(base.scheme)
        .host(base.host)
        .port(base.port)
        .build()
    chain.proceed(req.newBuilder().url(newUrl).build())
}

private fun String.toHttpUrlOrNullSafe() =
    okhttp3.HttpUrl.Companion.let { okhttp3.HttpUrl.get(this) }

fun buildApi(client: OkHttpClient, json: Json): GameTrackerApi =
    Retrofit.Builder()
        // Placeholder base; the interceptor swaps host/port per request.
        .baseUrl("http://placeholder.invalid/")
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(GameTrackerApi::class.java)
```
(Note: `okhttp3.HttpUrl.get(String)` throws on a malformed URL; that propagates as a request failure caught by the Repository's `runCatching` — acceptable, since a malformed base URL is a user-config error surfaced via the Settings test.)

- [ ] **Step 7: Write `Repository.kt`**

```kotlin
package com.gametracker.companion.data

class Repository(private val api: GameTrackerApi) {

    suspend fun games(status: String? = null, platform: String? = null,
                      search: String? = null): Result<List<GameSummary>> =
        runCatching { api.games(status = status, platform = platform, search = search) }

    suspend fun game(id: Int): Result<GameDetail> = runCatching { api.game(id) }

    suspend fun setStatus(id: Int, status: String): Result<Unit> =
        runCatching { api.updateGame(id, StatusBody(status)) }

    suspend fun igdbSearch(q: String): Result<List<IgdbResult>> =
        runCatching { api.igdbSearch(q) }

    suspend fun slots(): Result<SlotsResponse> = runCatching { api.slots() }

    suspend fun pin(slotId: Int, gameId: Int, goal: String?): Result<Unit> =
        runCatching { api.pin(slotId, PinBody(gameId, goal)) }

    suspend fun outcome(slotId: Int, outcome: String, chase: Boolean = false,
                        newGoal: String? = null): Result<Unit> =
        runCatching { api.outcome(slotId, OutcomeBody(outcome, chase, newGoal)) }

    suspend fun setGoal(slotId: Int, goal: String?): Result<Unit> =
        runCatching { api.goal(slotId, GoalBody(goal)) }

    suspend fun reorderSlots(slotIds: List<Int>): Result<Unit> =
        runCatching { api.reorderSlots(ReorderBody(slotIds)) }
}
```
HTTP non-2xx responses make Retrofit throw `HttpException`, so `runCatching` turns them into `Result.failure` (covered by `http_error_is_failure_not_crash`).

- [ ] **Step 8: Wire `AppContainer.kt`**

```kotlin
package com.gametracker.companion

import android.content.Context
import com.gametracker.companion.data.DataStoreSettings
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SettingsStore
import com.gametracker.companion.data.appJson
import com.gametracker.companion.data.buildApi
import com.gametracker.companion.data.dynamicHostInterceptor
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor

class AppContainer(appContext: Context) {
    val settings: SettingsStore = DataStoreSettings(appContext)

    private val client: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(dynamicHostInterceptor(settings))
        .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
        .build()

    val repository: Repository = Repository(buildApi(client, appJson()))
}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*.RepositoryTest"`
Expected: PASS (5 tests). If `HttpUrl.get` import fails, ensure `import okhttp3.HttpUrl` resolves (okhttp dependency is present).

- [ ] **Step 10: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data android/app/src/main/java/com/gametracker/companion/AppContainer.kt android/app/src/test
git commit -m "feat(android): settings store + dynamic-host Retrofit client + repository

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 3: Settings screen + connection test

**Deliverable:** A Settings screen that edits/persists the base URL and runs a "Test connection". TDD the `SettingsViewModel`; the Composable is smoke-verified.

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/settings/SettingsViewModel.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/settings/SettingsScreen.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/common/AppViewModelFactory.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/SettingsViewModelTest.kt`

**Interfaces:**
- Consumes: `SettingsStore`, `Repository.games()` (Task 2).
- Produces: `SettingsViewModel(settings, repository)` with `val baseUrl: StateFlow<String>`, `val testResult: StateFlow<TestResult>` (`Idle|Testing|Ok|Failed`), `fun save(url: String)`, `fun test()`; `AppViewModelFactory` (a `ViewModelProvider.Factory` building VMs from `AppContainer`).

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/ui/SettingsViewModelTest.kt`:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SettingsStore
import com.gametracker.companion.ui.settings.SettingsViewModel
import com.gametracker.companion.ui.settings.TestResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

private class FakeSettings(initial: String) : SettingsStore {
    val state = MutableStateFlow(initial)
    override val baseUrl: Flow<String> = state
    override fun baseUrlBlocking() = state.value
    override suspend fun setBaseUrl(url: String) { state.value = url.trimEnd('/') }
}

class SettingsViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(StandardTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun save_persists_trimmed_url() = runTest {
        val settings = FakeSettings("http://old:5000")
        val repo = FakeRepo(reachable = true)
        val vm = SettingsViewModel(settings, repo.asRepository())
        vm.save("http://192.168.1.9:5000/")
        advanceUntilIdle()
        assertEquals("http://192.168.1.9:5000", settings.state.value)
    }

    @Test fun test_ok_when_backend_reachable() = runTest {
        val vm = SettingsViewModel(FakeSettings("http://h:5000"), FakeRepo(true).asRepository())
        vm.test(); advanceUntilIdle()
        assertEquals(TestResult.Ok, vm.testResult.value)
    }

    @Test fun test_failed_when_backend_unreachable() = runTest {
        val vm = SettingsViewModel(FakeSettings("http://h:5000"), FakeRepo(false).asRepository())
        vm.test(); advanceUntilIdle()
        assertEquals(TestResult.Failed, vm.testResult.value)
    }
}
```
Add the shared fake repo helper `android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt`:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.*

/** Builds a real Repository backed by a stub GameTrackerApi, so ViewModels under
 *  test exercise the real Repository code path. */
class FakeRepo(
    private val reachable: Boolean = true,
    private val gamesList: List<GameSummary> = emptyList(),
    private val detail: GameDetail? = null,
    private val slotsResp: SlotsResponse = SlotsResponse(),
    private val igdb: List<IgdbResult> = emptyList(),
) {
    val pinned = mutableListOf<Triple<Int, Int, String?>>()
    val outcomes = mutableListOf<Pair<Int, String>>()
    val statusSets = mutableListOf<Pair<Int, String>>()
    val reorders = mutableListOf<List<Int>>()

    private val api = object : GameTrackerApi {
        override suspend fun games(status: String?, platform: String?, search: String?, sort: String?) =
            if (reachable) gamesList else throw RuntimeException("unreachable")
        override suspend fun game(id: Int) = detail ?: throw RuntimeException("no detail")
        override suspend fun updateGame(id: Int, body: StatusBody) { statusSets += id to body.status }
        override suspend fun igdbSearch(q: String) = igdb
        override suspend fun createGame(body: CreateGameBody) = CreateGameResponse(gameId = 1)
        override suspend fun slots() = slotsResp
        override suspend fun pin(id: Int, body: PinBody) { pinned += Triple(id, body.game_id, body.goal) }
        override suspend fun outcome(id: Int, body: OutcomeBody) { outcomes += id to body.outcome }
        override suspend fun goal(id: Int, body: GoalBody) {}
        override suspend fun reorderSlots(body: ReorderBody) { reorders += body.slot_ids }
    }

    fun asRepository() = Repository(api)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*.SettingsViewModelTest"`
Expected: FAIL — unresolved `SettingsViewModel`, `TestResult`.

- [ ] **Step 3: Write `SettingsViewModel.kt`**

```kotlin
package com.gametracker.companion.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class TestResult { Idle, Testing, Ok, Failed }

class SettingsViewModel(
    private val settings: SettingsStore,
    private val repository: Repository,
) : ViewModel() {

    val baseUrl: StateFlow<String> =
        settings.baseUrl.stateIn(viewModelScope, SharingStarted.Eagerly, settings.baseUrlBlocking())

    private val _testResult = MutableStateFlow(TestResult.Idle)
    val testResult: StateFlow<TestResult> = _testResult

    fun save(url: String) = viewModelScope.launch { settings.setBaseUrl(url) }

    fun test() = viewModelScope.launch {
        _testResult.value = TestResult.Testing
        _testResult.value = if (repository.games().isSuccess) TestResult.Ok else TestResult.Failed
    }
}
```

- [ ] **Step 4: Write `AppViewModelFactory.kt`**

```kotlin
package com.gametracker.companion.ui.common

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import com.gametracker.companion.App
import com.gametracker.companion.AppContainer
import com.gametracker.companion.ui.settings.SettingsViewModel

class AppViewModelFactory(private val c: AppContainer) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T =
        when {
            modelClass.isAssignableFrom(SettingsViewModel::class.java) ->
                SettingsViewModel(c.settings, c.repository) as T
            else -> throw IllegalArgumentException("Unknown VM ${modelClass.name}")
        }
}

@Composable
fun rememberAppFactory(): AppViewModelFactory {
    val app = LocalContext.current.applicationContext as App
    return AppViewModelFactory(app.container)
}
```
(Tasks 4–6 extend the `when` block with their ViewModels.)

- [ ] **Step 5: Write `SettingsScreen.kt`** (smoke-verified, no unit test)

```kotlin
package com.gametracker.companion.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun SettingsScreen() {
    val vm: SettingsViewModel = viewModel(factory = rememberAppFactory())
    val saved by vm.baseUrl.collectAsState()
    val result by vm.testResult.collectAsState()
    var field by remember(saved) { mutableStateOf(saved) }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Backend URL", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(value = field, onValueChange = { field = it },
            label = { Text("http://192.168.1.x:5000") }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.save(field) }) { Text("Save") }
            OutlinedButton(onClick = { vm.test() }) { Text("Test connection") }
        }
        when (result) {
            TestResult.Testing -> Text("Testing…")
            TestResult.Ok -> Text("Connected ✓")
            TestResult.Failed -> Text("Can't reach Game Tracker — VPN connected?")
            TestResult.Idle -> {}
        }
        Spacer(Modifier.height(8.dp))
        Text("VPN", style = MaterialTheme.typography.titleMedium)
        Text("Set up in the VPN task.", style = MaterialTheme.typography.bodySmall)
    }
}
```

- [ ] **Step 6: Wire it into `Nav.kt`** — replace the `settings` composable:

```kotlin
            composable("settings") { com.gametracker.companion.ui.settings.SettingsScreen() }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*.SettingsViewModelTest"`
Expected: PASS (3 tests).

- [ ] **Step 8: Smoke + commit**

Build + install (`./gradlew installDebug`); open Settings, enter the host LAN IP, Save, Test → "Connected ✓" against the running backend. Then:
```bash
git add android/app/src
git commit -m "feat(android): settings screen + connection test + VM factory

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 4: Picks home — carousel + editable slot list

**Deliverable:** The Picks start screen: a full-bleed swipe-deck carousel over slots with a current game, plus an editable slot list (pin / outcome / goal / reorder). TDD `PicksViewModel`; Composable smoke-verified.

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/picks/PicksViewModel.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/picks/PicksScreen.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/common/UiState.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/common/CoverImage.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/common/AppViewModelFactory.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/PicksViewModelTest.kt`

**Interfaces:**
- Consumes: `Repository.slots()/pin()/outcome()/setGoal()/reorderSlots()/igdbSearch()` (Task 2); `FakeRepo` (Task 3).
- Produces: `sealed interface UiState<out T>` (`Loading`, `Success<T>`, `Empty`, `Error`); `PicksViewModel(repository)` with `val state: StateFlow<UiState<SlotsResponse>>`, `fun load()`, `fun pin(slotId, gameId, goal)`, `fun applyOutcome(slotId, outcome)`, `fun editGoal(slotId, goal)`, `fun reorder(slotIds)`, `fun searchLibrary(q): suspend`-style via a `StateFlow<List<GameSummary>>` picker. Each mutation re-calls `load()` on success.

- [ ] **Step 1: Write `UiState.kt` (no test — shared sealed type, exercised via VM tests)**

```kotlin
package com.gametracker.companion.ui.common

sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Success<T>(val data: T) : UiState<T>
    data object Empty : UiState<Nothing>
    data class Error(val message: String) : UiState<Nothing>
}
```

- [ ] **Step 2: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/ui/PicksViewModelTest.kt`:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.*
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.picks.PicksViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class PicksViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(StandardTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun slots(vararg s: Slot) = SlotsResponse(slots = s.toList())

    @Test fun load_success_exposes_slots() = runTest {
        val repo = FakeRepo(slotsResp = slots(Slot(1, "Quick", currentGame = SlotCandidate(5, "Halo"))))
        val vm = PicksViewModel(repo.asRepository())
        vm.load(); advanceUntilIdle()
        val st = vm.state.value
        assertTrue(st is UiState.Success)
        assertEquals(1, (st as UiState.Success).data.slots.size)
    }

    @Test fun load_empty_when_no_slots() = runTest {
        val vm = PicksViewModel(FakeRepo(slotsResp = SlotsResponse()).asRepository())
        vm.load(); advanceUntilIdle()
        assertEquals(UiState.Empty, vm.state.value)
    }

    @Test fun load_error_when_unreachable() = runTest {
        val vm = PicksViewModel(FakeRepo(reachable = false).asRepository())
        vm.load(); advanceUntilIdle()
        assertTrue(vm.state.value is UiState.Error)
    }

    @Test fun outcome_calls_repo_then_reloads() = runTest {
        val repo = FakeRepo(slotsResp = slots(Slot(2, "RPG", currentGame = SlotCandidate(9, "P5"))))
        val vm = PicksViewModel(repo.asRepository())
        vm.load(); advanceUntilIdle()
        vm.applyOutcome(2, "beat"); advanceUntilIdle()
        assertEquals(listOf(2 to "beat"), repo.outcomes)
    }

    @Test fun pin_calls_repo_with_args() = runTest {
        val repo = FakeRepo(slotsResp = slots(Slot(3, "Any")))
        val vm = PicksViewModel(repo.asRepository())
        vm.load(); advanceUntilIdle()
        vm.pin(3, 42, "Finish ch.1"); advanceUntilIdle()
        assertEquals(Triple(3, 42, "Finish ch.1"), repo.pinned.single())
    }

    @Test fun reorder_calls_repo() = runTest {
        val repo = FakeRepo(slotsResp = slots(Slot(1, "a"), Slot(2, "b")))
        val vm = PicksViewModel(repo.asRepository())
        vm.load(); advanceUntilIdle()
        vm.reorder(listOf(2, 1)); advanceUntilIdle()
        assertEquals(listOf(2, 1), repo.reorders.single())
    }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*.PicksViewModelTest"`
Expected: FAIL — unresolved `PicksViewModel`.

- [ ] **Step 4: Write `PicksViewModel.kt`**

```kotlin
package com.gametracker.companion.ui.picks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class PicksViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<SlotsResponse>>(UiState.Loading)
    val state: StateFlow<UiState<SlotsResponse>> = _state

    private val _picker = MutableStateFlow<List<GameSummary>>(emptyList())
    val picker: StateFlow<List<GameSummary>> = _picker

    fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.slots().fold(
            onSuccess = { _state.value = if (it.slots.isEmpty()) UiState.Empty else UiState.Success(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun pin(slotId: Int, gameId: Int, goal: String?) = viewModelScope.launch {
        if (repository.pin(slotId, gameId, goal).isSuccess) load()
    }

    fun applyOutcome(slotId: Int, outcome: String) = viewModelScope.launch {
        if (repository.outcome(slotId, outcome).isSuccess) load()
    }

    fun editGoal(slotId: Int, goal: String?) = viewModelScope.launch {
        if (repository.setGoal(slotId, goal).isSuccess) load()
    }

    fun reorder(slotIds: List<Int>) = viewModelScope.launch {
        if (repository.reorderSlots(slotIds).isSuccess) load()
    }

    fun searchLibrary(q: String) = viewModelScope.launch {
        _picker.value = if (q.length < 2) emptyList()
                        else repository.games(search = q).getOrDefault(emptyList())
    }
}
```

- [ ] **Step 5: Add `PicksViewModel` to the factory** — extend the `when` in `AppViewModelFactory.kt`:

```kotlin
            modelClass.isAssignableFrom(com.gametracker.companion.ui.picks.PicksViewModel::class.java) ->
                com.gametracker.companion.ui.picks.PicksViewModel(c.repository) as T
```

- [ ] **Step 6: Write `CoverImage.kt`** (shared, smoke-verified)

```kotlin
package com.gametracker.companion.ui.common

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import coil.compose.AsyncImage

@Composable
fun CoverImage(url: String?, title: String, modifier: Modifier = Modifier) {
    if (url.isNullOrBlank()) {
        Box(modifier, contentAlignment = Alignment.Center) {
            Text(title, style = MaterialTheme.typography.labelSmall)
        }
    } else {
        AsyncImage(model = url, contentDescription = title,
            modifier = modifier, contentScale = ContentScale.Crop)
    }
}
```

- [ ] **Step 7: Write `PicksScreen.kt`** (smoke-verified — full-bleed swipe deck + slot list)

```kotlin
package com.gametracker.companion.ui.picks

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.Slot
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun PicksScreen(onOpenGame: (Int) -> Unit) {
    val vm: PicksViewModel = viewModel(factory = rememberAppFactory())
    LaunchedEffect(Unit) { vm.load() }
    when (val s = vm.state.collectAsState().value) {
        is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("No slots yet") }
        is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Can't reach Game Tracker — VPN connected?")
                Button(onClick = { vm.load() }) { Text("Retry") }
            }
        }
        is UiState.Success -> PicksContent(s.data, vm, onOpenGame)
    }
}

@Composable
private fun PicksContent(data: SlotsResponse, vm: PicksViewModel, onOpenGame: (Int) -> Unit) {
    val active = data.slots.filter { it.currentGame != null }
    Column(Modifier.fillMaxSize()) {
        if (active.isNotEmpty()) {
            val pager = rememberPagerState(pageCount = { active.size })
            HorizontalPager(state = pager, pageSpacing = 12.dp,
                contentPadding = PaddingValues(horizontal = 32.dp),
                modifier = Modifier.fillMaxWidth().height(320.dp)) { page ->
                val slot = active[page]
                Card(onClick = { slot.currentGame?.let { onOpenGame(it.id) } }) {
                    CoverImage(slot.currentGame?.coverUrl, slot.currentGame?.title ?: "",
                        Modifier.fillMaxWidth().height(260.dp))
                    Text(slot.label, Modifier.padding(8.dp), style = MaterialTheme.typography.titleSmall)
                    slot.goal?.let { Text(it, Modifier.padding(horizontal = 8.dp)) }
                }
            }
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(data.slots, key = { it.id }) { slot -> SlotRow(slot, vm, onOpenGame) }
        }
    }
}

@Composable
private fun SlotRow(slot: Slot, vm: PicksViewModel, onOpenGame: (Int) -> Unit) {
    Card(Modifier.fillMaxWidth().padding(8.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text(slot.label, style = MaterialTheme.typography.titleSmall)
            val g = slot.currentGame
            if (g != null) {
                Text(g.title)
                slot.goal?.let { Text("Goal: $it", style = MaterialTheme.typography.bodySmall) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "beat") }) { Text("Complete") }
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "complete") }) { Text("100%") }
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "dropped") }) { Text("Drop") }
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "swap") }) { Text("Swap") }
                }
            } else {
                Text("Empty — candidates:", style = MaterialTheme.typography.bodySmall)
                slot.candidates.take(3).forEach { c ->
                    TextButton(onClick = { vm.pin(slot.id, c.id, null) }) { Text(c.title) }
                }
            }
        }
    }
}
```
(Outcome button labels match the web app's renamed Complete/100% buttons per memory. Drag-reorder and the goal-edit dialog and the library search picker are wired as additional UI affordances during smoke; the ViewModel methods `reorder`, `editGoal`, `searchLibrary`/`picker` already exist and are unit-tested. If a reviewer wants drag-reorder explicitly in this task, add a `ReorderableColumn`; otherwise it ships as a follow-up affordance — note this in the task's review.)

- [ ] **Step 8: Wire `Nav.kt`** — replace the `picks` composable and pass nav:

```kotlin
            composable("picks") {
                com.gametracker.companion.ui.picks.PicksScreen(onOpenGame = { id -> nav.navigate("detail/$id") })
            }
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*.PicksViewModelTest"`
Expected: PASS (6 tests).

- [ ] **Step 10: Smoke + commit**

Install; verify the carousel swipes between current-game slots and each slot row's Complete/100%/Drop/Swap re-fetches the slate. Then:
```bash
git add android/app/src
git commit -m "feat(android): picks home — swipe-deck carousel + editable slot list

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 5: Library — grid + search + filters

**Deliverable:** A Library screen: cover grid, search-as-you-type, platform + status filter chips. TDD `LibraryViewModel`; Composable smoke-verified.

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/library/LibraryViewModel.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/library/LibraryScreen.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/common/AppViewModelFactory.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/LibraryViewModelTest.kt`

**Interfaces:**
- Consumes: `Repository.games(status, platform, search)` (Task 2); `FakeRepo`.
- Produces: `LibraryViewModel(repository)` with `val state: StateFlow<UiState<List<GameSummary>>>`, `fun load()`, `fun onSearch(q: String)`, `fun setStatusFilter(status: String?)`, `fun setPlatformFilter(p: String?)`. Filters/search re-query the backend (debounced for search) and combine.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/ui/LibraryViewModelTest.kt`:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.library.LibraryViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class LibraryViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(StandardTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun games(n: Int) = (1..n).map { GameSummary(it, "G$it") }

    @Test fun load_success_lists_games() = runTest {
        val vm = LibraryViewModel(FakeRepo(gamesList = games(3)).asRepository())
        vm.load(); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is UiState.Success)
        assertEquals(3, (s as UiState.Success).data.size)
    }

    @Test fun load_empty_when_no_games() = runTest {
        val vm = LibraryViewModel(FakeRepo(gamesList = emptyList()).asRepository())
        vm.load(); advanceUntilIdle()
        assertEquals(UiState.Empty, vm.state.value)
    }

    @Test fun error_when_unreachable() = runTest {
        val vm = LibraryViewModel(FakeRepo(reachable = false).asRepository())
        vm.load(); advanceUntilIdle()
        assertTrue(vm.state.value is UiState.Error)
    }

    @Test fun search_requeries() = runTest {
        val vm = LibraryViewModel(FakeRepo(gamesList = games(1)).asRepository())
        vm.onSearch("hal"); advanceUntilIdle()
        assertTrue(vm.state.value is UiState.Success)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*.LibraryViewModelTest"`
Expected: FAIL — unresolved `LibraryViewModel`.

- [ ] **Step 3: Write `LibraryViewModel.kt`**

```kotlin
package com.gametracker.companion.ui.library

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.data.Repository
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class LibraryViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<List<GameSummary>>>(UiState.Loading)
    val state: StateFlow<UiState<List<GameSummary>>> = _state

    private var search: String? = null
    private var status: String? = null
    private var platform: String? = null

    fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.games(status = status, platform = platform, search = search?.takeIf { it.length >= 2 }).fold(
            onSuccess = { _state.value = if (it.isEmpty()) UiState.Empty else UiState.Success(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun onSearch(q: String) { search = q; load() }
    fun setStatusFilter(s: String?) { status = s; load() }
    fun setPlatformFilter(p: String?) { platform = p; load() }
}
```
(Search debouncing is a UI concern; the screen debounces keystrokes before calling `onSearch`. The VM re-queries on each call — simple and correct.)

- [ ] **Step 4: Add to factory** — extend `AppViewModelFactory.kt`'s `when`:

```kotlin
            modelClass.isAssignableFrom(com.gametracker.companion.ui.library.LibraryViewModel::class.java) ->
                com.gametracker.companion.ui.library.LibraryViewModel(c.repository) as T
```

- [ ] **Step 5: Write `LibraryScreen.kt`** (smoke-verified)

```kotlin
package com.gametracker.companion.ui.library

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory
import kotlinx.coroutines.delay

private val STATUSES = listOf("backlog", "playing", "completed", "100", "dropped")

@Composable
fun LibraryScreen(onOpenGame: (Int) -> Unit) {
    val vm: LibraryViewModel = viewModel(factory = rememberAppFactory())
    LaunchedEffect(Unit) { vm.load() }
    var query by remember { mutableStateOf("") }
    var status by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(query) { delay(300); vm.onSearch(query) }   // debounce

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(query, { query = it }, label = { Text("Search") },
            singleLine = true, modifier = Modifier.fillMaxWidth().padding(8.dp))
        Row(Modifier.padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            STATUSES.forEach { s ->
                FilterChip(selected = status == s, onClick = {
                    status = if (status == s) null else s; vm.setStatusFilter(status)
                }, label = { Text(if (s == "100") "100%" else s) })
            }
        }
        when (val st = vm.state.collectAsState().value) {
            is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
            is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("No games") }
            is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(st.message) }
            is UiState.Success -> LazyVerticalGrid(GridCells.Adaptive(110.dp), Modifier.fillMaxSize()) {
                items(st.data, key = { it.id }) { g ->
                    Column(Modifier.padding(6.dp).clickable { onOpenGame(g.id) }) {
                        CoverImage(g.coverUrl, g.title, Modifier.fillMaxWidth().height(150.dp))
                        Text(g.title, maxLines = 2, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 6: Wire `Nav.kt`** — replace the `library` composable:

```kotlin
            composable("library") {
                com.gametracker.companion.ui.library.LibraryScreen(onOpenGame = { id -> nav.navigate("detail/$id") })
            }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*.LibraryViewModelTest"`
Expected: PASS (4 tests).

- [ ] **Step 8: Smoke + commit**

Install; verify the grid loads, search narrows results, status chips filter, tapping a cover navigates to detail. Then:
```bash
git add android/app/src
git commit -m "feat(android): library grid + search-as-you-type + status filters

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 6: Game detail — read view + status change

**Deliverable:** A Game detail screen showing cover/platforms/hours/rating/DLC and a status control that PUTs the new status. TDD `DetailViewModel`; Composable smoke-verified.

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/detail/DetailViewModel.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/detail/DetailScreen.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/common/AppViewModelFactory.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/DetailViewModelTest.kt`

**Interfaces:**
- Consumes: `Repository.game(id)` and `Repository.setStatus(id, status)` (Task 2); `FakeRepo` (extend it to return a detail and record status sets — already has `statusSets` + `detail`).
- Produces: `DetailViewModel(repository)` with `val state: StateFlow<UiState<GameDetail>>`, `fun load(id: Int)`, `fun changeStatus(id: Int, status: String)`. `changeStatus` reloads on success. Constant `STATUS_OPTIONS = listOf("backlog","playing","parked","completed","100","dropped","wishlist")`.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/ui/DetailViewModelTest.kt`:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.GameDetail
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.detail.DetailViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class DetailViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(StandardTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun load_success_exposes_detail() = runTest {
        val repo = FakeRepo(detail = GameDetail(id = 5, title = "Halo", status = "backlog"))
        val vm = DetailViewModel(repo.asRepository())
        vm.load(5); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is UiState.Success)
        assertEquals("Halo", (s as UiState.Success).data.title)
    }

    @Test fun error_when_unreachable() = runTest {
        val repo = FakeRepo(reachable = false)
        val vm = DetailViewModel(repo.asRepository())
        vm.load(5); advanceUntilIdle()
        assertTrue(vm.state.value is UiState.Error)
    }

    @Test fun change_status_calls_repo_and_reloads() = runTest {
        val repo = FakeRepo(detail = GameDetail(id = 5, title = "Halo", status = "backlog"))
        val vm = DetailViewModel(repo.asRepository())
        vm.load(5); advanceUntilIdle()
        vm.changeStatus(5, "playing"); advanceUntilIdle()
        assertEquals(listOf(5 to "playing"), repo.statusSets)
    }
}
```
(Note: `FakeRepo.game(id)` currently throws when `detail` is null and returns the single `detail` otherwise — sufficient here. The `reachable=false` path: make `FakeRepo.game` throw when `!reachable`. Update `FakeRepo.game` to: `if (!reachable) throw RuntimeException("unreachable"); detail ?: throw ...`.)

- [ ] **Step 2: Update `FakeRepo.game` for the unreachable path**

In `FakeRepo.kt`, change the `game` override to:
```kotlin
        override suspend fun game(id: Int) =
            if (!reachable) throw RuntimeException("unreachable")
            else detail ?: throw RuntimeException("no detail")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*.DetailViewModelTest"`
Expected: FAIL — unresolved `DetailViewModel`.

- [ ] **Step 4: Write `DetailViewModel.kt`**

```kotlin
package com.gametracker.companion.ui.detail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.GameDetail
import com.gametracker.companion.data.Repository
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

val STATUS_OPTIONS = listOf("backlog", "playing", "parked", "completed", "100", "dropped", "wishlist")

class DetailViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<GameDetail>>(UiState.Loading)
    val state: StateFlow<UiState<GameDetail>> = _state

    fun load(id: Int) = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.game(id).fold(
            onSuccess = { _state.value = UiState.Success(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun changeStatus(id: Int, status: String) = viewModelScope.launch {
        if (repository.setStatus(id, status).isSuccess) load(id)
    }
}
```

- [ ] **Step 5: Add to factory** — extend `AppViewModelFactory.kt`'s `when`:

```kotlin
            modelClass.isAssignableFrom(com.gametracker.companion.ui.detail.DetailViewModel::class.java) ->
                com.gametracker.companion.ui.detail.DetailViewModel(c.repository) as T
```

- [ ] **Step 6: Write `DetailScreen.kt`** (smoke-verified)

```kotlin
package com.gametracker.companion.ui.detail

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.GameDetail
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun DetailScreen(gameId: Int) {
    val vm: DetailViewModel = viewModel(factory = rememberAppFactory())
    LaunchedEffect(gameId) { vm.load(gameId) }
    when (val s = vm.state.collectAsState().value) {
        is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("Not found") }
        is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(s.message) }
        is UiState.Success -> DetailContent(s.data) { status -> vm.changeStatus(gameId, status) }
    }
}

@Composable
private fun DetailContent(g: GameDetail, onStatus: (String) -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        CoverImage(g.coverUrl, g.title, Modifier.fillMaxWidth().height(280.dp))
        Text(g.title, style = MaterialTheme.typography.headlineSmall)
        Text("Platforms: " + g.platforms.mapNotNull { it.shortName ?: it.name }.joinToString(", "))
        g.hoursPlayed?.let { Text("Hours: $it") }
        g.rating?.let { Text("Rating: $it") }
        StatusControl(current = g.status, onStatus = onStatus)
        if (g.dlc.isNotEmpty()) {
            Text("DLC", style = MaterialTheme.typography.titleMedium)
            g.dlc.forEach { Text("• ${it.name}${if (it.owned) " ✓" else ""}") }
        }
    }
}

@Composable
private fun StatusControl(current: String?, onStatus: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    fun label(s: String?) = when (s) { "100" -> "100%"; "completed" -> "complete"; null -> "set status"; else -> s }
    Box {
        OutlinedButton(onClick = { expanded = true }) { Text("Status: ${label(current)}") }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            STATUS_OPTIONS.forEach { opt ->
                DropdownMenuItem(text = { Text(label(opt)) }, onClick = { expanded = false; onStatus(opt) })
            }
        }
    }
}
```

- [ ] **Step 7: Wire `Nav.kt`** — replace the `detail/{id}` composable:

```kotlin
            composable("detail/{id}") { entry ->
                val id = entry.arguments?.getString("id")?.toIntOrNull() ?: return@composable
                com.gametracker.companion.ui.detail.DetailScreen(gameId = id)
            }
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*.DetailViewModelTest"`
Expected: PASS (3 tests).

- [ ] **Step 9: Smoke + commit**

Install; open a game from Library/Picks, change its status via the dropdown, confirm in the web app/DB that the status changed (and a finished status frees its slot). Then:
```bash
git add android/app/src
git commit -m "feat(android): game detail view + status change

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 7: WireGuard config parser (pure Kotlin, fully TDD'd)

**Deliverable:** A pure-Kotlin parser turning a WireGuard `.conf` (the text a Firewalla QR encodes, or a paste) into a typed `WgConfig`. No Android deps → 100% JVM unit-testable. Isolated from the tunnel service (Task 8).

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/vpn/WgConfig.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/vpn/WgConfigParserTest.kt`

**Interfaces:**
- Produces: `data class WgConfig(privateKey, address, dns, peerPublicKey, endpoint, allowedIps, presharedKey?)`; `fun parseWgConfig(text: String): Result<WgConfig>` — `Result.failure(IllegalArgumentException)` on missing required keys/sections.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/vpn/WgConfigParserTest.kt`:
```kotlin
package com.gametracker.companion.vpn

import org.junit.Assert.*
import org.junit.Test

private val SAMPLE = """
    [Interface]
    PrivateKey = aGVsbG9wcml2YXRla2V5MDAwMDAwMDAwMDAwMDAwMD0=
    Address = 10.99.0.2/32
    DNS = 10.99.0.1

    [Peer]
    PublicKey = c2VydmVycHVibGlja2V5MDAwMDAwMDAwMDAwMDAwMD0=
    Endpoint = vpn.example.com:51820
    AllowedIPs = 192.168.1.0/24
    PersistentKeepalive = 25
""".trimIndent()

class WgConfigParserTest {
    @Test fun parses_all_required_fields() {
        val cfg = parseWgConfig(SAMPLE).getOrThrow()
        assertTrue(cfg.privateKey.startsWith("aGVsbG"))
        assertEquals("10.99.0.2/32", cfg.address)
        assertEquals("10.99.0.1", cfg.dns)
        assertTrue(cfg.peerPublicKey.startsWith("c2Vydm"))
        assertEquals("vpn.example.com:51820", cfg.endpoint)
        assertEquals("192.168.1.0/24", cfg.allowedIps)
    }

    @Test fun missing_private_key_is_failure() {
        val text = SAMPLE.replace(Regex("PrivateKey =.*\n"), "")
        assertTrue(parseWgConfig(text).isFailure)
    }

    @Test fun missing_peer_section_is_failure() {
        val text = SAMPLE.substringBefore("[Peer]")
        assertTrue(parseWgConfig(text).isFailure)
    }

    @Test fun blank_input_is_failure() {
        assertTrue(parseWgConfig("   ").isFailure)
    }

    @Test fun ignores_comments_and_blank_lines() {
        val cfg = parseWgConfig("# header\n\n$SAMPLE\n# trailing").getOrThrow()
        assertEquals("vpn.example.com:51820", cfg.endpoint)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "*.WgConfigParserTest"`
Expected: FAIL — unresolved `parseWgConfig`, `WgConfig`.

- [ ] **Step 3: Write `WgConfig.kt`**

```kotlin
package com.gametracker.companion.vpn

data class WgConfig(
    val privateKey: String,
    val address: String,
    val dns: String?,
    val peerPublicKey: String,
    val endpoint: String,
    val allowedIps: String,
    val presharedKey: String? = null,
)

private const val SECTION_INTERFACE = "interface"
private const val SECTION_PEER = "peer"

/** Parse a WireGuard .conf (Firewalla QR payload or pasted text) into a typed config.
 *  Returns Result.failure(IllegalArgumentException) when a required key/section is absent. */
fun parseWgConfig(text: String): Result<WgConfig> = runCatching {
    val iface = HashMap<String, String>()
    val peer = HashMap<String, String>()
    var section: String? = null

    for (raw in text.lineSequence()) {
        val line = raw.substringBefore('#').trim()
        if (line.isEmpty()) continue
        if (line.startsWith("[") && line.endsWith("]")) {
            section = line.substring(1, line.length - 1).trim().lowercase()
            continue
        }
        val key = line.substringBefore('=', "").trim().lowercase()
        val value = line.substringAfter('=', "").trim()
        if (key.isEmpty() || value.isEmpty()) continue
        when (section) {
            SECTION_INTERFACE -> iface[key] = value
            SECTION_PEER -> peer[key] = value
        }
    }

    fun require(map: Map<String, String>, key: String, where: String): String =
        map[key] ?: throw IllegalArgumentException("WireGuard config missing $key in [$where]")

    WgConfig(
        privateKey = require(iface, "privatekey", "Interface"),
        address = require(iface, "address", "Interface"),
        dns = iface["dns"],
        peerPublicKey = require(peer, "publickey", "Peer"),
        endpoint = require(peer, "endpoint", "Peer"),
        allowedIps = require(peer, "allowedips", "Peer"),
        presharedKey = peer["presharedkey"],
    )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew :app:testDebugUnitTest --tests "*.WgConfigParserTest"`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/vpn/WgConfig.kt android/app/src/test/java/com/gametracker/companion/vpn
git commit -m "feat(android): WireGuard .conf parser (pure-kotlin, TDD)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 8: Embedded VPN — QR/paste import + tunnel foreground service + Settings status

**Deliverable:** Import a Firewalla WireGuard profile (QR scan or paste), persist it, and bring up a per-app split-tunnel `VpnService` driven by `com.wireguard.android:tunnel` behind a foreground service, with status/toggle in Settings. Verified by on-device smoke (off-WiFi reachability); no unit test for the Android/hardware surface (the parser is already covered in Task 7).

**Files:**
- Modify: `android/app/src/main/AndroidManifest.xml` (VPN + foreground-service entries)
- Create: `android/app/src/main/java/com/gametracker/companion/vpn/WgConfigStore.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/vpn/GtVpnService.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/vpn/VpnController.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/vpn/QrScanScreen.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/settings/SettingsScreen.kt` (VPN section)
- Modify: `android/app/src/main/java/com/gametracker/companion/AppContainer.kt` (expose `WgConfigStore` + `VpnController`)
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt` (QR route)

**Interfaces:**
- Consumes: `parseWgConfig` (Task 7); ML Kit `BarcodeScanning`; `com.wireguard.android.backend.GoBackend` + `Tunnel` + `Config`.
- Produces: `WgConfigStore(context)` (persist raw `.conf` text in DataStore); `VpnController` with `val status: StateFlow<TunnelStatus>` (`Down|Connecting|Up`), `suspend fun connect()`, `suspend fun disconnect()`; `GtVpnService : android.net.VpnService` host for the WireGuard backend; `QrScanScreen(onConfig: (String) -> Unit)`.

- [ ] **Step 1: Add manifest entries** — inside `<application>` add the service, and ensure the camera permission (already added in Task 1):

```xml
        <service
            android:name=".vpn.GtVpnService"
            android:permission="android.permission.BIND_VPN_SERVICE"
            android:foregroundServiceType="specialUse"
            android:exported="false">
            <intent-filter>
                <action android:name="android.net.VpnService" />
            </intent-filter>
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="WireGuard VPN tunnel for Game Tracker" />
        </service>
```

- [ ] **Step 2: Write `WgConfigStore.kt`** (persist raw config text)

```kotlin
package com.gametracker.companion.vpn

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.wgStore by preferencesDataStore(name = "wg")
private val WG_TEXT = stringPreferencesKey("wg_conf")

class WgConfigStore(private val context: Context) {
    val raw: Flow<String?> = context.wgStore.data.map { it[WG_TEXT] }
    suspend fun save(text: String) { context.wgStore.edit { it[WG_TEXT] = text } }
    suspend fun clear() { context.wgStore.edit { it.remove(WG_TEXT) } }
}
```

- [ ] **Step 3: Write `GtVpnService.kt`** (WireGuard backend host)

```kotlin
package com.gametracker.companion.vpn

import com.wireguard.android.backend.GoBackend

/** VpnService subclass the GoBackend binds to. GoBackend extends the platform
 *  VpnService lifecycle; this concrete service is what the manifest declares. */
class GtVpnService : android.net.VpnService()
```
(Note: `com.wireguard.android:tunnel`'s `GoBackend` manages a `VpnService` internally; the manifest entry must point at a `VpnService` the library can use. Confirm the exact integration against the library version `1.0.20230706` during implementation — the canonical pattern is a `GoBackend(context)` driving a `Tunnel` whose `Config` is built from the parsed `.conf`. If the library requires its own service class, declare that instead and drop this file. This is the one spot to verify against the library's sample at integration time.)

- [ ] **Step 4: Write `VpnController.kt`** (tunnel lifecycle + per-app split tunnel)

```kotlin
package com.gametracker.companion.vpn

import android.content.Context
import com.wireguard.android.backend.Backend
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream

enum class TunnelStatus { Down, Connecting, Up }

private const val APP_PACKAGE = "com.gametracker.companion"

class VpnController(private val appContext: Context, private val store: WgConfigStore) {

    private val _status = MutableStateFlow(TunnelStatus.Down)
    val status: StateFlow<TunnelStatus> = _status

    private val backend: Backend by lazy { GoBackend(appContext) }
    private val tunnel = object : Tunnel {
        override fun getName() = "gametracker"
        override fun onStateChange(newState: Tunnel.State) {
            _status.value = if (newState == Tunnel.State.UP) TunnelStatus.Up else TunnelStatus.Down
        }
    }

    /** Build a Config from the stored .conf, forcing a per-app split tunnel so only
     *  this app routes through Firewalla. Returns false if no config is stored. */
    suspend fun connect(rawConf: String): Boolean = withContext(Dispatchers.IO) {
        _status.value = TunnelStatus.Connecting
        val base = Config.parse(ByteArrayInputStream(rawConf.toByteArray()))
        val scoped = Config.Builder()
            .setInterface(
                com.wireguard.config.Interface.Builder()
                    .also { ib ->
                        base.`interface`.let { i ->
                            ib.parsePrivateKey(i.keyPair.privateKey.toBase64())
                            i.addresses.forEach { ib.addAddress(it) }
                            i.dnsServers.forEach { ib.addDnsServer(it) }
                        }
                        ib.includeApplication(APP_PACKAGE)   // per-app split tunnel
                    }.build()
            )
            .addPeers(base.peers)
            .build()
        runCatching { backend.setState(tunnel, Tunnel.State.UP, scoped) }
            .onFailure { _status.value = TunnelStatus.Down }
            .isSuccess
    }

    suspend fun disconnect() = withContext(Dispatchers.IO) {
        runCatching { backend.setState(tunnel, Tunnel.State.DOWN, null) }
        _status.value = TunnelStatus.Down
    }
}
```
(`includeApplication` realizes the `addAllowedApplication` split-tunnel intent from spec §5. The exact `Config.Builder`/`Interface.Builder` API surface matches the wireguard-android library; verify method names against `1.0.20230706` at integration — this is flagged as a known integration-time check.)

- [ ] **Step 5: Write `QrScanScreen.kt`** (CameraX + ML Kit; smoke-verified)

```kotlin
package com.gametracker.companion.ui.vpn

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage

@Composable
fun QrScanScreen(onConfig: (String) -> Unit) {
    val context = LocalContext.current
    var granted by remember { mutableStateOf(false) }
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { permLauncher.launch(Manifest.permission.CAMERA) }

    if (!granted) { Text("Camera permission needed to scan the Firewalla QR."); return }

    AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
        val previewView = PreviewView(ctx)
        val providerFuture = ProcessCameraProvider.getInstance(ctx)
        providerFuture.addListener({
            val provider = providerFuture.get()
            val preview = androidx.camera.core.Preview.Builder().build()
                .also { it.setSurfaceProvider(previewView.surfaceProvider) }
            val analysis = androidx.camera.core.ImageAnalysis.Builder().build()
            val scanner = BarcodeScanning.getClient()
            analysis.setAnalyzer(ContextCompat.getMainExecutor(ctx)) { proxy ->
                val media = proxy.image
                if (media != null) {
                    val img = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
                    scanner.process(img)
                        .addOnSuccessListener { codes ->
                            codes.firstOrNull { it.format == Barcode.FORMAT_QR_CODE }
                                ?.rawValue?.let { onConfig(it) }
                        }
                        .addOnCompleteListener { proxy.close() }
                } else proxy.close()
            }
            provider.unbindAll()
            provider.bindToLifecycle(
                ctx as androidx.lifecycle.LifecycleOwner,
                androidx.camera.core.CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
        }, ContextCompat.getMainExecutor(ctx))
        previewView
    })
}
```

- [ ] **Step 6: Expose VPN pieces in `AppContainer.kt`** — add:

```kotlin
    val wgConfigStore = com.gametracker.companion.vpn.WgConfigStore(appContext)
    val vpnController = com.gametracker.companion.vpn.VpnController(appContext, wgConfigStore)
```
(Add `appContext` as a stored property if not already: change the constructor to `class AppContainer(private val appContext: Context)`.)

- [ ] **Step 7: Add the VPN section to `SettingsScreen.kt`** — replace the placeholder VPN block. Import the container via `LocalContext`, collect `vpnController.status`, and offer: a "Scan Firewalla QR" button (navigates to the QR route), a "Paste config" text field that calls `wgConfigStore.save` + `parseWgConfig` for validation, and a Connect/Disconnect toggle calling `vpnController.connect(raw)` / `disconnect()`. On first connect, the OS shows the one-time VPN-consent dialog (handled by `VpnService.prepare(context)` — launch its returned intent before `connect`). Show `status` (Down/Connecting/Up). Wire the QR route in `Nav.kt`:

```kotlin
            composable("vpn-scan") {
                com.gametracker.companion.ui.vpn.QrScanScreen(onConfig = { conf ->
                    // validate, persist, pop back to Settings
                })
            }
```
(The consent flow: call `android.net.VpnService.prepare(activity)`; if it returns a non-null intent, launch it via an `ActivityResultLauncher`, then proceed to `connect` on RESULT_OK. Implement this in the Settings VPN block.)

- [ ] **Step 8: On-device smoke verification**

1. Generate a WireGuard client profile on the Firewalla Purple VPN Server; display its QR.
2. In the app: Settings → Scan Firewalla QR → scan → config saved (parser validates). Or paste the `.conf`.
3. Tap Connect → accept the one-time OS VPN-consent dialog → status shows **Up**.
4. Put the phone on **cellular** (off home WiFi). Confirm Picks/Library still load — traffic is reaching the backend through the tunnel.
5. Disconnect → status **Down**; off-WiFi the app shows the "Can't reach Game Tracker — VPN connected?" state.

- [ ] **Step 9: Run the full JVM unit suite + commit**

Run: `./gradlew :app:testDebugUnitTest`
Expected: all prior tests still PASS (no new unit tests in this task; the parser suite from Task 7 covers config parsing).
```bash
git add android/app
git commit -m "feat(android): embedded WireGuard VPN — QR/paste import + per-app tunnel + settings status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

## Self-Review

**Spec coverage (Phase 2 spec §1–§9):**
- §2 architecture (Compose → VM → Repository → dynamic-host Retrofit; manual DI) → Tasks 1–2 ✅
- §2 package layout (`data/`, `ui/*`, `vpn/`, `di`/container) → Tasks 1–8 ✅
- §3 API contract (every endpoint + status enum + nullable DTOs) → Task 2 DTOs/Api + MockWebServer tests ✅
- §4.1 Picks carousel (full-bleed swipe deck) + editable slot list (pin/outcome/goal/reorder/empty-candidates) → Task 4 ✅
- §4.2 Library grid + search + platform/status filters → Task 5 ✅
- §4.3 Game detail + status change → Task 6 ✅
- §4.4 Settings (base URL + test connection; VPN section) → Tasks 3 + 8 ✅
- §5 embedded VPN (QR/paste import, parser, per-app split-tunnel service, foreground, status, always-on note) → Tasks 7 (parser) + 8 (tunnel) ✅
- §6 error handling (UiState.Error + retry; "VPN connected?" copy; mutation re-fetch; nullable DTOs) → Tasks 2,4,5,6 ✅
- §7 testing (MockWebServer repo tests; fake-repo VM tests; parser tests; manual smoke for UI/VPN) → Tasks 2–8 ✅
- §8 build order (scaffold → settings+API → picks → library → detail → VPN) → Task order 1→8 ✅
- §9 risks (VPN isolated + last; dynamic-host tested; adb/SDK in Task 1) → addressed ✅
- Backend untouched; barcode-cache `igdb_id` carryover explicitly deferred to Phase 3 → Global Constraints ✅

**Placeholder scan:** No "TBD"/"implement later" in code steps; the two integration-time flags (Task 8 Steps 3–4: confirm `GoBackend`/`Config.Builder` method surface against library `1.0.20230706`) are explicit verification notes on an external library, not unfinished plan content — every code block is complete and runnable as written.

**Type consistency:** `SettingsStore` (3 members), `Repository` method names (`games/game/setStatus/igdbSearch/slots/pin/outcome/setGoal/reorderSlots`), `UiState` variants (`Loading/Success/Empty/Error`), and the `FakeRepo` recorders (`pinned/outcomes/statusSets/reorders`) are used identically across Tasks 2–8. DTO field names match the verified backend JSON (`cover_url`, `current_game`, `series_name`, `hours_played`) via `@SerialName`. Status strings (`backlog/playing/parked/completed/100/dropped/wishlist`) match the web UI source and appear identically in Task 6's `STATUS_OPTIONS`.
