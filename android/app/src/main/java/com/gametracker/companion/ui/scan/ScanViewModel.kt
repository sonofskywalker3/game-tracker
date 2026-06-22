package com.gametracker.companion.ui.scan

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.Repository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ScanState {
    data object Scanning : ScanState
    data object Resolving : ScanState
    data class Owned(val gameId: Int, val title: String?, val platform: String?) : ScanState
    data class Candidates(val candidates: List<BarcodeCandidate>, val upc: String) : ScanState
    data class NoMatch(val upc: String, val productTitle: String?) : ScanState
    data class Error(val message: String) : ScanState
    data class Added(val gameId: Int?) : ScanState
}

class ScanViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<ScanState>(ScanState.Scanning)
    val state: StateFlow<ScanState> = _state

    fun onBarcode(upc: String) = viewModelScope.launch {
        _state.value = ScanState.Resolving
        repository.resolveBarcode(upc).fold(
            onSuccess = { r ->
                val owned = r.candidates.firstOrNull { it.ownedGameId != null }
                _state.value = when {
                    owned != null -> ScanState.Owned(owned.ownedGameId!!, owned.title, owned.platform)
                    r.candidates.isNotEmpty() -> ScanState.Candidates(r.candidates, upc)
                    else -> ScanState.NoMatch(upc, r.productTitle)
                }
            },
            onFailure = { _state.value = ScanState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun addCandidate(c: BarcodeCandidate, upc: String) = viewModelScope.launch {
        val gid = repository.createGame(
            title = c.title ?: "", coverUrl = c.coverUrl,
            platforms = listOfNotNull(c.platform), physical = true, upc = upc,
        ).getOrNull()?.gameId
        _state.value = ScanState.Added(gid)
    }

    fun reset() { _state.value = ScanState.Scanning }
}
