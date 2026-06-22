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
