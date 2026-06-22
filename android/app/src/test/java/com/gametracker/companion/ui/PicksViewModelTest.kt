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

    @Test fun searchLibrary_populates_picker_and_clears_below_two_chars() = runTest {
        val repo = FakeRepo(slotsResp = SlotsResponse(), gamesList = listOf(
            com.gametracker.companion.data.GameSummary(1, "Halo"),
            com.gametracker.companion.data.GameSummary(2, "Hades"),
        ))
        val vm = PicksViewModel(repo.asRepository())
        vm.searchLibrary("ha"); advanceUntilIdle()
        assertEquals(2, vm.picker.value.size)
        vm.searchLibrary("h"); advanceUntilIdle()   // below 2 chars -> cleared
        assertEquals(0, vm.picker.value.size)
    }
}
