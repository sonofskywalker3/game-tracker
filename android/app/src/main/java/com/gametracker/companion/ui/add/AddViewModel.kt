package com.gametracker.companion.ui.add

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.IgdbResult
import com.gametracker.companion.data.Repository
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class AddViewModel(private val repository: Repository) : ViewModel() {

    private val _results = MutableStateFlow<UiState<List<IgdbResult>>>(UiState.Empty)
    val results: StateFlow<UiState<List<IgdbResult>>> = _results

    fun search(q: String) = viewModelScope.launch {
        if (q.length < 2) { _results.value = UiState.Empty; return@launch }
        _results.value = UiState.Loading
        repository.igdbSearch(q).fold(
            onSuccess = { _results.value = if (it.isEmpty()) UiState.Empty else UiState.Success(it) },
            onFailure = { _results.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    /** Create the game with the platforms the user actually owns (not every platform
     *  IGDB lists). Returns the new game_id (or null on failure). */
    suspend fun add(result: IgdbResult, platforms: List<String> = result.platforms,
                    physical: Boolean = false, upc: String? = null): Int? =
        repository.createGame(
            title = result.name, coverUrl = result.coverUrl,
            platforms = platforms, physical = physical, upc = upc,
        ).getOrNull()?.gameId

    /** Info mode: record UPC -> chosen IGDB game in the registry, no library add.
     *  Search results carry no numeric igdb_id, so we link by title/cover/platform. */
    suspend fun linkInfo(result: IgdbResult, platform: String, upc: String): Result<Unit> =
        repository.link(upc, igdbId = null, title = result.name,
                        coverUrl = result.coverUrl, platform = platform, gameId = null)
}
