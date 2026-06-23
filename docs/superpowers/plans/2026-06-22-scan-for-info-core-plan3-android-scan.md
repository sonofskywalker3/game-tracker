# Scan-for-Info Core — Plan 3: Android Scan-for-Info Screen

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Android barcode flow from "scan to add" into a hands-free **scan-for-info** experience: scan → resolve → show the game with cross-platform + multi-pack ownership (e.g. "You already own this on PS5 (Physical)"), with a bottom-center barcode FAB, X/tap-off-to-rescan, and auto re-arm ~5s after an add.

**Architecture:** Native Kotlin/Jetpack Compose companion app in `android/`. Compose screen → `ScanViewModel` (`StateFlow<ScanState>`) → `Repository` → dynamic-host Retrofit. The enhanced backend `resolve()` (Plan 1, live-verified 2026-06-22) already returns `scanned_platform`, per-candidate `owned_platforms[]` with format, and bundle `constituents[]`; this plan extends the DTOs and UI to consume it. The detail screen stays read-only (web is the canonical editor — see memory `web-main-mobile-streamlined`).

**Tech Stack:** Kotlin, Jetpack Compose, Material 3, CameraX + ML Kit (already wired), Retrofit + kotlinx.serialization, Coroutines. Unit tests: JUnit4 + `kotlinx-coroutines-test` + MockWebServer (existing patterns). Plus one Python/pytest backend task.

## Global Constraints

- **Android build/test** from `android/`: build `./gradlew.bat assembleDebug`; unit tests `./gradlew.bat testDebugUnitTest`. adb at `C:\Users\Jeff\AppData\Local\Android\Sdk\platform-tools\adb.exe`; device SM-S948U (serial `R5GL11FYRGE`). After install, ALWAYS launch: `adb ... shell am start -n com.gametracker.companion/.MainActivity` (owner requirement).
- **Python** (Task 1 only): tests `uv run python -m pytest` (NOT plain `pytest`); lint `ruff check` ONLY (never `ruff format`). Use the pytest temp DB; NEVER touch the live `games.db` or the running server.
- **Do NOT change dependency or SDK versions** without owner approval. `material-icons-extended` is already a dependency (`app/build.gradle.kts:44` → `libs.compose.material.icons`), so `Icons.Filled.QrCodeScanner` is available with no dep change.
- `appJson()` already sets `ignoreUnknownKeys = true; explicitNulls = false` (`data/Networking.kt:11`) — adding DTO fields is backward-compatible and the installed build tolerates the new response keys.
- Repository methods return `Result<T>` via `runCatching` (one error pattern); ViewModels `.fold` over them. Match this exactly.
- The detail screen stays read-only; per-platform format editing lives on the web (Plan 2). Mobile only *reads/displays* format via the resolve payload.
- Subagents: static work + unit tests only. Building/installing/launching on the device and the on-device smoke are the **controller's / owner's** responsibility, not a subagent's.

---

## File Structure

- `barcode.py` — `resolve()`: add a zero-result unrestricted retry (deferred Plan-1 follow-up).
- `android/.../data/Dtos.kt` — add `OwnedPlatform`, `BarcodeConstituent`; extend `BarcodeCandidate` (+`gameType`, `ownedPlatforms`, `constituents`) and `BarcodeResolveResponse` (+`scannedPlatform`).
- `android/.../data/GameTrackerApi.kt` — add `AddPlatformPayload`/`AddPlatformBody` + `addPlatform(id, body)` PUT.
- `android/.../data/Repository.kt` — add `addPlatform(...)`.
- `android/.../ui/FakeRepo.kt` (test) — add `addedPlatforms` recorder + override.
- `android/.../ui/scan/Ownership.kt` (new) — pure ownership/label helpers.
- `android/.../ui/scan/ScanViewModel.kt` — new `ScanState` set + `onBarcode`/`addToLibrary`/`addPlatformCopy`/`reset`.
- `android/.../ui/scan/ScanScreen.kt` — scan-for-info overlay + auto re-arm + X/tap-off.
- `android/.../ui/Nav.kt` — bottom-center barcode FAB; drop `onScan` from the Add route.
- `android/.../ui/add/AddScreen.kt` — remove the "Scan barcode" button + `onScan` param.
- Tests: `data/RepositoryTest.kt`, `data/BarcodeDtosTest.kt` (new), `ui/ScanViewModelTest.kt`, `ui/OwnershipTest.kt` (new), `tests/test_api_barcode.py`.

---

## Task 1: Backend — unrestricted retry when the platform filter zeroes out

**Files:**
- Modify: `barcode.py` — `resolve()` candidate fetch (~200-215)
- Test: `tests/test_api_barcode.py`

**Interfaces:**
- Consumes: `igdb_match.candidates_for(..., drop_fan_types=True, restrict_to_platform=...)`.
- Produces: when a platform-restricted search returns zero candidates, `resolve()` retries once with `restrict_to_platform=False` (still `drop_fan_types=True`). Aligns with the owner's "scanner misses some games" pain.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_barcode.py` (mirrors the cred setup of the existing `test_resolve_reports_owned_bundle_constituents`, which already drives the matcher path):
```python
def test_resolve_retries_unrestricted_when_platform_filter_zeroes_out(client, monkeypatch):
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Obscure Game (Nintendo Switch)")
    monkeypatch.setattr(barcode.igdb_match, "platform_ids_for", lambda shorts: {130})
    monkeypatch.setattr(barcode.igdb_match, "short_names_for", lambda ids: ["Switch"])

    calls = []

    def fake_candidates_for(title, plat_ids, coll, cid, tok, *,
                            drop_fan_types=False, restrict_to_platform=False):
        calls.append(restrict_to_platform)
        if restrict_to_platform:
            return []   # platform-restricted search finds nothing
        return [{"igdb_id": 77, "name": "Obscure Game", "platforms": [130],
                 "cover_url": "c", "source": "search", "score": 50, "game_type": 0}]

    monkeypatch.setattr(barcode.igdb_match, "candidates_for", fake_candidates_for)

    body = client.get("/api/barcode/resolve?upc=OBSCURE1").get_json()
    assert calls == [True, False]          # restricted first, then unrestricted retry
    assert body["candidates"][0]["title"] == "Obscure Game"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_api_barcode.py::test_resolve_retries_unrestricted_when_platform_filter_zeroes_out -v`
Expected: FAIL — `calls == [True]` (no retry); `body["candidates"]` empty → `KeyError`/IndexError.

- [ ] **Step 3: Write minimal implementation**

In `barcode.py` `resolve()`, replace the candidate-fetch loop header. Change:
```python
    candidates: list[dict] = []
    if client_id and token:
        for c in igdb_match.candidates_for(
                search_title, platform_ids, None, client_id, token,
                drop_fan_types=True, restrict_to_platform=bool(platform_ids))[:MAX_CANDIDATES]:
```
to:
```python
    candidates: list[dict] = []
    if client_id and token:
        raw = igdb_match.candidates_for(
            search_title, platform_ids, None, client_id, token,
            drop_fan_types=True, restrict_to_platform=bool(platform_ids))
        # Some valid IGDB entries have empty/incomplete platform lists, so a
        # platform-restricted search can drop the real game entirely. If the
        # restricted search found nothing, retry once unrestricted (still dropping
        # fan/mod types) so a known scanned platform never zeroes out a valid scan.
        if not raw and platform_ids:
            raw = igdb_match.candidates_for(
                search_title, platform_ids, None, client_id, token,
                drop_fan_types=True, restrict_to_platform=False)
        for c in raw[:MAX_CANDIDATES]:
```
(The loop body — building each candidate dict — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_api_barcode.py -v`
Expected: PASS (new test + all existing barcode tests, including the bundle/ownership/unmatched ones).

- [ ] **Step 5: Commit**

```bash
git add barcode.py tests/test_api_barcode.py
git commit -m "feat(barcode): retry unrestricted when platform-filtered scan yields zero candidates"
```

---

## Task 2: DTOs for ownership + multi-pack constituents

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/data/Dtos.kt` (~105-120)
- Test (new): `android/app/src/test/java/com/gametracker/companion/data/BarcodeDtosTest.kt`

**Interfaces:**
- Produces: `OwnedPlatform(shortName: String?, format: String?, hasDigitalMarket: Int)`; `BarcodeConstituent(title: String?, ownedGameId: Int?, ownedPlatforms: List<OwnedPlatform>)`; `BarcodeCandidate` gains `gameType: Int?`, `ownedPlatforms: List<OwnedPlatform>`, `constituents: List<BarcodeConstituent>`; `BarcodeResolveResponse` gains `scannedPlatform: String?`.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/gametracker/companion/data/BarcodeDtosTest.kt`:
```kotlin
package com.gametracker.companion.data

import org.junit.Assert.*
import org.junit.Test

class BarcodeDtosTest {
    @Test fun parses_enhanced_resolve_with_ownership_and_constituents() {
        val json = appJson()
        val body = json.decodeFromString<BarcodeResolveResponse>(
            """{"upc":"045496590475","source":"upc_api","scanned_platform":"Switch",
                "candidates":[
                  {"igdb_id":26764,"title":"Mario Kart 8 Deluxe","platform":"Switch",
                   "cover_url":"https://x/c.jpg","game_type":10,"owned_game_id":341,
                   "owned_platforms":[{"short_name":"Switch","format":"digital","has_digital_market":1}]},
                  {"igdb_id":203219,"title":"MK8D + Super Mario Party Double Pack","platform":"Switch",
                   "cover_url":"https://x/d.jpg","game_type":3,"owned_game_id":null,"owned_platforms":[],
                   "constituents":[
                     {"title":"Super Mario Party","owned_game_id":null,"owned_platforms":[]},
                     {"title":"Mario Kart 8 Deluxe","owned_game_id":341,
                      "owned_platforms":[{"short_name":"Switch","format":"digital","has_digital_market":1}]}]}]}"""
        )
        assertEquals("Switch", body.scannedPlatform)
        val top = body.candidates[0]
        assertEquals(341, top.ownedGameId)
        assertEquals("Switch", top.ownedPlatforms[0].shortName)
        assertEquals("digital", top.ownedPlatforms[0].format)
        assertEquals(1, top.ownedPlatforms[0].hasDigitalMarket)
        val bundle = body.candidates[1]
        assertEquals(3, bundle.gameType)
        assertEquals(2, bundle.constituents.size)
        assertEquals(341, bundle.constituents[1].ownedGameId)
        assertEquals("Switch", bundle.constituents[1].ownedPlatforms[0].shortName)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.BarcodeDtosTest"`
Expected: FAIL — compile error (`ownedPlatforms`/`constituents`/`scannedPlatform` unresolved).

- [ ] **Step 3: Write minimal implementation**

In `Dtos.kt`, replace the `BarcodeCandidate` and `BarcodeResolveResponse` blocks (~105-120) with:
```kotlin
@Serializable
data class OwnedPlatform(
    @SerialName("short_name") val shortName: String? = null,
    val format: String? = null,
    @SerialName("has_digital_market") val hasDigitalMarket: Int = 0,
)

@Serializable
data class BarcodeConstituent(
    val title: String? = null,
    @SerialName("owned_game_id") val ownedGameId: Int? = null,
    @SerialName("owned_platforms") val ownedPlatforms: List<OwnedPlatform> = emptyList(),
)

@Serializable
data class BarcodeCandidate(
    @SerialName("igdb_id") val igdbId: Int? = null,
    val title: String? = null,
    val platform: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    @SerialName("owned_game_id") val ownedGameId: Int? = null,
    @SerialName("game_type") val gameType: Int? = null,
    @SerialName("owned_platforms") val ownedPlatforms: List<OwnedPlatform> = emptyList(),
    val constituents: List<BarcodeConstituent> = emptyList(),
)

@Serializable
data class BarcodeResolveResponse(
    val upc: String,
    val source: String,
    val candidates: List<BarcodeCandidate> = emptyList(),
    @SerialName("product_title") val productTitle: String? = null,
    @SerialName("scanned_platform") val scannedPlatform: String? = null,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.BarcodeDtosTest"`
Expected: PASS. (Existing `RepositoryTest.resolveBarcode_*` still compile — the new fields default to empty.)

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data/Dtos.kt \
        android/app/src/test/java/com/gametracker/companion/data/BarcodeDtosTest.kt
git commit -m "feat(android): DTOs for scan ownership (owned_platforms, constituents, scanned_platform)"
```

---

## Task 3: Add-a-platform API + Repository method

**Files:**
- Modify: `android/.../data/GameTrackerApi.kt` (~17-20 bodies, ~34-35 PUT methods)
- Modify: `android/.../data/Repository.kt` (~35-38)
- Modify: `android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt`

**Interfaces:**
- Produces: `AddPlatformPayload(short_name: String, format: String? = null, upc: String? = null)`; `AddPlatformBody(add_platform: AddPlatformPayload)`; `GameTrackerApi.addPlatform(id: Int, body: AddPlatformBody)` (PUT `api/games/{id}`); `Repository.addPlatform(id: Int, shortName: String, format: String?, upc: String?): Result<Unit>`.
- Consumes (test): FakeRepo gains `val addedPlatforms = mutableListOf<Pair<Int, AddPlatformPayload>>()`.

- [ ] **Step 1: Write the failing test**

Append to `RepositoryTest.kt`:
```kotlin
    @Test fun addPlatform_sends_put_with_add_platform_body() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true}"""))
        val r = repo.addPlatform(341, "Switch", "physical", "upc-9")
        assertTrue(r.isSuccess)
        val recorded = server.takeRequest()
        assertEquals("PUT", recorded.method)
        assertEquals("/api/games/341", recorded.path)
        val sent = recorded.body.readUtf8()
        assertTrue(sent.contains("\"add_platform\""))
        assertTrue(sent.contains("\"short_name\":\"Switch\""))
        assertTrue(sent.contains("\"format\":\"physical\""))
        assertTrue(sent.contains("\"upc\":\"upc-9\""))
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.RepositoryTest"`
Expected: FAIL — compile error (`repo.addPlatform` unresolved).

- [ ] **Step 3: Write minimal implementation**

In `GameTrackerApi.kt`, add the bodies near the other `@Serializable data class` declarations (after `CreateGameBody`, ~20):
```kotlin
@Serializable data class AddPlatformPayload(val short_name: String, val format: String? = null,
                                            val upc: String? = null)
@Serializable data class AddPlatformBody(val add_platform: AddPlatformPayload)
```
And add the method to the `interface GameTrackerApi` (after `updateGame`, ~35):
```kotlin
    @PUT("api/games/{id}")
    suspend fun addPlatform(@Path("id") id: Int, @Body body: AddPlatformBody)
```
In `Repository.kt`, add after `createGame` (~38):
```kotlin
    suspend fun addPlatform(id: Int, shortName: String, format: String?, upc: String?): Result<Unit> =
        runCatching { api.addPlatform(id, AddPlatformBody(AddPlatformPayload(shortName, format, upc))) }
```
In `FakeRepo.kt`, add the recorder (after `val created`, ~19):
```kotlin
    val addedPlatforms = mutableListOf<Pair<Int, AddPlatformPayload>>()
```
and the override inside the anonymous `api` object (after `createGame`, ~33):
```kotlin
        override suspend fun addPlatform(id: Int, body: AddPlatformBody) {
            addedPlatforms += id to body.add_platform
            if (!reachable) throw RuntimeException("unreachable")
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.RepositoryTest"`
Expected: PASS (new test + all existing Repository tests; FakeRepo still satisfies the interface).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data/GameTrackerApi.kt \
        android/app/src/main/java/com/gametracker/companion/data/Repository.kt \
        android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt \
        android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt
git commit -m "feat(android): addPlatform API + Repository (PUT add_platform with format + upc)"
```

---

## Task 4: Pure ownership + label helpers

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/scan/Ownership.kt`
- Test (new): `android/app/src/test/java/com/gametracker/companion/ui/OwnershipTest.kt`

**Interfaces:**
- Produces: `enum class Ownership { NOT_OWNED, SAME_PLATFORM, OTHER_PLATFORM }`; `fun platformLabel(p: OwnedPlatform): String` (appends `(Physical)`/`(Digital)` only when `hasDigitalMarket == 1` and `format != null`); `fun ownershipOf(candidate: BarcodeCandidate, scannedPlatform: String?): Ownership`; `fun ownedLabels(platforms: List<OwnedPlatform>): String`.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/gametracker/companion/ui/OwnershipTest.kt`:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.OwnedPlatform
import com.gametracker.companion.ui.scan.Ownership
import com.gametracker.companion.ui.scan.ownedLabels
import com.gametracker.companion.ui.scan.ownershipOf
import com.gametracker.companion.ui.scan.platformLabel
import org.junit.Assert.*
import org.junit.Test

class OwnershipTest {
    @Test fun label_adds_qualifier_only_for_digital_market() {
        assertEquals("PS5 (Physical)",
            platformLabel(OwnedPlatform("PS5", "physical", 1)))
        assertEquals("3DS (Digital)",
            platformLabel(OwnedPlatform("3DS", "digital", 1)))
        assertEquals("SNES",  // cartridge-only legacy: no qualifier
            platformLabel(OwnedPlatform("SNES", "physical", 0)))
    }

    @Test fun not_owned_when_no_owned_platforms() {
        val c = BarcodeCandidate(title = "X", ownedPlatforms = emptyList())
        assertEquals(Ownership.NOT_OWNED, ownershipOf(c, "Switch"))
    }

    @Test fun same_platform_when_owned_on_scanned() {
        val c = BarcodeCandidate(title = "X",
            ownedPlatforms = listOf(OwnedPlatform("Switch", "digital", 1)))
        assertEquals(Ownership.SAME_PLATFORM, ownershipOf(c, "Switch"))
    }

    @Test fun other_platform_when_owned_elsewhere() {
        val c = BarcodeCandidate(title = "X",
            ownedPlatforms = listOf(OwnedPlatform("PS5", "physical", 1)))
        assertEquals(Ownership.OTHER_PLATFORM, ownershipOf(c, "Switch"))
        assertEquals("PS5 (Physical)", ownedLabels(c.ownedPlatforms))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.ui.OwnershipTest"`
Expected: FAIL — compile error (helpers unresolved).

- [ ] **Step 3: Write minimal implementation**

Create `android/app/src/main/java/com/gametracker/companion/ui/scan/Ownership.kt`:
```kotlin
package com.gametracker.companion.ui.scan

import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.OwnedPlatform

enum class Ownership { NOT_OWNED, SAME_PLATFORM, OTHER_PLATFORM }

/** "PS5 (Physical)" / "3DS (Digital)" / "SNES". The (Physical/Digital) qualifier is
 *  shown only for platforms with a digital storefront (has_digital_market). */
fun platformLabel(p: OwnedPlatform): String {
    val base = p.shortName ?: "?"
    val fmt = p.format
    return if (p.hasDigitalMarket == 1 && fmt != null)
        "$base (${fmt.replaceFirstChar { it.uppercase() }})" else base
}

fun ownedLabels(platforms: List<OwnedPlatform>): String =
    platforms.joinToString(", ") { platformLabel(it) }

/** Ownership of a resolved title relative to the platform the barcode was scanned on. */
fun ownershipOf(candidate: BarcodeCandidate, scannedPlatform: String?): Ownership {
    val owned = candidate.ownedPlatforms
    if (owned.isEmpty()) return Ownership.NOT_OWNED
    val onScanned = scannedPlatform != null && owned.any { it.shortName == scannedPlatform }
    return if (onScanned) Ownership.SAME_PLATFORM else Ownership.OTHER_PLATFORM
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.ui.OwnershipTest"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/scan/Ownership.kt \
        android/app/src/test/java/com/gametracker/companion/ui/OwnershipTest.kt
git commit -m "feat(android): pure ownership + platform-label helpers for scan-for-info"
```

---

## Task 5: ScanViewModel — scan-for-info state machine

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanViewModel.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/ScanViewModelTest.kt`

**Interfaces:**
- Consumes: `repository.resolveBarcode`, `repository.createGame`, `repository.addPlatform`; `BarcodeCandidate`, `BarcodeResolveResponse`.
- Produces: `ScanState` = `Scanning | Resolving | Info(candidate, scannedPlatform, upc) | NoMatch(upc, productTitle) | Error(message) | Added(gameId)`. Methods: `onBarcode(upc)`, `addToLibrary(c, scannedPlatform, upc)`, `addPlatformCopy(c, scannedPlatform, upc)`, `reset()`. (The ~5s auto re-arm after `Added` is driven by the screen, not the VM, so the VM stays deterministically testable.)

- [ ] **Step 1: Write the failing tests**

Replace the body of `ScanViewModelTest.kt` (keep the package + `@Before/@After`) with tests for the new machine:
```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.BarcodeResolveResponse
import com.gametracker.companion.data.OwnedPlatform
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

    @Test fun resolve_with_candidate_becomes_info() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            scannedPlatform = "Switch",
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo", platform = "Switch"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("711"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.Info)
        assertEquals("Switch", (s as ScanState.Info).scannedPlatform)
        assertEquals("Halo", s.candidate.title)
    }

    @Test fun empty_candidates_becomes_nomatch() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("999", "upc_api",
            candidates = emptyList(), productTitle = "Mystery"))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("999"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.NoMatch)
        assertEquals("Mystery", (s as ScanState.NoMatch).productTitle)
    }

    @Test fun addToLibrary_posts_create_with_scanned_platform_and_physical() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api"))
        val vm = ScanViewModel(repo.asRepository())
        vm.addToLibrary(BarcodeCandidate(igdbId = 9, title = "P5", coverUrl = "u"),
            scannedPlatform = "PS5", upc = "711"); advanceUntilIdle()
        val body = repo.created.single()
        assertEquals("P5", body.title)
        assertEquals(true, body.physical)
        assertEquals("711", body.upc)
        assertEquals(listOf("PS5"), body.platforms)
        assertTrue(vm.state.value is ScanState.Added)
    }

    @Test fun addPlatformCopy_calls_add_platform_for_owned_game() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api"))
        val vm = ScanViewModel(repo.asRepository())
        vm.addPlatformCopy(BarcodeCandidate(title = "P5", ownedGameId = 42,
            ownedPlatforms = listOf(OwnedPlatform("PS5", "physical", 1))),
            scannedPlatform = "Switch", upc = "711"); advanceUntilIdle()
        val (id, payload) = repo.addedPlatforms.single()
        assertEquals(42, id)
        assertEquals("Switch", payload.short_name)
        assertEquals("physical", payload.format)
        assertEquals("711", payload.upc)
        assertTrue(vm.state.value is ScanState.Added)
    }

    @Test fun reset_returns_to_scanning() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("0", "none"))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("0"); advanceUntilIdle()
        vm.reset()
        assertTrue(vm.state.value is ScanState.Scanning)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.ui.ScanViewModelTest"`
Expected: FAIL — compile errors (`ScanState.Info`, `addToLibrary`, `addPlatformCopy` unresolved).

- [ ] **Step 3: Write minimal implementation**

Replace `ScanViewModel.kt` with:
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
    data class Info(val candidate: BarcodeCandidate, val scannedPlatform: String?,
                    val upc: String) : ScanState
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
                val top = r.candidates.firstOrNull()
                _state.value = if (top == null) ScanState.NoMatch(upc, r.productTitle)
                               else ScanState.Info(top, r.scannedPlatform, upc)
            },
            onFailure = { _state.value = ScanState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    /** Add a not-owned game, defaulting to the scanned platform + physical format. */
    fun addToLibrary(c: BarcodeCandidate, scannedPlatform: String?, upc: String) =
        viewModelScope.launch {
            val platforms = listOfNotNull(scannedPlatform ?: c.platform)
            val gid = repository.createGame(
                title = c.title ?: "", coverUrl = c.coverUrl,
                platforms = platforms, physical = true, upc = upc,
            ).getOrNull()?.gameId
            _state.value = ScanState.Added(gid)
        }

    /** "I also bought the <scanned> copy": append that platform (physical) + UPC to the
     *  already-owned game. */
    fun addPlatformCopy(c: BarcodeCandidate, scannedPlatform: String, upc: String) =
        viewModelScope.launch {
            val gid = c.ownedGameId
            if (gid != null) repository.addPlatform(gid, scannedPlatform, "physical", upc)
            _state.value = ScanState.Added(gid)
        }

    fun reset() { _state.value = ScanState.Scanning }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.ui.ScanViewModelTest"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/scan/ScanViewModel.kt \
        android/app/src/test/java/com/gametracker/companion/ui/ScanViewModelTest.kt
git commit -m "feat(android): scan-for-info ViewModel state machine (info/add/add-platform)"
```

---

## Task 6: ScanScreen — info overlay, auto re-arm, X / tap-off-to-rescan

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt`
- Test: controller/owner on-device verification (UI; logic is covered by Tasks 4-5).

**Interfaces:**
- Consumes: `ScanState`, `ownershipOf`, `Ownership`, `platformLabel`, `ownedLabels`, `CoverImage`.
- Produces: the scan-for-info overlay. Behaviors (spec §4, §5, §7): ownership banner with format qualifiers; constituent ownership list for bundles; **no** "Scan again"/"Scan another" buttons; an **X** on the result card and **tap-off** both dismiss → live scanning; auto re-arm ~5s after `Added`.

- [ ] **Step 1: Rewrite the overlay**

Replace `ScanScreen.kt` with (camera setup unchanged; overlay + dismiss/re-arm new):
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
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
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
import kotlinx.coroutines.delay

private val PRODUCT_FORMATS = setOf(
    Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E, Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
)
private const val REARM_MS = 5000L

@Composable
fun ScanScreen(onOpenGame: (Int) -> Unit, onManualSearch: (String?, String) -> Unit) {
    val vm: ScanViewModel = viewModel(factory = rememberAppFactory())
    val context = LocalContext.current
    var granted by remember { mutableStateOf(false) }
    var fired by remember { mutableStateOf(false) }
    val permLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { permLauncher.launch(Manifest.permission.CAMERA) }

    if (!granted) {
        Box(Modifier.fillMaxSize(), Alignment.Center) {
            Text("Camera permission is required to scan a barcode.")
        }
        return
    }

    val state = vm.state.collectAsState().value
    fun rescan() { fired = false; vm.reset() }

    // Hands-free: after an add, show the confirmation briefly then re-arm the scanner.
    LaunchedEffect(state) { if (state is ScanState.Added) { delay(REARM_MS); rescan() } }

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

        when (val s = state) {
            ScanState.Scanning -> {}
            is ScanState.Resolving -> ResultCard(onDismiss = null) {
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.width(20.dp)); Text("Looking it up…")
                }
            }
            is ScanState.Info -> ResultCard(onDismiss = ::rescan) {
                ScanInfo(s, onOpenGame = onOpenGame,
                    onAdd = { vm.addToLibrary(s.candidate, s.scannedPlatform, s.upc) },
                    onAddCopy = { p -> vm.addPlatformCopy(s.candidate, p, s.upc) })
            }
            is ScanState.NoMatch -> ResultCard(onDismiss = ::rescan) {
                Text("Couldn't identify that barcode.")
                Button(onClick = { onManualSearch(s.productTitle, s.upc) }) { Text("Search manually") }
            }
            is ScanState.Added -> ResultCard(onDismiss = ::rescan) {
                Text("Added ✓")
                s.gameId?.let { Button(onClick = { onOpenGame(it) }) { Text("View") } }
            }
            is ScanState.Error -> ResultCard(onDismiss = ::rescan) { Text(s.message) }
        }
    }
}

@Composable
private fun ScanInfo(s: ScanState.Info, onOpenGame: (Int) -> Unit,
                     onAdd: () -> Unit, onAddCopy: (String) -> Unit) {
    val c = s.candidate
    Row(verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        CoverImage(c.coverUrl, c.title ?: "", Modifier.width(48.dp))
        Text(c.title ?: "Unknown", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
    }
    when (ownershipOf(c, s.scannedPlatform)) {
        Ownership.NOT_OWNED -> {
            Button(onClick = onAdd) {
                Text("Add to library" + (s.scannedPlatform?.let { " ($it)" } ?: ""))
            }
        }
        Ownership.SAME_PLATFORM -> {
            Text("You already own this on ${ownedLabels(c.ownedPlatforms)} ✓")
            c.ownedGameId?.let { TextButton(onClick = { onOpenGame(it) }) { Text("View") } }
        }
        Ownership.OTHER_PLATFORM -> {
            Text("You already own this on ${ownedLabels(c.ownedPlatforms)}")
            s.scannedPlatform?.let { p ->
                Button(onClick = { onAddCopy(p) }) { Text("Add the $p copy") }
            }
            c.ownedGameId?.let { TextButton(onClick = { onOpenGame(it) }) { Text("View") } }
        }
    }
    // Multi-pack: report which constituents you already own.
    if (c.constituents.isNotEmpty()) {
        HorizontalDivider()
        Text("This collection includes:", style = MaterialTheme.typography.labelMedium)
        c.constituents.forEach { k ->
            val owned = k.ownedPlatforms.isNotEmpty()
            Text(
                if (owned) "✓ ${k.title} — ${ownedLabels(k.ownedPlatforms)}"
                else "• ${k.title}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ResultCard(onDismiss: (() -> Unit)?, content: @Composable ColumnScope.() -> Unit) {
    Box(Modifier.fillMaxSize()) {
        // Tap-off the card dismisses + re-arms (only when dismiss is allowed).
        if (onDismiss != null) {
            Box(Modifier.fillMaxSize().clickable(onClick = onDismiss))
        }
        Box(Modifier.fillMaxSize(), Alignment.BottomCenter) {
            Card(Modifier.fillMaxWidth().padding(16.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (onDismiss != null) {
                        Box(Modifier.fillMaxWidth(), Alignment.TopEnd) {
                            IconButton(onClick = onDismiss) {
                                Icon(Icons.Filled.Close, contentDescription = "Dismiss")
                            }
                        }
                    }
                    content()
                }
            }
        }
    }
}
```

- [ ] **Step 2: Build the app**

Run (from `android/`): `./gradlew.bat assembleDebug`
Expected: `BUILD SUCCESSFUL` (no unresolved references; `HorizontalDivider` is Material 3).

- [ ] **Step 3: Unit tests still green**

Run: `./gradlew.bat testDebugUnitTest`
Expected: `BUILD SUCCESSFUL` (Scan/Ownership/Repository/Dtos tests all pass; ScanScreen is UI-only).

- [ ] **Step 4: Manual verification note (controller/owner)**

Recorded for the gate task (device install is the controller's job, on-device reaction is the owner's): not-owned shows "Add to library (Switch)"; owned-elsewhere shows the cross-platform banner with format qualifiers + "Add the <platform> copy"; owned-on-scanned shows the ✓ line and no add; a collection lists constituent ownership; X and tap-off return to live scanning; after an add the confirmation auto re-arms after ~5s.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt
git commit -m "feat(android): scan-for-info overlay (ownership, multi-pack, auto re-arm, X/tap-off)"
```

---

## Task 7: Bottom-center barcode FAB; remove the Add-screen Scan button

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/add/AddScreen.kt`
- Test: build + controller/owner verification.

**Interfaces:**
- Consumes: nav routes (`scan`, `picks`, `library`, `add`).
- Produces: a `FloatingActionButton` (centered) on the main browsing routes that navigates to `scan`; `AddScreen` no longer takes `onScan` and shows no Scan button.

- [ ] **Step 1: Add the FAB to AppNav**

In `Nav.kt`, add imports:
```kotlin
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.FabPosition
import androidx.compose.material3.FloatingActionButton
```
Change the `Scaffold(bottomBar = { ... }) { padding ->` call to also declare the FAB. Replace the `Scaffold(bottomBar = {` opening and its closing `}) { padding ->` with:
```kotlin
    Scaffold(
        bottomBar = {
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
        },
        floatingActionButton = {
            val entry by nav.currentBackStackEntryAsState()
            val route = entry?.destination?.route
            // Show the scan FAB only on the main browsing routes (not on scan itself,
            // detail, settings, or the VPN QR screen).
            if (route == "picks" || route == "library" || route?.startsWith("add") == true) {
                FloatingActionButton(onClick = { nav.navigate("scan") }) {
                    Icon(Icons.Filled.QrCodeScanner, contentDescription = "Scan barcode")
                }
            }
        },
        floatingActionButtonPosition = FabPosition.Center,
    ) { padding ->
```
(The existing `val entry by ...; val current = ...` lines that were directly under `Scaffold(bottomBar = {` move inside the `bottomBar` lambda as shown; remove the old duplicates.)

- [ ] **Step 2: Drop `onScan` from the Add route**

In `Nav.kt`, in the `composable("add?prefill={prefill}&upc={upc}")` block, remove the `onScan = { nav.navigate("scan") },` argument from the `AddScreen(...)` call.

- [ ] **Step 3: Remove the Scan button + param from AddScreen**

In `AddScreen.kt`, change the signature:
```kotlin
fun AddScreen(initialQuery: String?, pendingUpc: String?, onOpenGame: (Int) -> Unit) {
```
and delete the Scan button block:
```kotlin
            Button(onClick = onScan, modifier = Modifier.padding(horizontal = 8.dp)) {
                Text("Scan barcode")
            }
```

- [ ] **Step 4: Build + unit tests**

Run (from `android/`): `./gradlew.bat assembleDebug testDebugUnitTest`
Expected: `BUILD SUCCESSFUL` (no references to the removed `onScan`).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/Nav.kt \
        android/app/src/main/java/com/gametracker/companion/ui/add/AddScreen.kt
git commit -m "feat(android): bottom-center barcode FAB; remove Add-screen scan button"
```

---

## Task 8: Gate — full unit suite, build, install + launch, on-device smoke

**Files:** none (verification task)

- [ ] **Step 1: Backend gate (Task 1)**

Run: `uv run python -m pytest -q && uv run ruff check`
Expected: all green; `All checks passed!`.

- [ ] **Step 2: Android unit suite + assemble**

Run (from `android/`): `./gradlew.bat testDebugUnitTest assembleDebug`
Expected: `BUILD SUCCESSFUL` (BarcodeDtos, Ownership, ScanViewModel, Repository tests all pass).

- [ ] **Step 3: Install + LAUNCH on device (controller)**

The controller installs and launches (subagents do not touch the device):
```bash
cd android && ./gradlew.bat installDebug
"C:/Users/Jeff/AppData/Local/Android/Sdk/platform-tools/adb.exe" -s R5GL11FYRGE shell am start -n com.gametracker.companion/.MainActivity
```
Ensure the Plan-1 backend is running (`HOST=0.0.0.0 uv run python app.py`) so the phone resolves against the migrated DB.

- [ ] **Step 4: Owner on-device smoke (not automatable)**

- Tap the bottom-center barcode FAB → scanner opens.
- Scan a game owned on a different platform → cross-platform banner with the format qualifier; "Add the <platform> copy" appends that platform (verify in the web app).
- Scan a game owned on the scanned platform → "You already own this on … ✓", no add.
- Scan a not-owned game → "Add to library (<platform>)" adds it; confirmation auto re-arms after ~5s.
- Scan a multi-pack/collection → constituent ownership list.
- X and tap-off return to live scanning.

- [ ] **Step 5: Final whole-branch review**

Per subagent-driven-development, dispatch the final whole-branch review on the most capable model over the Plan 2 + Plan 3 commit range, then address any Critical/Important findings.

---

## Self-Review

- **Spec coverage:** §3 platform-aware matching robustness (zero-result fallback) → Task 1. §4 ownership states (not-owned / other-platform / same-platform) + format qualifiers → Tasks 4 (helpers) + 5 (VM) + 6 (UI). §4 "Add the [platform] copy" → Task 3 (API) + 5 (`addPlatformCopy`) + 6 (button). §5 multi-pack constituents → Tasks 2 (DTO) + 6 (UI list). §7 UX: remove Scan again/another → Task 6; auto re-arm ~5s → Task 6 `LaunchedEffect`; X/cancel + tap-off → Task 6 `ResultCard(onDismiss)`; bottom-center barcode FAB → Task 7; detail stays read-only → unchanged (no detail edits in this plan). §6 enhanced resolve consumption → Tasks 2-6.
- **Placeholder scan:** every step has concrete Kotlin/Python/commands. UI-only steps (Task 6 verification, Task 8 smoke) explicitly defer to controller/owner because they are not unit-testable — stated, not hand-waved.
- **Type consistency:** `OwnedPlatform`/`BarcodeConstituent`/`BarcodeCandidate`/`BarcodeResolveResponse` (Task 2) are consumed by `Ownership.kt` (Task 4), the VM (Task 5), and the screen (Task 6). `AddPlatformPayload`/`AddPlatformBody` + `Repository.addPlatform(id, shortName, format, upc)` (Task 3) are called by `addPlatformCopy` (Task 5) and recorded by `FakeRepo.addedPlatforms` (Tasks 3, 5 test). `ScanState.Info(candidate, scannedPlatform, upc)` (Task 5) is rendered in Task 6. The FAB route guard (Task 7) matches the nav routes defined in `Nav.kt`.
- **Open verification at implementation:** confirm `HorizontalDivider` (Material 3) is the right divider composable for the installed Compose BOM (fall back to `Divider()` if the BOM predates it); confirm the `Scaffold` refactor in Task 7 compiles cleanly (the `entry`/`current` vals move into the `bottomBar` lambda). Both are caught by `assembleDebug`.
- **Cross-plan dependency:** Plan 3 consumes data the backend already returns (Plan 1, live-verified) and the web editor sets (Plan 2). Plan 3 can be implemented independently of Plan 2 — the format values it displays default sensibly from Plan 1's backfill even before Plan 2's editor exists.
```
