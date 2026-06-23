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
    data class Info(val candidate: BarcodeCandidate, val scannedPlatform: String?,
                    val upc: String) : ScanState
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
                val top = r.candidates.firstOrNull()
                _state.value = if (top == null) ScanState.NoMatch(upc, r.productTitle)
                               else ScanState.Info(top, r.scannedPlatform, upc)
            },
            onFailure = { _state.value = ScanState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    /** Add a not-owned game, defaulting to the scanned platform + physical format. */
    fun addToLibrary(c: BarcodeCandidate, scannedPlatform: String?, upc: String) =
        viewModelScope.launch {
            val platforms = listOfNotNull(scannedPlatform ?: c.platform)
            val gid = repository.createGame(
                title = c.title ?: "", coverUrl = c.coverUrl,
                platforms = platforms, physical = true, upc = upc,
            ).getOrNull()?.gameId
            _state.value = ScanState.Added(gid)
        }

    /** "I also bought the <scanned> copy": append that platform (physical) + UPC to the
     *  already-owned game. */
    fun addPlatformCopy(c: BarcodeCandidate, scannedPlatform: String, upc: String) =
        viewModelScope.launch {
            val gid = c.ownedGameId
            if (gid != null) repository.addPlatform(gid, scannedPlatform, "physical", upc)
            _state.value = ScanState.Added(gid)
        }

    fun reset() { _state.value = ScanState.Scanning }
}
