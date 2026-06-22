# Android Companion — Phase 3: Barcode Scan-Add (+ Text-Search Add) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner add a game from the phone by IGDB text search or barcode scan, behind a new "Add" tab, and close the Phase-1 carryover by populating `barcode_cache.igdb_id`.

**Architecture:** One small backend change (`api_create_game` writes `igdb_id` into the cache). On the app side: extend the data layer with a `resolveBarcode` call + `BarcodeResolveResponse` DTOs and a `Repository.createGame` wrapper; add an "Add" bottom-nav tab (text search → create) and a continuous-auto-detect barcode Scan screen whose `ScanViewModel` drives the master-spec §4.2 three-outcome flow (already-owned / confirm-candidate / no-match→manual). Logic is TDD'd (pytest, MockWebServer, fake-repo view-models); camera + UI are owner on-device smoke.

**Tech Stack:** Python/Flask/SQLite + pytest (backend); Kotlin/Compose, Retrofit + kotlinx-serialization, CameraX + ML Kit (Android). JDK 17, gradle wrapper present.

## Global Constraints

- **Backend (Task 1) uses the Python gates:** `uv run python -m pytest` (plain `uv run pytest` FAILS: ModuleNotFoundError: models) and `ruff check` ONLY — NEVER `ruff format` (hand-aligned). Tests run against the pytest **temp DB**, never the live `games.db` or the running server.
- **Android (Tasks 2–4) are outside the Python gates.** Build from inside `android/` with `./gradlew.bat` (Git Bash on Windows). Unit tests: `./gradlew.bat :app:testDebugUnitTest --tests "<pattern>"`. Compile gate: `./gradlew.bat :app:assembleDebug` → BUILD SUCCESSFUL. Do NOT install to the device (owner does visual/camera smoke). Do NOT change dependency or SDK versions.
- **Work directly on `main`** (no feature branches). End EVERY commit message with:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf
  ```
- **DTO JSON mappings** use `@SerialName` snake_case matching the backend: `product_title`, `igdb_id`, `cover_url`, `owned_game_id`, `game_id`. `appJson()` already sets `ignoreUnknownKeys = true`.
- **Status/`source` strings** are exact: resolve `source` ∈ {`cache`, `upc_api`, `none`}. Physical-add posts `physical = true`.
- **One error pattern per side:** backend raises / best-effort-degrades (resolve never 500s — Phase 1); the app wraps calls in `runCatching` → `Result`, view-models map to `UiState`.

---

### Task 1: Backend — populate `barcode_cache.igdb_id` on `POST /api/games`

**Files:**
- Modify: `app.py` `api_create_game()` — the existing-game (409) query + `cache_put` (lines 218–228) and the created-game `cache_put` (lines 288–291).
- Test: `tests/test_api_barcode.py` (append).

**Interfaces:**
- Consumes: existing `barcode.cache_put(conn, upc, *, igdb_id=None, title=None, platform=None, game_id=None)` and `barcode.cache_get(conn, upc)` (returns dict incl. `igdb_id`).
- Produces: no new symbols; `barcode_cache` rows written by `POST /api/games` now carry `igdb_id` when known.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_barcode.py`:

```python
def test_existing_game_with_upc_caches_igdb_id(client):
    import models
    conn = models.get_db()
    conn.execute(
        "INSERT INTO games (title, normalized_title, igdb_id) VALUES (?, ?, ?)",
        ("Owned RPG", models.normalize_title("Owned RPG"), 555),
    )
    gid = conn.execute("SELECT id FROM games WHERE title = 'Owned RPG'").fetchone()[0]
    conn.commit()
    conn.close()

    resp = client.post("/api/games", json={"title": "Owned RPG", "upc": "upc-existing"})
    assert resp.status_code == 409
    assert resp.get_json()["game_id"] == gid

    conn = models.get_db()
    row = barcode.cache_get(conn, "upc-existing")
    conn.close()
    assert row["game_id"] == gid
    assert row["igdb_id"] == 555


def test_post_game_with_upc_caches_igdb_id_from_enrichment(client, monkeypatch):
    import app
    import igdb_dlc
    import models

    monkeypatch.setattr(app, "get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda client_id, secret: "tok")

    def fake_enrich(conn, game_id, client_id, token):
        conn.execute("UPDATE games SET igdb_id = ? WHERE id = ?", (424242, game_id))

    monkeypatch.setattr(igdb_dlc, "enrich_game", fake_enrich)

    resp = client.post("/api/games", json={"title": "Tunic Scan", "upc": "upc-enrich"})
    assert resp.status_code == 201
    gid = resp.get_json()["game_id"]

    conn = models.get_db()
    row = barcode.cache_get(conn, "upc-enrich")
    conn.close()
    assert row["game_id"] == gid
    assert row["igdb_id"] == 424242
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_api_barcode.py -v -k "igdb_id"`
Expected: FAIL — `row["igdb_id"]` is `None` (the upc is cached today without `igdb_id`).

- [ ] **Step 3: Fetch `igdb_id` on the existing-game (409) path**

In `app.py` `api_create_game()`, change the existing-game lookup (lines 218–221) to also select `igdb_id`, and pass it to `cache_put` (line 225):

```python
    # Check if game already exists
    existing = conn.execute(
        "SELECT id, igdb_id FROM games WHERE normalized_title = ?",
        (normalized,)
    ).fetchone()

    if existing:
        if upc:
            barcode.cache_put(conn, upc, igdb_id=existing['igdb_id'], title=title,
                              game_id=existing['id'])
            conn.commit()
        conn.close()
        return jsonify({'error': 'Game already exists', 'game_id': existing['id']}), 409
```

- [ ] **Step 4: Fetch `igdb_id` on the created-game path**

In `api_create_game()`, replace the created-game cache write (lines 288–291) so it reads the game's `igdb_id` (set by enrichment, if any) and includes it:

```python
    if upc:
        platform_short = platforms[0] if platforms else None
        igdb_row = conn.execute(
            "SELECT igdb_id FROM games WHERE id = ?", (game_id,)
        ).fetchone()
        barcode.cache_put(conn, upc, igdb_id=igdb_row['igdb_id'] if igdb_row else None,
                          title=title, platform=platform_short, game_id=game_id)
        conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: PASS — the two new tests plus all pre-existing `test_api_barcode.py` tests (the existing `test_post_game_with_upc_writes_cache` still passes; with no Twitch creds it caches `igdb_id = None`, which is unchanged behavior).

- [ ] **Step 6: Full suite + lint + commit**

```bash
uv run python -m pytest -q
uv run ruff check app.py tests/test_api_barcode.py
git add app.py tests/test_api_barcode.py
git commit -m "feat(barcode): populate barcode_cache.igdb_id on POST /api/games

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 2: API client — `resolveBarcode` + DTOs + `CreateGameBody.upc` + `Repository.createGame`

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/data/Dtos.kt` (add `BarcodeResolveResponse`, `BarcodeCandidate`)
- Modify: `android/app/src/main/java/com/gametracker/companion/data/GameTrackerApi.kt` (add `upc` to `CreateGameBody`; add `resolveBarcode`)
- Modify: `android/app/src/main/java/com/gametracker/companion/data/Repository.kt` (add `resolveBarcode`, `createGame`)
- Modify: `android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt` (add `resolveResp`/`created` + stub `resolveBarcode`, record `createGame`)
- Test: `android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt` (append)

**Interfaces:**
- Produces:
  - `BarcodeResolveResponse(upc: String, source: String, candidates: List<BarcodeCandidate> = emptyList(), productTitle: String? = null)`
  - `BarcodeCandidate(igdbId: Int? = null, title: String? = null, platform: String? = null, coverUrl: String? = null, ownedGameId: Int? = null)`
  - `GameTrackerApi.resolveBarcode(upc: String): BarcodeResolveResponse`
  - `Repository.resolveBarcode(upc: String): Result<BarcodeResolveResponse>`
  - `Repository.createGame(title: String, coverUrl: String? = null, platforms: List<String> = emptyList(), physical: Boolean = false, upc: String? = null): Result<CreateGameResponse>`
  - `CreateGameBody(... , upc: String? = null)`
  - `FakeRepo` gains ctor params `resolveResp: BarcodeResolveResponse` and recorder `created: MutableList<CreateGameBody>`.
- Consumes (Task 3/4): `Repository.resolveBarcode`, `Repository.createGame`; `FakeRepo.resolveResp`/`created`.

- [ ] **Step 1: Write the failing tests**

Append to `RepositoryTest.kt` (it already has `server`, `repo`, `appJson()` set up in `@Before`):

```kotlin
    @Test fun resolveBarcode_parses_candidates_and_ownership() = runTest {
        server.enqueue(MockResponse().setBody(
            """{"upc":"711","source":"upc_api","candidates":[
                 {"igdb_id":119171,"title":"Spider-Man 2","platform":"ps5",
                  "cover_url":"https://x/c.jpg","owned_game_id":42}]}"""
        ))
        val r = repo.resolveBarcode("711")
        assertTrue(r.isSuccess)
        val body = r.getOrThrow()
        assertEquals("upc_api", body.source)
        assertEquals(119171, body.candidates[0].igdbId)
        assertEquals(42, body.candidates[0].ownedGameId)
    }

    @Test fun resolveBarcode_source_none_empty_candidates() = runTest {
        server.enqueue(MockResponse().setBody("""{"upc":"999","source":"none","candidates":[]}"""))
        val body = repo.resolveBarcode("999").getOrThrow()
        assertEquals("none", body.source)
        assertTrue(body.candidates.isEmpty())
    }

    @Test fun resolveBarcode_http_error_is_failure() = runTest {
        server.enqueue(MockResponse().setResponseCode(500))
        assertTrue(repo.resolveBarcode("1").isFailure)
    }

    @Test fun createGame_posts_upc_and_physical() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true,"game_id":7}"""))
        val r = repo.createGame(title = "Halo", platforms = listOf("xbox"),
                                physical = true, upc = "upc-1")
        assertTrue(r.isSuccess)
        assertEquals(7, r.getOrThrow().gameId)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/api/games", recorded.path)
        val sent = recorded.body.readUtf8()
        assertTrue(sent.contains("\"upc\":\"upc-1\""))
        assertTrue(sent.contains("\"physical\":true"))
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.RepositoryTest"`
Expected: FAIL — unresolved `repo.resolveBarcode` / `repo.createGame` / `BarcodeResolveResponse`.

- [ ] **Step 3: Add the DTOs**

Append to `Dtos.kt`:

```kotlin
@Serializable
data class BarcodeCandidate(
    @SerialName("igdb_id") val igdbId: Int? = null,
    val title: String? = null,
    val platform: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    @SerialName("owned_game_id") val ownedGameId: Int? = null,
)

@Serializable
data class BarcodeResolveResponse(
    val upc: String,
    val source: String,
    val candidates: List<BarcodeCandidate> = emptyList(),
    @SerialName("product_title") val productTitle: String? = null,
)
```

- [ ] **Step 4: Add `upc` to `CreateGameBody` and the `resolveBarcode` endpoint**

In `GameTrackerApi.kt`, change `CreateGameBody` and add the endpoint:

```kotlin
@Serializable data class CreateGameBody(val title: String, val cover_url: String? = null,
                                        val platforms: List<String> = emptyList(),
                                        val physical: Boolean = false,
                                        val upc: String? = null)
```
Add inside the `interface GameTrackerApi` (next to `igdbSearch`):
```kotlin
    @GET("api/barcode/resolve")
    suspend fun resolveBarcode(@Query("upc") upc: String): BarcodeResolveResponse
```

- [ ] **Step 5: Add the Repository wrappers**

Append to `Repository.kt` (inside the class):

```kotlin
    suspend fun resolveBarcode(upc: String): Result<BarcodeResolveResponse> =
        runCatching { api.resolveBarcode(upc) }

    suspend fun createGame(title: String, coverUrl: String? = null,
                           platforms: List<String> = emptyList(),
                           physical: Boolean = false, upc: String? = null): Result<CreateGameResponse> =
        runCatching { api.createGame(CreateGameBody(title, coverUrl, platforms, physical, upc)) }
```

- [ ] **Step 6: Extend the shared `FakeRepo`**

In `FakeRepo.kt`, add the ctor param + recorder and the stub. Change the class header and the `createGame` override:

```kotlin
class FakeRepo(
    private val reachable: Boolean = true,
    private val gamesList: List<GameSummary> = emptyList(),
    private val detail: GameDetail? = null,
    private val slotsResp: SlotsResponse = SlotsResponse(),
    private val igdb: List<IgdbResult> = emptyList(),
    private val resolveResp: BarcodeResolveResponse = BarcodeResolveResponse("", "none"),
) {
    val pinned = mutableListOf<Triple<Int, Int, String?>>()
    val outcomes = mutableListOf<Pair<Int, String>>()
    val statusSets = mutableListOf<Pair<Int, String>>()
    val reorders = mutableListOf<List<Int>>()
    val created = mutableListOf<CreateGameBody>()
```
And in the `api` object, replace the `createGame` override and add `resolveBarcode`:
```kotlin
        override suspend fun createGame(body: CreateGameBody): CreateGameResponse {
            created += body
            return if (reachable) CreateGameResponse(gameId = 1)
                   else throw RuntimeException("unreachable")
        }
        override suspend fun resolveBarcode(upc: String): BarcodeResolveResponse =
            if (reachable) resolveResp else throw RuntimeException("unreachable")
```
(Leave all other overrides and recorders unchanged.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.RepositoryTest"`
Expected: PASS (the 5 Phase-2 tests + the 4 new ones). Then confirm nothing else broke:
Run: `./gradlew.bat :app:testDebugUnitTest`
Expected: BUILD SUCCESSFUL (all prior view-model tests still compile against the extended `FakeRepo`).

- [ ] **Step 8: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt
git commit -m "feat(android): barcode resolve DTOs + resolveBarcode/createGame repository

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 3: Add tab — `AddViewModel` + `AddScreen` + nav 4th tab

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/add/AddViewModel.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/add/AddScreen.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/common/AppViewModelFactory.kt` (add `AddViewModel` branch)
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt` (4th tab + `add` route incl. optional prefill/upc args)
- Test: `android/app/src/test/java/com/gametracker/companion/ui/AddViewModelTest.kt`

**Interfaces:**
- Consumes: `Repository.igdbSearch(q)`, `Repository.createGame(...)`; `IgdbResult(name, slug, coverUrl, platforms)`; shared `UiState`, `CoverImage`, `rememberAppFactory`; `FakeRepo` (`igdb`, `created`).
- Produces: `AddViewModel(repository)` with `results: StateFlow<UiState<List<IgdbResult>>>`, `fun search(q: String)`, `suspend fun add(result: IgdbResult, physical: Boolean = false, upc: String? = null): Int?` (returns new game_id or null). `AddScreen(initialQuery: String?, pendingUpc: String?, onOpenGame: (Int) -> Unit, onScan: () -> Unit)`.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/gametracker/companion/ui/AddViewModelTest.kt`:

```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.IgdbResult
import com.gametracker.companion.ui.add.AddViewModel
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class AddViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(StandardTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun search_populates_results() = runTest {
        val repo = FakeRepo(igdb = listOf(IgdbResult(name = "Hades"), IgdbResult(name = "Halo")))
        val vm = AddViewModel(repo.asRepository())
        vm.search("ha"); advanceUntilIdle()
        val s = vm.results.value
        assertTrue(s is UiState.Success)
        assertEquals(2, (s as UiState.Success).data.size)
    }

    @Test fun search_below_two_chars_is_empty_state() = runTest {
        val vm = AddViewModel(FakeRepo(igdb = listOf(IgdbResult(name = "Hades"))).asRepository())
        vm.search("h"); advanceUntilIdle()
        assertEquals(UiState.Empty, vm.results.value)
    }

    @Test fun add_posts_create_with_upc_and_physical() = runTest {
        val repo = FakeRepo(igdb = emptyList())
        val vm = AddViewModel(repo.asRepository())
        val gid = vm.add(IgdbResult(name = "Celeste", coverUrl = "u", platforms = listOf("ps5")),
                         physical = true, upc = "scan-upc")
        advanceUntilIdle()
        assertEquals(1, gid)
        val body = repo.created.single()
        assertEquals("Celeste", body.title)
        assertEquals(true, body.physical)
        assertEquals("scan-upc", body.upc)
        assertEquals(listOf("ps5"), body.platforms)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.AddViewModelTest"`
Expected: FAIL — unresolved `AddViewModel`.

- [ ] **Step 3: Write `AddViewModel.kt`**

```kotlin
package com.gametracker.companion.ui.add

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.IgdbResult
import com.gametracker.companion.data.Repository
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class AddViewModel(private val repository: Repository) : ViewModel() {

    private val _results = MutableStateFlow<UiState<List<IgdbResult>>>(UiState.Empty)
    val results: StateFlow<UiState<List<IgdbResult>>> = _results

    fun search(q: String) = viewModelScope.launch {
        if (q.length < 2) { _results.value = UiState.Empty; return@launch }
        _results.value = UiState.Loading
        repository.igdbSearch(q).fold(
            onSuccess = { _results.value = if (it.isEmpty()) UiState.Empty else UiState.Success(it) },
            onFailure = { _results.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    /** Create the game; returns the new game_id (or null on failure). */
    suspend fun add(result: IgdbResult, physical: Boolean = false, upc: String? = null): Int? =
        repository.createGame(
            title = result.name, coverUrl = result.coverUrl,
            platforms = result.platforms, physical = physical, upc = upc,
        ).getOrNull()?.gameId
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.AddViewModelTest"`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the factory branch**

In `AppViewModelFactory.kt`, add before the `else`:
```kotlin
            modelClass.isAssignableFrom(com.gametracker.companion.ui.add.AddViewModel::class.java) ->
                com.gametracker.companion.ui.add.AddViewModel(c.repository) as T
```

- [ ] **Step 6: Write `AddScreen.kt`** (smoke-verified)

```kotlin
package com.gametracker.companion.ui.add

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.IgdbResult
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun AddScreen(initialQuery: String?, pendingUpc: String?, onOpenGame: (Int) -> Unit, onScan: () -> Unit) {
    val vm: AddViewModel = viewModel(factory = rememberAppFactory())
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var query by remember { mutableStateOf(initialQuery ?: "") }
    LaunchedEffect(query) { delay(300); vm.search(query) }   // debounce

    Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            OutlinedTextField(query, { query = it }, label = { Text("Search to add a game") },
                singleLine = true, modifier = Modifier.fillMaxWidth().padding(8.dp))
            Button(onClick = onScan, modifier = Modifier.padding(horizontal = 8.dp)) {
                Text("Scan barcode")
            }
            when (val st = vm.results.collectAsState().value) {
                is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
                is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("Search IGDB to add") }
                is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(st.message) }
                is UiState.Success -> LazyColumn(Modifier.fillMaxSize()) {
                    items(st.data) { r: IgdbResult ->
                        Row(Modifier.fillMaxWidth().clickable {
                            scope.launch {
                                val gid = vm.add(r, physical = pendingUpc != null, upc = pendingUpc)
                                if (gid != null) onOpenGame(gid)
                                else snackbar.showSnackbar("Couldn't add — it may already be in your library")
                            }
                        }.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            CoverImage(r.coverUrl, r.name, Modifier.width(48.dp).height(64.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(r.name)
                        }
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 7: Wire the 4th tab + route in `Nav.kt`**

Add the import and a tab, and the route. Replace the `TABS` list and add the `add` composable. First add the import near the other icon imports:
```kotlin
import androidx.compose.material.icons.filled.Add
```
Change `TABS` to include Add (order: Picks, Library, Add, Settings):
```kotlin
private val TABS = listOf(
    Tab("picks", "Picks", Icons.Filled.Home),
    Tab("library", "Library", Icons.AutoMirrored.Filled.List),
    Tab("add", "Add", Icons.Filled.Add),
    Tab("settings", "Settings", Icons.Filled.Settings),
)
```
Add inside the `NavHost { ... }` (after the `library` composable). The route carries optional `prefill`/`upc` query args so the Scan no-match handoff can prefill:
```kotlin
            composable(
                "add?prefill={prefill}&upc={upc}",
                arguments = listOf(
                    androidx.navigation.navArgument("prefill") { nullable = true; defaultValue = null },
                    androidx.navigation.navArgument("upc") { nullable = true; defaultValue = null },
                ),
            ) { entry ->
                com.gametracker.companion.ui.add.AddScreen(
                    initialQuery = entry.arguments?.getString("prefill"),
                    pendingUpc = entry.arguments?.getString("upc"),
                    onOpenGame = { id -> nav.navigate("detail/$id") },
                    onScan = { nav.navigate("scan") },
                )
            }
```
The bottom-nav "add" tab navigates to the bare route `"add"`, which matches the optional-arg pattern with both args null. (No change needed to the `TABS.forEach { nav.navigate(tab.route) }` logic.)

- [ ] **Step 8: Compile + run tests + commit**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.AddViewModelTest"` (PASS) then `./gradlew.bat :app:assembleDebug` (BUILD SUCCESSFUL).
```bash
git add android/app/src
git commit -m "feat(android): Add tab — IGDB text-search add + Scan entry (4th nav tab)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

### Task 4: Scan screen — `ScanViewModel` state machine + `ScanScreen` (CameraX/ML Kit, continuous)

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanViewModel.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/common/AppViewModelFactory.kt` (add `ScanViewModel` branch)
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt` (`scan` route)
- Test: `android/app/src/test/java/com/gametracker/companion/ui/ScanViewModelTest.kt`

**Interfaces:**
- Consumes: `Repository.resolveBarcode(upc)`, `Repository.createGame(...)`; `BarcodeResolveResponse`/`BarcodeCandidate`; `FakeRepo` (`resolveResp`, `created`); shared `UiState`/`CoverImage`/`rememberAppFactory`; the `QrScanScreen` camera pattern.
- Produces: `sealed interface ScanState { Scanning; Resolving; Owned(gameId, title, platform); Candidates(candidates, upc); NoMatch(upc, productTitle); Error(message); Added(gameId) }`; `ScanViewModel(repository)` with `state: StateFlow<ScanState>`, `fun onBarcode(upc: String)`, `fun addCandidate(c: BarcodeCandidate, upc: String)`, `fun reset()`.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/gametracker/companion/ui/ScanViewModelTest.kt`:

```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.BarcodeResolveResponse
import com.gametracker.companion.ui.scan.ScanState
import com.gametracker.companion.ui.scan.ScanViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class ScanViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(StandardTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun owned_candidate_becomes_owned_state() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "cache",
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo",
                platform = "xbox", ownedGameId = 42))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("711"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.Owned)
        assertEquals(42, (s as ScanState.Owned).gameId)
    }

    @Test fun unowned_candidates_become_candidates_state() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            candidates = listOf(BarcodeCandidate(igdbId = 9, title = "P5", platform = "ps5"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("711"); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.Candidates)
    }

    @Test fun no_match_becomes_nomatch_with_product_title() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("999", "upc_api",
            candidates = emptyList(), productTitle = "Mystery Game"))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("999"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.NoMatch)
        assertEquals("Mystery Game", (s as ScanState.NoMatch).productTitle)
    }

    @Test fun source_none_becomes_nomatch() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("0", "none"))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("0"); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.NoMatch)
    }

    @Test fun add_candidate_posts_create_with_upc_and_physical() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api"))
        val vm = ScanViewModel(repo.asRepository())
        vm.addCandidate(BarcodeCandidate(igdbId = 9, title = "P5", platform = "ps5",
            coverUrl = "u"), "711"); advanceUntilIdle()
        val body = repo.created.single()
        assertEquals("P5", body.title)
        assertEquals(true, body.physical)
        assertEquals("711", body.upc)
        assertEquals(listOf("ps5"), body.platforms)
        assertTrue(vm.state.value is ScanState.Added)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.ScanViewModelTest"`
Expected: FAIL — unresolved `ScanViewModel` / `ScanState`.

- [ ] **Step 3: Write `ScanViewModel.kt`**

```kotlin
package com.gametracker.companion.ui.scan

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.Repository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ScanState {
    data object Scanning : ScanState
    data object Resolving : ScanState
    data class Owned(val gameId: Int, val title: String?, val platform: String?) : ScanState
    data class Candidates(val candidates: List<BarcodeCandidate>, val upc: String) : ScanState
    data class NoMatch(val upc: String, val productTitle: String?) : ScanState
    data class Error(val message: String) : ScanState
    data class Added(val gameId: Int?) : ScanState
}

class ScanViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<ScanState>(ScanState.Scanning)
    val state: StateFlow<ScanState> = _state

    fun onBarcode(upc: String) = viewModelScope.launch {
        _state.value = ScanState.Resolving
        repository.resolveBarcode(upc).fold(
            onSuccess = { r ->
                val owned = r.candidates.firstOrNull { it.ownedGameId != null }
                _state.value = when {
                    owned != null -> ScanState.Owned(owned.ownedGameId!!, owned.title, owned.platform)
                    r.candidates.isNotEmpty() -> ScanState.Candidates(r.candidates, upc)
                    else -> ScanState.NoMatch(upc, r.productTitle)
                }
            },
            onFailure = { _state.value = ScanState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun addCandidate(c: BarcodeCandidate, upc: String) = viewModelScope.launch {
        val gid = repository.createGame(
            title = c.title ?: "", coverUrl = c.coverUrl,
            platforms = listOfNotNull(c.platform), physical = true, upc = upc,
        ).getOrNull()?.gameId
        _state.value = ScanState.Added(gid)
    }

    fun reset() { _state.value = ScanState.Scanning }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.ScanViewModelTest"`
Expected: PASS (5 tests).

- [ ] **Step 5: Add the factory branch**

In `AppViewModelFactory.kt`, add before the `else`:
```kotlin
            modelClass.isAssignableFrom(com.gametracker.companion.ui.scan.ScanViewModel::class.java) ->
                com.gametracker.companion.ui.scan.ScanViewModel(c.repository) as T
```

- [ ] **Step 6: Write `ScanScreen.kt`** (smoke-verified — camera + state overlay)

```kotlin
package com.gametracker.companion.ui.scan

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.rememberAppFactory
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage

private val PRODUCT_FORMATS = setOf(
    Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E, Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
)

@Composable
fun ScanScreen(onOpenGame: (Int) -> Unit, onManualSearch: (String?, String) -> Unit) {
    val vm: ScanViewModel = viewModel(factory = rememberAppFactory())
    val context = LocalContext.current
    var granted by remember { mutableStateOf(false) }
    var fired by remember { mutableStateOf(false) }
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { permLauncher.launch(Manifest.permission.CAMERA) }

    if (!granted) { Text("Camera permission is required to scan a barcode."); return }

    val state = vm.state.collectAsState().value

    Box(Modifier.fillMaxSize()) {
        AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
            val previewView = PreviewView(ctx)
            val scanner = BarcodeScanning.getClient()
            val providerFuture = ProcessCameraProvider.getInstance(ctx)
            providerFuture.addListener({
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                val analysis = ImageAnalysis.Builder().build()
                analysis.setAnalyzer(ContextCompat.getMainExecutor(ctx)) { proxy ->
                    val media = proxy.image
                    if (media != null) {
                        val img = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
                        scanner.process(img)
                            .addOnSuccessListener { codes ->
                                codes.firstOrNull { it.format in PRODUCT_FORMATS }?.rawValue
                                    ?.let { if (!fired) { fired = true; vm.onBarcode(it) } }
                            }
                            .addOnCompleteListener { proxy.close() }
                    } else proxy.close()
                }
                provider.unbindAll()
                provider.bindToLifecycle(ctx as LifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }, ContextCompat.getMainExecutor(ctx))
            previewView
        })

        // Result overlay
        when (val s = state) {
            is ScanState.Resolving -> ResultCard { CircularProgressIndicator(); Text("Looking it up…") }
            is ScanState.Owned -> ResultCard {
                Text("You own this — ${s.platform ?: "library"}")
                Button(onClick = { onOpenGame(s.gameId) }) { Text("View") }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan again") }
            }
            is ScanState.Candidates -> ResultCard {
                s.candidates.forEach { c: BarcodeCandidate ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CoverImage(c.coverUrl, c.title ?: "", Modifier.width(40.dp).height(56.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(c.title ?: "Unknown", Modifier.weight(1f))
                        Button(onClick = { vm.addCandidate(c, s.upc) }) { Text("Add") }
                    }
                }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan again") }
            }
            is ScanState.NoMatch -> ResultCard {
                Text("Couldn't identify that barcode.")
                Button(onClick = { onManualSearch(s.productTitle, s.upc) }) { Text("Search manually") }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan again") }
            }
            is ScanState.Added -> ResultCard {
                Text("Added ✓")
                s.gameId?.let { Button(onClick = { onOpenGame(it) }) { Text("View") } }
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Scan another") }
            }
            is ScanState.Error -> ResultCard {
                Text(s.message)
                TextButton(onClick = { fired = false; vm.reset() }) { Text("Try again") }
            }
            ScanState.Scanning -> {}
        }
    }
}

@Composable
private fun ResultCard(content: @Composable ColumnScope.() -> Unit) {
    Box(Modifier.fillMaxSize(), Alignment.BottomCenter) {
        Card(Modifier.fillMaxWidth().padding(16.dp)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
                content = content)
        }
    }
}
```

- [ ] **Step 7: Wire the `scan` route in `Nav.kt`**

Add inside the `NavHost { ... }` (after the `add` composable):
```kotlin
            composable("scan") {
                com.gametracker.companion.ui.scan.ScanScreen(
                    onOpenGame = { id -> nav.navigate("detail/$id") },
                    onManualSearch = { productTitle, upc ->
                        val q = productTitle?.let { java.net.URLEncoder.encode(it, "UTF-8") } ?: ""
                        nav.navigate("add?prefill=$q&upc=$upc") {
                            popUpTo("scan") { inclusive = true }
                        }
                    },
                )
            }
```
(The no-match handoff navigates to the Add route carrying the `productTitle` prefill and the pending `upc`, and pops the scan screen off the back stack.)

- [ ] **Step 8: Compile + run tests + full suite + commit**

Run: `./gradlew.bat :app:testDebugUnitTest --tests "*.ScanViewModelTest"` (PASS), then `./gradlew.bat :app:testDebugUnitTest` (full suite green), then `./gradlew.bat :app:assembleDebug` (BUILD SUCCESSFUL).
```bash
git add android/app/src
git commit -m "feat(android): barcode Scan screen — resolve chain + own/confirm/no-match flow

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CBkaXjAZp9ANwpUH6phsgf"
```

---

## Self-Review

**Spec coverage (Phase 3 spec §1–§10):**
- §2 backend `igdb_id` carryover (created + 409 paths) → Task 1 ✅ (created-path test mocks enrichment to set `igdb_id`; 409-path test seeds an existing game with `igdb_id`).
- §3 API client (`BarcodeResolveResponse`/`BarcodeCandidate`, `resolveBarcode`, `CreateGameBody.upc`, `Repository.createGame`) → Task 2 ✅ (MockWebServer tests).
- §4 Add tab (text search → create; Scan button; prefill + pending upc) → Task 3 ✅.
- §5 Scan screen (continuous auto-detect; Owned / Candidates / NoMatch / Added / Error; confirm posts `upc`+`physical=true`; no-match → Add prefilled, upc threaded) → Task 4 ✅.
- §6 nav/wiring (4th Add tab, `scan` route, factory branches) → Tasks 3 + 4 ✅.
- §7 error handling (UiState.Error / ScanState.Error + retry; resolve `none` is normal; create failure → snackbar) → Tasks 3 + 4 ✅.
- §8 testing (pytest temp DB; MockWebServer resolve/create; AddViewModel + ScanViewModel fake-repo; camera = smoke) → all tasks ✅.

**Placeholder scan:** none — every code step shows complete code and an exact command + expected result.

**Type consistency:** `BarcodeResolveResponse(upc, source, candidates, productTitle)` and `BarcodeCandidate(igdbId, title, platform, coverUrl, ownedGameId)` are defined in Task 2 and consumed identically in Task 4. `Repository.createGame(title, coverUrl, platforms, physical, upc)` signature is identical across Tasks 2/3/4. `FakeRepo` ctor (`resolveResp`) and recorder (`created`) added in Task 2 are used by Tasks 3/4. `ScanState` variants are self-consistent within Task 4.

**Deliberate simplification (flag for owner):** On `POST /api/games`, a 409 (already-owned) makes Retrofit throw, so `Repository.createGame` returns `Result.failure` — the app shows "Couldn't add — it may already be in your library" rather than deep-linking to the existing game (spec §4 mentioned the deep-link). This is acceptable because the **scan** path already detects ownership *before* adding via `resolve`'s `owned_game_id` (the `Owned` state). Deep-linking from a 409 would need `Response<CreateGameResponse>` plumbing; deferred as a follow-up. No other spec deviations.
