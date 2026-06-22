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
