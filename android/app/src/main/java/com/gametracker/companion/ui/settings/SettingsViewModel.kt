package com.gametracker.companion.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class TestResult { Idle, Testing, Ok, Failed }
enum class LoginState { Idle, LoggingIn, Ok, Failed }

class SettingsViewModel(
    private val settings: SettingsStore,
    private val repository: Repository,
) : ViewModel() {

    val baseUrl: StateFlow<String> =
        settings.baseUrl.stateIn(viewModelScope, SharingStarted.Eagerly, settings.baseUrlBlocking())

    /** True once a bearer token is stored (i.e. signed in). */
    val loggedIn: StateFlow<Boolean> =
        settings.authToken.map { it.isNotEmpty() }
            .stateIn(viewModelScope, SharingStarted.Eagerly, settings.authTokenBlocking().isNotEmpty())

    private val _testResult = MutableStateFlow(TestResult.Idle)
    val testResult: StateFlow<TestResult> = _testResult

    private val _loginState = MutableStateFlow(LoginState.Idle)
    val loginState: StateFlow<LoginState> = _loginState

    fun save(url: String) = viewModelScope.launch { settings.setBaseUrl(url) }

    fun test() = viewModelScope.launch {
        _testResult.value = TestResult.Testing
        _testResult.value = if (repository.games().isSuccess) TestResult.Ok else TestResult.Failed
    }

    /** Exchange the password for a token and persist it; the auth interceptor
     *  then signs every subsequent request. */
    fun login(password: String) = viewModelScope.launch {
        _loginState.value = LoginState.LoggingIn
        val result = repository.login(password)
        if (result.isSuccess) {
            settings.setAuthToken(result.getOrThrow())
            _loginState.value = LoginState.Ok
        } else {
            _loginState.value = LoginState.Failed
        }
    }

    fun logout() = viewModelScope.launch {
        settings.setAuthToken("")
        _loginState.value = LoginState.Idle
    }
}
