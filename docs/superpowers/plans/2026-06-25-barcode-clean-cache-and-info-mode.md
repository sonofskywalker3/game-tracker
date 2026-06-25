# Barcode Cleaning Fix + Side-Effect-Free Resolve + Info Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the interactive scan path from poisoning the UPC registry, fix retail-title cleaning so confident matches resolve, and add a hands-free **Info mode** for registry-only continuous scanning.

**Architecture:** `resolve()` becomes read-only; a new `POST /api/barcode/link` is the single scan-side registry writer. `clean_product_title` strips publisher names + catalog/UPC digit runs. The Android `ScanViewModel` gains a decision tree (auto-link confident singles, picker for multiples, platform prompt when unknown) plus an Info-mode flag; the scan UI gets a toggle, picker, platform chips, and presence-based re-arm.

**Tech Stack:** Python 3 / Flask / sqlite (backend, `barcode.py`, `app.py`); Kotlin / Jetpack Compose / Retrofit / kotlinx.serialization (Android).

**Spec:** `docs/superpowers/specs/2026-06-25-barcode-clean-cache-and-info-mode-design.md`

## Global Constraints

- Package/env: `uv` only — run Python via `uv run`. Backend tests: `uv run python -m pytest` (plain `uv run pytest` fails: `ModuleNotFoundError: models`).
- Lint gate: `ruff check` only. **Never** run `ruff format` (codebase is hand-aligned).
- Android build/tests: `cd android && ./gradlew.bat <task>`. JVM unit tests: `:app:testDebugUnitTest`.
- Git: commit directly to `main` and push; no feature branches. Commit identity is the repo default (`sonofskywalker3`).
- "Complete"/cacheable = a candidate with a non-null `cover_url`. Coverless singles never auto-save.
- Catalog/UPC digit-strip floor: **5+ digits** (`\b\d{5,}\b`) — preserves `1942`, `FIFA 23`.
- Publisher noise list (extensible): `nintendo, sony, microsoft, sega, capcom, square enix, bandai namco, ubisoft, electronic arts, activision, konami, atlus`.
- A saved registry row **requires a platform** (parsed or user-selected).
- Presence re-arm debounce: **3** consecutive empty frames.
- Do not touch the live `games.db`; tests use the pytest `temp_db`/`client` fixtures only.

---

### Task 1: Clean publisher names + catalog/UPC numbers out of product titles

**Files:**
- Modify: `barcode.py` (`_RETAIL_NOISE_WORDS` region ~42-64, `clean_product_title` ~97-110)
- Test: `tests/test_api_barcode.py` (cleaning tests live here, lines 4-19)

**Interfaces:**
- Produces: `barcode.clean_product_title(raw: str | None) -> str` (signature unchanged; behavior extended).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api_barcode.py`:

```python
def test_clean_product_title_strips_publisher_and_catalog_numbers():
    cases = {
        "Super Mario 3D All-Stars Nintendo 045496596743": "Super Mario 3D All-Stars",
        "The Legend of Zelda: Link's Awakening 110249": "The Legend of Zelda: Link's Awakening",
        "Bravely Default II 045496596842": "Bravely Default II",
    }
    for raw, expected in cases.items():
        assert barcode.clean_product_title(raw) == expected


def test_clean_product_title_preserves_short_title_numbers():
    assert barcode.clean_product_title("1942") == "1942"
    assert barcode.clean_product_title("FIFA 23 (PS5)") == "FIFA 23"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_api_barcode.py::test_clean_product_title_strips_publisher_and_catalog_numbers -v`
Expected: FAIL (publisher word + digits not stripped).

- [ ] **Step 3: Implement** — in `barcode.py`, add the publisher tuple right after `_RETAIL_NOISE_WORDS` closes (after line 56), and combine it into the noise regex. Replace the `_NOISE_RE = re.compile(...)` definition (lines 61-64) so it is built from both tuples:

```python
# Standalone publisher/vendor words that UPC titles tack on ("... Nintendo 0454...").
# Stripped as whole words; extensible — add new publishers here.
_PUBLISHER_NOISE_WORDS: tuple[str, ...] = (
    "nintendo", "sony", "microsoft", "sega", "capcom", "square enix",
    "bandai namco", "ubisoft", "electronic arts", "activision", "konami", "atlus",
)
# Platform/packaging noise first (longest-first), then publishers.
_ALL_NOISE_WORDS: tuple[str, ...] = _RETAIL_NOISE_WORDS + _PUBLISHER_NOISE_WORDS
_NOISE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _ALL_NOISE_WORDS) + r")\b",
    re.IGNORECASE,
)
# Standalone catalog numbers / embedded UPCs (5+ digits). The floor preserves
# real title numbers like "1942" or "FIFA 23".
_CATALOG_NUM_RE = re.compile(r"\b\d{5,}\b")
```

Then in `clean_product_title`, add the catalog-number strip immediately after the noise strip. The pipeline becomes:

```python
    t = _BRACKETS_RE.sub(" ", raw)
    t = _VIDEO_GAME_RE.sub(" ", t)
    t = _NOISE_RE.sub(" ", t)
    t = _CATALOG_NUM_RE.sub(" ", t)
    t = _SEP_DASH_RE.sub(" ", t)                 # collapse separator dashes
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t.strip(" -–—:").strip()    # trim stray leading/trailing seps
```

- [ ] **Step 4: Run to verify pass** (and no regressions in the existing cleaning test)

Run: `uv run python -m pytest tests/test_api_barcode.py -k clean_product_title -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check barcode.py tests/test_api_barcode.py
git add barcode.py tests/test_api_barcode.py
git commit -m "fix(barcode): strip publisher names + 5+ digit catalog/UPC runs in title cleaning"
```

---

### Task 2: Make `resolve()` side-effect-free (the poisoning fix)

**Files:**
- Modify: `barcode.py` `resolve()` (remove the registry write at lines 343-352)
- Test: `tests/test_api_barcode.py` (flip `test_resolve_records_unmatched_scan`, line 177)

**Interfaces:**
- Produces: `barcode.resolve(conn, upc, *, client_id=None, token=None) -> dict` — same return shape, but **never writes** `barcode_registry`.

- [ ] **Step 1: Flip the existing test + add a single-match no-write test.** In `tests/test_api_barcode.py`, replace `test_resolve_records_unmatched_scan` (lines 177-190) with:

```python
def test_resolve_records_nothing_for_unmatched_scan(client, monkeypatch):
    import models
    monkeypatch.setattr(barcode, "lookup_product_title",
                        lambda upc: "Totally Unknown Game (Nintendo Switch)")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [])
    resp = client.get("/api/barcode/resolve?upc=NEW123")
    assert resp.get_json()["scanned_platform"] == "Switch"
    conn = models.get_db()
    row = barcode.registry_get(conn, "NEW123")
    count = conn.execute("SELECT COUNT(*) FROM barcode_registry").fetchone()[0]
    conn.close()
    assert row is None        # resolve must not poison the registry
    assert count == 0


def test_resolve_single_match_writes_nothing(client, monkeypatch):
    import models
    monkeypatch.setattr(barcode, "lookup_product_title", lambda upc: "Celeste (PS5)")
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES", (barcode.lookup_product_title,))
    monkeypatch.setattr(barcode.igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1, "name": "Celeste", "platforms": [167], "cover_url": "c",
         "source": "search", "score": 99, "game_type": 0}])
    monkeypatch.setattr(barcode.igdb_match, "short_names_for", lambda ids: ["PS5"])
    client.get("/api/barcode/resolve?upc=NOWRITE1")
    conn = models.get_db()
    count = conn.execute("SELECT COUNT(*) FROM barcode_registry").fetchone()[0]
    conn.close()
    assert count == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_api_barcode.py -k "writes_nothing or records_nothing" -v`
Expected: FAIL (resolve still writes a row).

- [ ] **Step 3: Implement** — in `barcode.py` `resolve()`, delete the write block (lines 343-352): the comment `# Record EVERY scan ...`, the `registry_put(...)` call, and the `conn.commit()`. Keep the candidate-building loop above and the `return` statements below (the `if not candidates:` / final returns at lines 354-360) exactly as-is. After the edit, the code between the bundle-constituent loop and `if not candidates:` is just the `top`/return logic with no registry write.

- [ ] **Step 4: Run to verify pass + full barcode suites**

Run: `uv run python -m pytest tests/test_api_barcode.py tests/test_barcode.py -v`
Expected: PASS (cache-hit reads still work; no resolve writes).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check barcode.py tests/test_api_barcode.py
git add barcode.py tests/test_api_barcode.py
git commit -m "fix(barcode): resolve() is read-only; never caches incomplete/unmatched scans"
```

---

### Task 3: `POST /api/barcode/link` — the single scan-side registry writer

**Files:**
- Modify: `app.py` (add route near `api_barcode_resolve`, ~line 2300)
- Test: `tests/test_api_barcode.py`

**Interfaces:**
- Produces: `POST /api/barcode/link` body `{upc, igdb_id?, title?, cover_url?, platform, game_id?}` → `200 {"ok": true}` or `400 {"error": ...}`. Writes via `barcode.registry_put` (idempotent upsert on `upc`).

- [ ] **Step 1: Write failing tests** — append to `tests/test_api_barcode.py`:

```python
def test_link_writes_registry_row(client):
    import models
    resp = client.post("/api/barcode/link", json={
        "upc": "L1", "igdb_id": 7, "title": "Celeste",
        "cover_url": "http://x/c.jpg", "platform": "Switch"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    conn = models.get_db()
    row = barcode.registry_get(conn, "L1")
    conn.close()
    assert row["game_id"] is None and row["igdb_id"] == 7
    assert row["platform"] == "Switch" and row["cover_url"] == "http://x/c.jpg"


def test_link_requires_upc_and_platform(client):
    assert client.post("/api/barcode/link", json={"platform": "Switch"}).status_code == 400
    assert client.post("/api/barcode/link", json={"upc": "X"}).status_code == 400


def test_link_idempotent_and_multi_upc_per_game(client):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id, title, normalized_title) VALUES (4,'G','g')")
    conn.commit()
    conn.close()
    client.post("/api/barcode/link", json={"upc": "A", "platform": "Switch", "game_id": 4})
    client.post("/api/barcode/link", json={"upc": "A", "platform": "Switch", "game_id": 4})
    client.post("/api/barcode/link", json={"upc": "B", "platform": "PS5", "game_id": 4})
    conn = models.get_db()
    upcs = {r["upc"] for r in barcode.registry_upcs_for_game(conn, 4)}
    conn.close()
    assert upcs == {"A", "B"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/test_api_barcode.py -k link -v`
Expected: FAIL (404 — route undefined).

- [ ] **Step 3: Implement** — in `app.py`, add immediately after the `api_barcode_resolve` function (after line 2300):

```python
@app.route('/api/barcode/link', methods=['POST'])
def api_barcode_link():
    """Record a confirmed UPC -> game mapping in the registry (no library write).

    The single scan-side registry writer; resolve() is read-only. Idempotent
    upsert on UPC. game_id is optional (knowledge without ownership)."""
    data = request.get_json(silent=True) or {}
    upc = (data.get('upc') or '').strip()
    platform = (data.get('platform') or '').strip() or None
    if not upc or not platform:
        return jsonify({'error': 'upc and platform required'}), 400
    conn = get_db()
    barcode.registry_put(conn, upc, igdb_id=data.get('igdb_id'),
                         title=data.get('title'), platform=platform,
                         cover_url=data.get('cover_url'), game_id=data.get('game_id'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/test_api_barcode.py -k link -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app.py tests/test_api_barcode.py
git add app.py tests/test_api_barcode.py
git commit -m "feat(barcode): POST /api/barcode/link records confirmed UPC->game (registry-only)"
```

---

### Task 4: Android link plumbing (DTO + API + Repository + FakeRepo)

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/data/GameTrackerApi.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/data/Repository.kt`
- Modify: `android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt` (existing file)

**Interfaces:**
- Produces: `Repository.link(upc, igdbId, title, coverUrl, platform, gameId): Result<Unit>`; `BarcodeLinkBody`; `GameTrackerApi.linkBarcode(body)`; `FakeRepo.linked: MutableList<BarcodeLinkBody>`.

- [ ] **Step 1: Add the DTO + API method.** In `GameTrackerApi.kt`, add the body class next to `AddPlatformBody` (after line 23):

```kotlin
@Serializable data class BarcodeLinkBody(
    val upc: String,
    @SerialName("igdb_id") val igdbId: Int? = null,
    val title: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    val platform: String,
    @SerialName("game_id") val gameId: Int? = null,
)
```

Add the `SerialName` import at the top (after line 3): `import kotlinx.serialization.SerialName`. Then add to the `interface GameTrackerApi` (after the `createGame` method, line 50):

```kotlin
    @POST("api/barcode/link")
    suspend fun linkBarcode(@Body body: BarcodeLinkBody)
```

- [ ] **Step 2: Add the Repository method.** In `Repository.kt`, after `addPlatform` (line 41):

```kotlin
    suspend fun link(upc: String, igdbId: Int?, title: String?, coverUrl: String?,
                     platform: String, gameId: Int?): Result<Unit> =
        runCatching { api.linkBarcode(BarcodeLinkBody(upc, igdbId, title, coverUrl, platform, gameId)) }
```

- [ ] **Step 3: Extend FakeRepo + write the failing test.** In `FakeRepo.kt`, add a tracking list (after line 20): `val linked = mutableListOf<BarcodeLinkBody>()`, and implement the stub method inside the `api` object (after `resolveBarcode`, line 40):

```kotlin
        override suspend fun linkBarcode(body: BarcodeLinkBody) {
            linked += body
            if (!reachable) throw RuntimeException("unreachable")
        }
```

Add to `RepositoryTest.kt` a test (match the file's existing style):

```kotlin
    @Test fun link_posts_barcode_link_body() = kotlinx.coroutines.test.runTest {
        val repo = FakeRepo()
        repo.asRepository().link("U1", 7, "Celeste", "cov", "Switch", null)
        val body = repo.linked.single()
        org.junit.Assert.assertEquals("U1", body.upc)
        org.junit.Assert.assertEquals(7, body.igdbId)
        org.junit.Assert.assertEquals("Switch", body.platform)
        org.junit.Assert.assertEquals(null, body.gameId)
    }
```

- [ ] **Step 4: Build + run the unit tests**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest --tests "*RepositoryTest*"`
Expected: PASS (compiles; new test green).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data/GameTrackerApi.kt android/app/src/main/java/com/gametracker/companion/data/Repository.kt android/app/src/test/java/com/gametracker/companion/ui/FakeRepo.kt android/app/src/test/java/com/gametracker/companion/data/RepositoryTest.kt
git commit -m "feat(android): Repository.link + /api/barcode/link client plumbing"
```

---

### Task 5: `PresenceReArmGate` — pure re-arm helper

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/ui/scan/PresenceReArmGate.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/PresenceReArmGateTest.kt`

**Interfaces:**
- Produces: `class PresenceReArmGate(threshold: Int = 3)` with `fun onFrame(barcodePresent: Boolean): Boolean` (true ⇒ time to re-arm) and `fun reset()`.

- [ ] **Step 1: Write the failing test** — create `PresenceReArmGateTest.kt`:

```kotlin
package com.gametracker.companion.ui

import com.gametracker.companion.ui.scan.PresenceReArmGate
import org.junit.Assert.*
import org.junit.Test

class PresenceReArmGateTest {
    @Test fun rearms_only_after_threshold_empty_frames() {
        val gate = PresenceReArmGate(threshold = 3)
        assertFalse(gate.onFrame(barcodePresent = true))   // held in frame
        assertFalse(gate.onFrame(false))                   // 1 empty
        assertFalse(gate.onFrame(false))                   // 2 empty
        assertTrue(gate.onFrame(false))                    // 3 empty -> re-arm
    }

    @Test fun a_present_frame_resets_the_empty_run() {
        val gate = PresenceReArmGate(threshold = 3)
        gate.onFrame(false); gate.onFrame(false)
        assertFalse(gate.onFrame(true))    // item back in view resets
        assertFalse(gate.onFrame(false))   // only 1 empty again
    }

    @Test fun reset_clears_counter() {
        val gate = PresenceReArmGate(threshold = 2)
        gate.onFrame(false)
        gate.reset()
        assertFalse(gate.onFrame(false))   // back to 1, not 2
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest --tests "*PresenceReArmGateTest*"`
Expected: FAIL (class does not exist).

- [ ] **Step 3: Implement** — create `PresenceReArmGate.kt`:

```kotlin
package com.gametracker.companion.ui.scan

/** Gates scanner re-arm on barcode *absence*: after a scan fires, re-arm only
 *  once the item has left the frame for [threshold] consecutive frames. A
 *  short debounce avoids re-arming on ML Kit's momentary detection gaps. */
class PresenceReArmGate(private val threshold: Int = 3) {
    private var emptyFrames = 0

    /** Feed one analyzer frame; returns true when it is time to re-arm. */
    fun onFrame(barcodePresent: Boolean): Boolean {
        if (barcodePresent) { emptyFrames = 0; return false }
        emptyFrames++
        return emptyFrames >= threshold
    }

    fun reset() { emptyFrames = 0 }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest --tests "*PresenceReArmGateTest*"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/scan/PresenceReArmGate.kt android/app/src/test/java/com/gametracker/companion/ui/PresenceReArmGateTest.kt
git commit -m "feat(android): PresenceReArmGate for absence-based scanner re-arm"
```

---

### Task 6: `ScanViewModel` decision tree + Info-mode flag

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanViewModel.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/ScanViewModelTest.kt`

**Interfaces:**
- Consumes: `Repository.link(...)` (Task 4); `BarcodeResolveResponse`, `BarcodeCandidate`.
- Produces: new `ScanState` variants `Picker(candidates, upc, scannedPlatform)`, `NeedsPlatform(candidate, upc)`, `Linked(title, platform)`; `ScanViewModel.infoMode: StateFlow<Boolean>`, `setInfoMode(on)`, `pick(c, upc, scannedPlatform)`, `choosePlatform(c, platform, upc)`. `Info`/`NoMatch`/`Added`/`reset` unchanged.

- [ ] **Step 1: Update the two existing tests that use coverless candidates, and add the decision-tree tests.** In `ScanViewModelTest.kt`:

In `resolve_with_candidate_becomes_info` (line 20-30), give the candidate a cover so it stays a single confident match: change the candidate to `BarcodeCandidate(igdbId = 1, title = "Halo", platform = "Switch", coverUrl = "u")`.

In `bundle_candidate_keeps_constituents_in_info` (line 69-89), add `coverUrl = "u"` to the top `BarcodeCandidate(igdbId = 500, title = "Mega Man X Legacy Collection", platform = "Switch", gameType = 3, coverUrl = "u", constituents = ...)`.

Then append these tests:

```kotlin
    @Test fun multiple_candidates_becomes_picker() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            scannedPlatform = "Switch", candidates = listOf(
                BarcodeCandidate(igdbId = 1, title = "NieR", platform = "Switch", coverUrl = "a"),
                BarcodeCandidate(igdbId = 2, title = "NieR Replicant", platform = "Switch", coverUrl = "b"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("711"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.Picker)
        assertEquals(2, (s as ScanState.Picker).candidates.size)
    }

    @Test fun confident_single_in_info_mode_links_and_shows_linked() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            scannedPlatform = "Switch",
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo",
                platform = "Switch", coverUrl = "u"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.setInfoMode(true)
        vm.onBarcode("711"); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.Linked)
        val body = repo.linked.single()
        assertEquals("711", body.upc); assertEquals("Switch", body.platform)
    }

    @Test fun confident_single_in_normal_mode_shows_info_and_records() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            scannedPlatform = "Switch",
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo",
                platform = "Switch", coverUrl = "u"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("711"); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.Info)
        assertEquals(1, repo.linked.size)   // knowledge still recorded
    }

    @Test fun coverless_single_does_not_autosave() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            scannedPlatform = "Switch",
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo", platform = "Switch"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.setInfoMode(true)
        vm.onBarcode("711"); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.Picker)   // forced to confirm
        assertTrue(repo.linked.isEmpty())
    }

    @Test fun unknown_platform_prompts_then_links() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "upc_api",
            scannedPlatform = null,
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo",
                platform = null, coverUrl = "u"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.setInfoMode(true)
        vm.onBarcode("711"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.NeedsPlatform)
        vm.choosePlatform((s as ScanState.NeedsPlatform).candidate, "PS5", s.upc); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.Linked)
        assertEquals("PS5", repo.linked.single().platform)
    }

    @Test fun cache_hit_does_not_relink() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("711", "cache",
            scannedPlatform = "Switch",
            candidates = listOf(BarcodeCandidate(igdbId = 1, title = "Halo",
                platform = "Switch", coverUrl = "u"))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("711"); advanceUntilIdle()
        assertTrue(vm.state.value is ScanState.Info)
        assertTrue(repo.linked.isEmpty())   // already cached, no re-link
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest --tests "*ScanViewModelTest*"`
Expected: FAIL (new states/methods undefined; updated tests reference them).

- [ ] **Step 3: Implement** — replace the body of `ScanViewModel.kt` with:

```kotlin
package com.gametracker.companion.ui.scan

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.BarcodeResolveResponse
import com.gametracker.companion.data.Repository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ScanState {
    data object Scanning : ScanState
    data object Resolving : ScanState
    data class Info(val candidate: BarcodeCandidate, val scannedPlatform: String?,
                    val upc: String) : ScanState
    data class Picker(val candidates: List<BarcodeCandidate>, val upc: String,
                      val scannedPlatform: String?) : ScanState
    data class NeedsPlatform(val candidate: BarcodeCandidate, val upc: String) : ScanState
    data class Linked(val title: String?, val platform: String) : ScanState
    data class NoMatch(val upc: String, val productTitle: String?) : ScanState
    data class Error(val message: String) : ScanState
    data class Added(val gameId: Int?) : ScanState
}

class ScanViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<ScanState>(ScanState.Scanning)
    val state: StateFlow<ScanState> = _state

    private val _infoMode = MutableStateFlow(false)
    val infoMode: StateFlow<Boolean> = _infoMode
    fun setInfoMode(on: Boolean) { _infoMode.value = on }

    fun onBarcode(upc: String) = viewModelScope.launch {
        _state.value = ScanState.Resolving
        repository.resolveBarcode(upc).fold(
            onSuccess = { r -> route(r, upc) },
            onFailure = { _state.value = ScanState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    private fun route(r: BarcodeResolveResponse, upc: String) {
        val cands = r.candidates
        val fromCache = r.source == "cache"
        when {
            cands.isEmpty() -> _state.value = ScanState.NoMatch(upc, r.productTitle)
            cands.size > 1 -> _state.value = ScanState.Picker(cands, upc, r.scannedPlatform)
            else -> routeSingle(cands[0], r.scannedPlatform, upc, link = !fromCache)
        }
    }

    // Auto path for exactly one candidate: coverless => force a confirm (picker),
    // unknown platform => prompt, otherwise commit (linking unless it came from cache).
    private fun routeSingle(c: BarcodeCandidate, scannedPlatform: String?, upc: String,
                            link: Boolean) {
        val platform = c.platform ?: scannedPlatform
        when {
            c.coverUrl == null -> _state.value = ScanState.Picker(listOf(c), upc, scannedPlatform)
            platform == null -> _state.value = ScanState.NeedsPlatform(c, upc)
            else -> commit(c, platform, upc, link)
        }
    }

    /** User chose a candidate from the picker. */
    fun pick(c: BarcodeCandidate, upc: String, scannedPlatform: String?) {
        val platform = c.platform ?: scannedPlatform
        if (platform == null) _state.value = ScanState.NeedsPlatform(c, upc)
        else commit(c, platform, upc, link = true)
    }

    /** User chose a platform for a candidate that resolved without one. */
    fun choosePlatform(c: BarcodeCandidate, platform: String, upc: String) =
        commit(c, platform, upc, link = true)

    private fun commit(c: BarcodeCandidate, platform: String, upc: String, link: Boolean) {
        if (link) viewModelScope.launch {
            repository.link(upc, c.igdbId, c.title, c.coverUrl, platform, c.ownedGameId)
        }
        _state.value = if (_infoMode.value) ScanState.Linked(c.title, platform)
                       else ScanState.Info(c, platform, upc)
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

- [ ] **Step 4: Run to verify pass (full ScanViewModel suite)**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest --tests "*ScanViewModelTest*"`
Expected: PASS (original + 6 new tests).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/scan/ScanViewModel.kt android/app/src/test/java/com/gametracker/companion/ui/ScanViewModelTest.kt
git commit -m "feat(android): scan decision tree (picker/platform/auto-link) + Info-mode flag"
```

---

### Task 7: `ScanScreen` UI — Info-mode toggle, picker, platform chips, presence re-arm

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt`

**Interfaces:**
- Consumes: `ScanViewModel` states + `infoMode`/`setInfoMode`/`pick`/`choosePlatform` (Task 6); `PresenceReArmGate` (Task 5).
- Produces: no new symbols; this is UI wiring. Validated by build + on-device smoke (the project has no Compose UI tests).

- [ ] **Step 1: Add a common-platform constant + the Info-mode toggle + picker/platform/linked rendering.** At the top of `ScanScreen.kt` (after `REARM_MS`, line 35) add:

```kotlin
// Offered when a scan can't determine the platform (extensible).
private val COMMON_PLATFORMS = listOf("Switch", "PS5", "PS4", "Xbox", "PC", "3DS", "WiiU", "Wii")
```

In the `ScanScreen` composable, read the toggle state near the other state (after line 54):

```kotlin
    val infoMode = vm.infoMode.collectAsState().value
```

Add an Info-mode toggle as a top overlay (inside the outer `Box`, before the `when (val s = state)` block):

```kotlin
        Row(Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
            Text("Info mode", style = MaterialTheme.typography.labelLarge)
            Spacer(Modifier.width(8.dp))
            Switch(checked = infoMode, onCheckedChange = { vm.setInfoMode(it) })
        }
```

Add the new `when` branches alongside the existing ones (Info/NoMatch/Added/Error keep working; in Info mode, hide the library-action buttons):

```kotlin
            is ScanState.Picker -> ResultCard(onDismiss = ::rescan) {
                Text("Which one?", style = MaterialTheme.typography.titleSmall)
                s.candidates.forEach { c ->
                    Row(Modifier.fillMaxWidth().clickable {
                        vm.pick(c, s.upc, s.scannedPlatform)
                    }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        CoverImage(c.coverUrl, c.title ?: "", Modifier.width(40.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(c.title ?: "Unknown", Modifier.weight(1f))
                    }
                }
            }
            is ScanState.NeedsPlatform -> ResultCard(onDismiss = ::rescan) {
                Text("${s.candidate.title ?: "This game"} — which platform?",
                    style = MaterialTheme.typography.titleSmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    COMMON_PLATFORMS.forEach { p ->
                        AssistChip(onClick = { vm.choosePlatform(s.candidate, p, s.upc) },
                            label = { Text(p) })
                    }
                }
            }
            is ScanState.Linked -> ResultCard(onDismiss = ::rescan) {
                Text("Saved ✓  ${s.title ?: ""} (${s.platform})")
            }
```

In the existing `is ScanState.Info` branch, wrap the library-action buttons so they only show in normal mode — pass `infoMode` into `ScanInfo` and guard the `Button`/`onAdd`/`onAddCopy` block with `if (!infoMode) { ... }` (keep the cover + title + owned text always visible). Add `@OptIn(ExperimentalLayoutApi::class)` to the file for `FlowRow`, and import `androidx.compose.foundation.layout.FlowRow`, `androidx.compose.material3.AssistChip`, `androidx.compose.material3.Switch`.

- [ ] **Step 2: Build to verify it compiles**

Run: `cd android && ./gradlew.bat :app:assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Wire presence-based re-arm (Info mode).** Add remembered state + gate after `fired` (line 42):

```kotlin
    var barcodePresent by remember { mutableStateOf(false) }
    val reArmGate = remember { PresenceReArmGate() }
```

In the analyzer success listener (lines 74-77), set presence each frame and only fire when armed:

```kotlin
                            .addOnSuccessListener { codes ->
                                val hit = codes.firstOrNull { it.format in PRODUCT_FORMATS }?.rawValue
                                barcodePresent = hit != null
                                if (hit != null && !fired) { fired = true; vm.onBarcode(hit) }
                            }
```

Replace the hands-free re-arm `LaunchedEffect` (lines 57-58) so Info mode re-arms on absence instead of a fixed delay:

```kotlin
    // Re-arm: Info mode waits until the item leaves the frame (presence gate);
    // normal mode keeps the timed re-arm after an add.
    LaunchedEffect(state, barcodePresent, infoMode) {
        val terminal = state is ScanState.Linked || state is ScanState.Info ||
            state is ScanState.NoMatch || state is ScanState.Added
        if (infoMode && terminal && reArmGate.onFrame(barcodePresent)) { reArmGate.reset(); rescan() }
        else if (!infoMode && state is ScanState.Added) { delay(REARM_MS); rescan() }
    }
```

Import `PresenceReArmGate` (`com.gametracker.companion.ui.scan.PresenceReArmGate` — same package, no import needed) and ensure `getValue`/`setValue`/`mutableStateOf` delegates are imported (already via `androidx.compose.runtime.*`).

- [ ] **Step 4: Build + install + on-device smoke**

```bash
cd android && ./gradlew.bat :app:installDebug
```
Smoke (per spec deferred on-device test): with the phone on the VPN, open Scan, flip **Info mode** on, scan a known game → it shows "Saved ✓" and re-arms when you pull the box away; scan a multi-match (e.g. NieR) → picker appears; scan something with no parsed platform → platform chips appear; scan an unknown → manual search opens. Confirm no "Add to library" buttons appear while Info mode is on.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt
git commit -m "feat(android): Info-mode toggle, candidate picker, platform chips, presence re-arm"
```

---

### Task 8: Info-mode manual-link path (not-found → link without library write)

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/add/AddViewModel.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/add/AddScreen.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt` (only the `onManualSearch` param type + its `NoMatch`-branch call site)
- Test: `android/app/src/test/java/com/gametracker/companion/ui/AddViewModelTest.kt` (existing)

**Interfaces:**
- Consumes: `Repository.link(...)` (Task 4); `ScanState.NoMatch` route via `onManualSearch`.
- Produces: `AddViewModel.linkInfo(result, platform, upc): Result<Unit>`; `AddScreen(initialQuery, pendingUpc, infoMode, onOpenGame, onLinked)`; Nav `add` route gains an `info` arg.

- [ ] **Step 1: Add `linkInfo` + a failing test.** In `AddViewModel.kt`, after `add(...)` (line 33):

```kotlin
    /** Info mode: record UPC -> chosen IGDB game in the registry, no library add.
     *  Search results carry no numeric igdb_id, so we link by title/cover/platform. */
    suspend fun linkInfo(result: IgdbResult, platform: String, upc: String): Result<Unit> =
        repository.link(upc, igdbId = null, title = result.name,
                        coverUrl = result.coverUrl, platform = platform, gameId = null)
```

In `AddViewModelTest.kt`, add (match the file's existing FakeRepo + runTest style):

```kotlin
    @Test fun linkInfo_records_registry_without_creating_game() = kotlinx.coroutines.test.runTest {
        val repo = FakeRepo()
        val vm = com.gametracker.companion.ui.add.AddViewModel(repo.asRepository())
        vm.linkInfo(com.gametracker.companion.data.IgdbResult(name = "Tunic", coverUrl = "c"),
            platform = "Switch", upc = "U9")
        org.junit.Assert.assertTrue(repo.created.isEmpty())          // no library write
        val body = repo.linked.single()
        org.junit.Assert.assertEquals("Tunic", body.title)
        org.junit.Assert.assertEquals("Switch", body.platform)
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest --tests "*AddViewModelTest*"`
Expected: FAIL (`linkInfo` undefined).

- [ ] **Step 3: Implement the `linkInfo` consumption in `AddScreen` + thread the flag through `Nav`.**

In `AddScreen.kt`, change the signature (line 23) to `fun AddScreen(initialQuery: String?, pendingUpc: String?, infoMode: Boolean, onOpenGame: (Int) -> Unit, onLinked: () -> Unit)`. In `commit(...)` (lines 32-39), branch on `infoMode`:

```kotlin
    fun commit(result: IgdbResult, platforms: List<String>, physical: Boolean) {
        scope.launch {
            if (infoMode) {
                val platform = platforms.firstOrNull() ?: result.platforms.firstOrNull() ?: ""
                vm.linkInfo(result, platform, pendingUpc ?: "")
                pendingAdd = null
                onLinked()
            } else {
                val gid = vm.add(result, platforms = platforms, physical = physical, upc = pendingUpc)
                pendingAdd = null
                if (gid != null) onOpenGame(gid)
                else snackbar.showSnackbar("Couldn't add — it may already be in your library")
            }
        }
    }
```

In `Nav.kt`, add an `info` arg to the `add` route (lines 75-87): add `androidx.navigation.navArgument("info") { defaultValue = "false" }` to the argument list and `&info={info}` to the route string, then read it and pass through:

```kotlin
                com.gametracker.companion.ui.add.AddScreen(
                    initialQuery = entry.arguments?.getString("prefill"),
                    pendingUpc = entry.arguments?.getString("upc"),
                    infoMode = entry.arguments?.getString("info") == "true",
                    onOpenGame = { id -> nav.navigate("detail/$id?added=true") },
                    onLinked = { nav.popBackStack() },
                )
```

In the `scan` composable's `onManualSearch` (lines 111-116), forward Info mode. Change `ScanScreen`'s `onManualSearch` lambda to include the flag by reading it off the view model is not possible from Nav; instead pass it through the navigation route built in `ScanScreen`. Simplest: have `onManualSearch` accept the current info flag. Update `ScanScreen`'s `onManualSearch` call site (Task 7 file) to `onManualSearch(s.productTitle, s.upc, infoMode)` and the `ScanScreen` parameter type to `(String?, String, Boolean) -> Unit`; then in `Nav.kt`:

```kotlin
                    onManualSearch = { productTitle, upc, info ->
                        val q = productTitle?.let { java.net.URLEncoder.encode(it, "UTF-8") } ?: ""
                        nav.navigate("add?prefill=$q&upc=$upc&info=$info") {
                            popUpTo("scan") { inclusive = true }
                        }
                    },
```

- [ ] **Step 4: Build + run unit tests**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest && ./gradlew.bat :app:assembleDebug`
Expected: PASS + BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/add/AddViewModel.kt android/app/src/main/java/com/gametracker/companion/ui/add/AddScreen.kt android/app/src/main/java/com/gametracker/companion/ui/Nav.kt android/app/src/main/java/com/gametracker/companion/ui/scan/ScanScreen.kt android/app/src/test/java/com/gametracker/companion/ui/AddViewModelTest.kt
git commit -m "feat(android): Info-mode manual link records registry without a library add"
```

---

### Task 9: Full-suite verification + push

- [ ] **Step 1: Backend suite**

Run: `uv run python -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Android suite + assemble**

Run: `cd android && ./gradlew.bat :app:testDebugUnitTest :app:assembleDebug`
Expected: all pass; BUILD SUCCESSFUL.

- [ ] **Step 3: Lint**

Run: `uv run ruff check barcode.py app.py tests/test_api_barcode.py`
Expected: clean.

- [ ] **Step 4: Push**

```bash
git push origin main
```

- [ ] **Step 5: Install the new build to the phone**

Run: `cd android && ./gradlew.bat :app:installDebug`
Then run the on-device Info-mode smoke from Task 7 Step 4.

---

## Notes / deliberate scope calls (flag for owner)

- **Info-mode toggle is in-memory** (ScanViewModel), not DataStore-persisted as the spec's §C1 suggested. It resets to off when you leave the scan screen — fine for a continuous store run, and avoids DataStore plumbing for v1. Say so if you want it persisted; it's a small follow-up (add a boolean to `SettingsStore`).
- **Manual links carry no `igdb_id`** (IGDB *search* results expose no numeric id in the current DTO), so an Info-mode manual link records title+cover+platform with `igdb_id` NULL. That's still a complete, user-confirmed registry row.
- **Platform chip list is a fixed common set** (`COMMON_PLATFORMS`); extend if you scan platforms outside it.
