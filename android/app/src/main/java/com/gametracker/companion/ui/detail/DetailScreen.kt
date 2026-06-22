package com.gametracker.companion.ui.detail

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.GameDetail
import com.gametracker.companion.ui.common.AppScaffold
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun DetailScreen(gameId: Int, justAdded: Boolean = false, onBack: () -> Unit = {}) {
    val vm: DetailViewModel = viewModel(key = gameId.toString(), factory = rememberAppFactory())
    LaunchedEffect(gameId) { vm.load(gameId) }
    val s = vm.state.collectAsState().value
    val title = (s as? UiState.Success)?.data?.title ?: "Game"

    AppScaffold(title = title, onBack = onBack) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (s) {
                is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
                is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("Not found") }
                is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(s.message) }
                is UiState.Success -> DetailContent(
                    g = s.data,
                    justAdded = justAdded,
                    onStatus = { status -> vm.changeStatus(gameId, status) },
                )
            }
        }
    }
}

@Composable
private fun DetailContent(g: GameDetail, justAdded: Boolean, onStatus: (String) -> Unit) {
    var showAdded by remember { mutableStateOf(justAdded) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (showAdded) {
            AddedBanner(g.title) { showAdded = false }
        }
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            CoverImage(g.coverUrl, g.title, Modifier.height(260.dp))
        }
        Text(g.title, style = MaterialTheme.typography.headlineSmall)

        val plats = g.platforms.mapNotNull { it.shortName ?: it.name }
        if (plats.isNotEmpty()) {
            Text("Platforms: " + plats.joinToString(", "), style = MaterialTheme.typography.bodyMedium)
        }
        g.hoursPlayed?.let { Text("Hours: $it", style = MaterialTheme.typography.bodyMedium) }
        g.rating?.let { Text("Rating: ${ratingLabel(it)}", style = MaterialTheme.typography.bodyMedium) }
        StatusControl(current = g.status, onStatus = onStatus)

        if (g.dlc.isNotEmpty()) {
            Text("DLC", style = MaterialTheme.typography.titleMedium)
            g.dlc.forEach { Text("• ${it.name}${if (it.owned) " ✓" else ""}") }
        }
    }
}

@Composable
private fun AddedBanner(title: String, onDismiss: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.primaryContainer,
        contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
        shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.padding(start = 14.dp, end = 6.dp, top = 10.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(Icons.Filled.CheckCircle, contentDescription = null)
            Column(Modifier.weight(1f)) {
                Text("Added to your library", style = MaterialTheme.typography.titleMedium)
                Text(title, style = MaterialTheme.typography.bodyMedium)
            }
            IconButton(onClick = onDismiss) {
                Icon(Icons.Filled.Close, contentDescription = "Dismiss")
            }
        }
    }
}

// Mirrors the web app's 1–4 qualitative scale (models.py: hate/meh/like/love).
private fun ratingLabel(r: Int) = when (r) {
    1 -> "😡 Hate it"
    2 -> "😐 Meh"
    3 -> "🙂 Like it"
    4 -> "😍 Love it"
    else -> r.toString()
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
