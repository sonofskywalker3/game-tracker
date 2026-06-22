package com.gametracker.companion.ui.detail

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.GameDetail
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun DetailScreen(gameId: Int) {
    val vm: DetailViewModel = viewModel(key = gameId.toString(), factory = rememberAppFactory())
    LaunchedEffect(gameId) { vm.load(gameId) }
    when (val s = vm.state.collectAsState().value) {
        is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("Not found") }
        is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(s.message) }
        is UiState.Success -> DetailContent(s.data) { status -> vm.changeStatus(gameId, status) }
    }
}

@Composable
private fun DetailContent(g: GameDetail, onStatus: (String) -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            CoverImage(g.coverUrl, g.title, Modifier.height(260.dp))
        }
        Text(g.title, style = MaterialTheme.typography.headlineSmall)
        Text("Platforms: " + g.platforms.mapNotNull { it.shortName ?: it.name }.joinToString(", "))
        g.hoursPlayed?.let { Text("Hours: $it") }
        g.rating?.let { Text("Rating: $it") }
        StatusControl(current = g.status, onStatus = onStatus)
        if (g.dlc.isNotEmpty()) {
            Text("DLC", style = MaterialTheme.typography.titleMedium)
            g.dlc.forEach { Text("• ${it.name}${if (it.owned) " ✓" else ""}") }
        }
    }
}

@Composable
private fun StatusControl(current: String?, onStatus: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    fun label(s: String?) = when (s) { "100" -> "100%"; "completed" -> "complete"; null -> "set status"; else -> s }
    Box {
        OutlinedButton(onClick = { expanded = true }) { Text("Status: ${label(current)}") }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            STATUS_OPTIONS.forEach { opt ->
                DropdownMenuItem(text = { Text(label(opt)) }, onClick = { expanded = false; onStatus(opt) })
            }
        }
    }
}
