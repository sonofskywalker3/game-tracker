package com.backlogquest.companion.ui.library

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.backlogquest.companion.data.GameSummary
import com.backlogquest.companion.data.Repository
import com.backlogquest.companion.ui.common.UiState
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class LibraryViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<List<GameSummary>>>(UiState.Loading)
    val state: StateFlow<UiState<List<GameSummary>>> = _state

    // Filters live in the ViewModel so they (and the list + scroll) survive navigating
    // to Detail and back — the screen observes these instead of holding local state.
    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query
    private val _status = MutableStateFlow<String?>(null)
    val status: StateFlow<String?> = _status
    private val _platform = MutableStateFlow<String?>(null)
    val platform: StateFlow<String?> = _platform

    // Accumulated set of platforms seen across loads — the platform dropdown's options.
    private val _platforms = MutableStateFlow<List<String>>(emptyList())
    val platforms: StateFlow<List<String>> = _platforms

    private var searchJob: Job? = null

    init { load() }

    private fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.games(
            status = _status.value,
            platform = _platform.value,
            search = _query.value.takeIf { it.length >= 2 },
        ).fold(
            onSuccess = { list ->
                _platforms.value = (_platforms.value + list.flatMap { it.platforms }).distinct().sorted()
                _state.value = if (list.isEmpty()) UiState.Empty else UiState.Success(list)
            },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach BacklogQuest") },
        )
    }

    fun onSearch(q: String) {
        if (q == _query.value) return
        _query.value = q
        searchJob?.cancel()
        searchJob = viewModelScope.launch { delay(300); load() }   // debounce
    }

    fun setStatusFilter(s: String?) { _status.value = s; load() }
    fun setPlatformFilter(p: String?) { _platform.value = p; load() }
}
