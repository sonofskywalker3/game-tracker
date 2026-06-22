package com.gametracker.companion.ui.library

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.data.Repository
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class LibraryViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<List<GameSummary>>>(UiState.Loading)
    val state: StateFlow<UiState<List<GameSummary>>> = _state

    private var search: String? = null
    private var status: String? = null
    private var platform: String? = null

    fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.games(status = status, platform = platform, search = search?.takeIf { it.length >= 2 }).fold(
            onSuccess = { _state.value = if (it.isEmpty()) UiState.Empty else UiState.Success(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun onSearch(q: String) { search = q; load() }
    fun setStatusFilter(s: String?) { status = s; load() }
    fun setPlatformFilter(p: String?) { platform = p; load() }
}
