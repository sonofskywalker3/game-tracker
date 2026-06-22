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
