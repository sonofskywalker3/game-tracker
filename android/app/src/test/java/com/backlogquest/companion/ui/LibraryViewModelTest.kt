package com.backlogquest.companion.ui

import com.backlogquest.companion.data.GameSummary
import com.backlogquest.companion.ui.common.UiState
import com.backlogquest.companion.ui.library.LibraryViewModel
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
        advanceUntilIdle()   // init { load() }
        val s = vm.state.value
        assertTrue(s is UiState.Success)
        assertEquals(3, (s as UiState.Success).data.size)
    }

    @Test fun load_empty_when_no_games() = runTest {
        val vm = LibraryViewModel(FakeRepo(gamesList = emptyList()).asRepository())
        advanceUntilIdle()
        assertEquals(UiState.Empty, vm.state.value)
    }

    @Test fun error_when_unreachable() = runTest {
        val vm = LibraryViewModel(FakeRepo(reachable = false).asRepository())
        advanceUntilIdle()
        assertTrue(vm.state.value is UiState.Error)
    }

    @Test fun search_requeries() = runTest {
        val vm = LibraryViewModel(FakeRepo(gamesList = games(1)).asRepository())
        advanceUntilIdle()
        vm.onSearch("hal"); advanceUntilIdle()   // debounced reload
        assertEquals("hal", vm.query.value)
        assertTrue(vm.state.value is UiState.Success)
    }
}
