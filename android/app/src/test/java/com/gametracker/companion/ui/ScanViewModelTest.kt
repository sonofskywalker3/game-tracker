package com.gametracker.companion.ui

import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.BarcodeConstituent
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

    @Test fun bundle_candidate_keeps_constituents_in_info() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("MMX", "upc_api",
            scannedPlatform = "Switch",
            candidates = listOf(BarcodeCandidate(
                igdbId = 500, title = "Mega Man X Legacy Collection", platform = "Switch",
                gameType = 3,
                constituents = listOf(
                    BarcodeConstituent(title = "Mega Man X", ownedGameId = 10,
                        ownedPlatforms = listOf(OwnedPlatform("SNES", "physical", 0))),
                    BarcodeConstituent(title = "Mega Man X2", ownedGameId = null))))))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("MMX"); advanceUntilIdle()
        val s = vm.state.value
        assertTrue(s is ScanState.Info)
        val cons = (s as ScanState.Info).candidate.constituents
        assertEquals(2, cons.size)
        assertEquals("Mega Man X", cons[0].title)
        assertEquals(10, cons[0].ownedGameId)
        assertEquals("SNES", cons[0].ownedPlatforms[0].shortName)
        assertEquals(null, cons[1].ownedGameId)
    }

    @Test fun reset_returns_to_scanning() = runTest {
        val repo = FakeRepo(resolveResp = BarcodeResolveResponse("0", "none"))
        val vm = ScanViewModel(repo.asRepository())
        vm.onBarcode("0"); advanceUntilIdle()
        vm.reset()
        assertTrue(vm.state.value is ScanState.Scanning)
    }
}
