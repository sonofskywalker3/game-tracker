package com.backlogquest.companion.ui

import com.backlogquest.companion.data.IgdbResult
import com.backlogquest.companion.ui.add.AddViewModel
import com.backlogquest.companion.ui.common.UiState
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

    @Test fun add_uses_chosen_platforms_not_all_igdb_platforms() = runTest {
        val repo = FakeRepo(igdb = emptyList())
        val vm = AddViewModel(repo.asRepository())
        // IGDB lists three platforms; the owner only owns PC.
        vm.add(
            IgdbResult(name = "Jedi Outcast", platforms = listOf("PC", "Xbox", "GC")),
            platforms = listOf("PC"),
        )
        advanceUntilIdle()
        assertEquals(listOf("PC"), repo.created.single().platforms)
    }

    @Test fun linkInfo_records_registry_without_creating_game() = kotlinx.coroutines.test.runTest {
        val repo = FakeRepo()
        val vm = com.backlogquest.companion.ui.add.AddViewModel(repo.asRepository())
        vm.linkInfo(com.backlogquest.companion.data.IgdbResult(name = "Tunic", coverUrl = "c"),
            platform = "Switch", upc = "U9")
        org.junit.Assert.assertTrue(repo.created.isEmpty())          // no library write
        val body = repo.linked.single()
        org.junit.Assert.assertEquals("Tunic", body.title)
        org.junit.Assert.assertEquals("Switch", body.platform)
    }
}
