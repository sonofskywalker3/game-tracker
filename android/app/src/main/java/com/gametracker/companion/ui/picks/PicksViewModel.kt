package com.gametracker.companion.ui.picks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class PicksViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<SlotsResponse>>(UiState.Loading)
    val state: StateFlow<UiState<SlotsResponse>> = _state

    private val _picker = MutableStateFlow<List<GameSummary>>(emptyList())
    val picker: StateFlow<List<GameSummary>> = _picker

    fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.slots().fold(
            onSuccess = { _state.value = if (it.slots.isEmpty()) UiState.Empty else UiState.Success(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun pin(slotId: Int, gameId: Int, goal: String?) = viewModelScope.launch {
        if (repository.pin(slotId, gameId, goal).isSuccess) load()
    }

    fun applyOutcome(slotId: Int, outcome: String) = viewModelScope.launch {
        if (repository.outcome(slotId, outcome).isSuccess) load()
    }

    fun editGoal(slotId: Int, goal: String?) = viewModelScope.launch {
        if (repository.setGoal(slotId, goal).isSuccess) load()
    }

    fun reorder(slotIds: List<Int>) = viewModelScope.launch {
        if (repository.reorderSlots(slotIds).isSuccess) load()
    }

    fun searchLibrary(q: String) = viewModelScope.launch {
        _picker.value = if (q.length < 2) emptyList()
                        else repository.games(search = q).getOrDefault(emptyList())
    }
}
