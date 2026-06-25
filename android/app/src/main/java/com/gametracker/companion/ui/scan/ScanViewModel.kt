package com.gametracker.companion.ui.scan

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.BarcodeResolveResponse
import com.gametracker.companion.data.Repository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ScanState {
    data object Scanning : ScanState
    data object Resolving : ScanState
    data class Info(val candidate: BarcodeCandidate, val scannedPlatform: String?,
                    val upc: String) : ScanState
    data class Picker(val candidates: List<BarcodeCandidate>, val upc: String,
                      val scannedPlatform: String?) : ScanState
    data class NeedsPlatform(val candidate: BarcodeCandidate, val upc: String) : ScanState
    data class Linked(val title: String?, val platform: String, val coverUrl: String? = null) : ScanState
    data class NoMatch(val upc: String, val productTitle: String?) : ScanState
    data class Error(val message: String) : ScanState
    data class Added(val gameId: Int?) : ScanState
}

class ScanViewModel(private val repository: Repository) : ViewModel() {

    private val _state = MutableStateFlow<ScanState>(ScanState.Scanning)
    val state: StateFlow<ScanState> = _state

    private val _databaseMode = MutableStateFlow(false)
    val databaseMode: StateFlow<Boolean> = _databaseMode
    fun setDatabaseMode(on: Boolean) { _databaseMode.value = on }

    fun onBarcode(upc: String) = viewModelScope.launch {
        _state.value = ScanState.Resolving
        repository.resolveBarcode(upc).fold(
            onSuccess = { r -> route(r, upc) },
            onFailure = { _state.value = ScanState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    private fun route(r: BarcodeResolveResponse, upc: String) {
        val cands = r.candidates
        val fromCache = r.source == "cache"
        when {
            cands.isEmpty() -> _state.value = ScanState.NoMatch(upc, r.productTitle)
            cands.size > 1 -> _state.value = ScanState.Picker(cands, upc, r.scannedPlatform)
            else -> routeSingle(cands[0], r.scannedPlatform, upc, link = !fromCache)
        }
    }

    // Auto path for exactly one candidate: coverless => force a confirm (picker),
    // unknown platform => prompt, otherwise commit (linking unless it came from cache).
    private fun routeSingle(c: BarcodeCandidate, scannedPlatform: String?, upc: String,
                            link: Boolean) {
        val platform = c.platform ?: scannedPlatform
        when {
            c.coverUrl == null -> _state.value = ScanState.Picker(listOf(c), upc, scannedPlatform)
            platform == null -> _state.value = ScanState.NeedsPlatform(c, upc)
            else -> commit(c, platform, upc, link)
        }
    }

    /** User chose a candidate from the picker. */
    fun pick(c: BarcodeCandidate, upc: String, scannedPlatform: String?) {
        val platform = c.platform ?: scannedPlatform
        if (platform == null) _state.value = ScanState.NeedsPlatform(c, upc)
        else commit(c, platform, upc, link = true)
    }

    /** User chose a platform for a candidate that resolved without one. */
    fun choosePlatform(c: BarcodeCandidate, platform: String, upc: String) =
        commit(c, platform, upc, link = true)

    private fun commit(c: BarcodeCandidate, platform: String, upc: String, link: Boolean) {
        if (link) viewModelScope.launch {
            repository.link(upc, c.igdbId, c.title, c.coverUrl, platform, c.ownedGameId)
        }
        _state.value = if (_databaseMode.value) ScanState.Linked(c.title, platform, c.coverUrl)
                       else ScanState.Info(c, platform, upc)
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
