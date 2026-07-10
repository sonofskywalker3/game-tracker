package com.backlogquest.companion.ui.picks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.backlogquest.companion.data.GameSummary
import com.backlogquest.companion.data.Repository
import com.backlogquest.companion.data.SlotsResponse
import com.backlogquest.companion.schedule.scheduleAwareOrder
import com.backlogquest.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.util.Calendar

/** (weekday 0=Mon..6=Sun, minute-of-day) from the device clock. */
fun deviceNowWeekdayMinute(): Pair<Int, Int> {
    val c = Calendar.getInstance()
    // Calendar.DAY_OF_WEEK: Sunday=1..Saturday=7 -> convert to Mon=0..Sun=6
    val weekday = ((c.get(Calendar.DAY_OF_WEEK) + 5) % 7)
    val minute = c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE)
    return weekday to minute
}

class PicksViewModel(
    private val repository: Repository,
    private val nowProvider: () -> Pair<Int, Int> = ::deviceNowWeekdayMinute,
) : ViewModel() {

    private val _state = MutableStateFlow<UiState<SlotsResponse>>(UiState.Loading)
    val state: StateFlow<UiState<SlotsResponse>> = _state

    private val _picker = MutableStateFlow<List<GameSummary>>(emptyList())
    val picker: StateFlow<List<GameSummary>> = _picker

    /** Re-order the slots schedule-aware (active most-restrictive-first) off the clock. */
    private fun ordered(resp: SlotsResponse): SlotsResponse {
        val (weekday, minute) = nowProvider()
        return resp.copy(slots = scheduleAwareOrder(resp.slots, weekday, minute))
    }

    private fun emit(resp: SlotsResponse) {
        _state.value = if (resp.slots.isEmpty()) UiState.Empty else UiState.Success(ordered(resp))
    }

    fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.slots().fold(
            onSuccess = { emit(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach BacklogQuest") },
        )
    }

    fun pin(slotId: Int, gameId: Int, goal: String?) = viewModelScope.launch {
        if (repository.pin(slotId, gameId, goal).isSuccess) refresh()
    }

    fun applyOutcome(slotId: Int, outcome: String) = viewModelScope.launch {
        if (repository.outcome(slotId, outcome).isSuccess) refresh()
    }

    fun editGoal(slotId: Int, goal: String?) = viewModelScope.launch {
        if (repository.setGoal(slotId, goal).isSuccess) refresh()
    }

    fun reorder(slotIds: List<Int>) = viewModelScope.launch {
        if (repository.reorderSlots(slotIds).isSuccess) refresh()
    }

    /** Reload after a mutation WITHOUT going through Loading. */
    private suspend fun refresh() {
        repository.slots().onSuccess { emit(it) }
    }

    fun searchLibrary(q: String) = viewModelScope.launch {
        _picker.value = if (q.length < 2) emptyList()
                        else repository.games(search = q).getOrDefault(emptyList())
    }
}
