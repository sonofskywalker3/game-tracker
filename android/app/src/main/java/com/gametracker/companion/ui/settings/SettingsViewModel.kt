package com.gametracker.companion.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class TestResult { Idle, Testing, Ok, Failed }

class SettingsViewModel(
    private val settings: SettingsStore,
    private val repository: Repository,
) : ViewModel() {

    val baseUrl: StateFlow<String> =
        settings.baseUrl.stateIn(viewModelScope, SharingStarted.Eagerly, settings.baseUrlBlocking())

    private val _testResult = MutableStateFlow(TestResult.Idle)
    val testResult: StateFlow<TestResult> = _testResult

    fun save(url: String) = viewModelScope.launch { settings.setBaseUrl(url) }

    fun test() = viewModelScope.launch {
        _testResult.value = TestResult.Testing
        _testResult.value = if (repository.games().isSuccess) TestResult.Ok else TestResult.Failed
    }
}
