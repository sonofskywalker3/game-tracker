package com.backlogquest.companion.ui

import com.backlogquest.companion.data.GameSummary
import com.backlogquest.companion.data.Repository
import com.backlogquest.companion.data.SettingsStore
import com.backlogquest.companion.ui.settings.LoginState
import com.backlogquest.companion.ui.settings.SettingsViewModel
import com.backlogquest.companion.ui.settings.TestResult
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
    val token = MutableStateFlow("")
    override val baseUrl: Flow<String> = state
    override fun baseUrlBlocking() = state.value
    override suspend fun setBaseUrl(url: String) { state.value = url.trimEnd('/') }
    override val authToken: Flow<String> = token
    override fun authTokenBlocking() = token.value
    override suspend fun setAuthToken(t: String) { token.value = t.trim() }
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

    @Test fun login_success_stores_token_and_sets_ok() = runTest {
        val settings = FakeSettings("http://h:5000")
        val vm = SettingsViewModel(settings, FakeRepo(reachable = true).asRepository())
        vm.login("pw"); advanceUntilIdle()
        assertEquals(LoginState.Ok, vm.loginState.value)
        assertEquals("fake-token-123", settings.token.value)
        assertEquals(true, vm.loggedIn.value)
    }

    @Test fun login_failure_sets_failed_and_stores_no_token() = runTest {
        val settings = FakeSettings("http://h:5000")
        val vm = SettingsViewModel(settings, FakeRepo(reachable = false).asRepository())
        vm.login("bad"); advanceUntilIdle()
        assertEquals(LoginState.Failed, vm.loginState.value)
        assertEquals("", settings.token.value)
    }

    @Test fun logout_clears_token() = runTest {
        val settings = FakeSettings("http://h:5000")
        settings.token.value = "existing"
        val vm = SettingsViewModel(settings, FakeRepo(true).asRepository())
        vm.logout(); advanceUntilIdle()
        assertEquals("", settings.token.value)
    }
}
