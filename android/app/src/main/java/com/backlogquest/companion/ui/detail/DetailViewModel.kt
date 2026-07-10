package com.backlogquest.companion.ui.detail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.backlogquest.companion.data.GameDetail
import com.backlogquest.companion.data.Repository
import com.backlogquest.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

val STATUS_OPTIONS = listOf("backlog", "playing", "parked", "completed", "100", "dropped", "wishlist")

class DetailViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<UiState<GameDetail>>(UiState.Loading)
    val state: StateFlow<UiState<GameDetail>> = _state

    fun load(id: Int) = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.game(id).fold(
            onSuccess = { _state.value = UiState.Success(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach BacklogQuest") },
        )
    }

    fun changeStatus(id: Int, status: String) = viewModelScope.launch {
        if (repository.setStatus(id, status).isSuccess) refresh(id)
    }

    /** Re-fetch the game after a mutation without flashing the loading spinner. */
    private suspend fun refresh(id: Int) {
        repository.game(id).onSuccess { _state.value = UiState.Success(it) }
    }
}
