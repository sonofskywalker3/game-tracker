package com.gametracker.companion.ui.library

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory
import kotlinx.coroutines.delay

private val STATUSES = listOf("backlog", "playing", "completed", "100", "dropped")

@Composable
fun LibraryScreen(onOpenGame: (Int) -> Unit) {
    val vm: LibraryViewModel = viewModel(factory = rememberAppFactory())
    LaunchedEffect(Unit) { vm.load() }
    var query by remember { mutableStateOf("") }
    var status by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(query) { delay(300); vm.onSearch(query) }   // debounce

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(query, { query = it }, label = { Text("Search") },
            singleLine = true, modifier = Modifier.fillMaxWidth().padding(8.dp))
        Row(Modifier.padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            STATUSES.forEach { s ->
                FilterChip(selected = status == s, onClick = {
                    status = if (status == s) null else s; vm.setStatusFilter(status)
                }, label = { Text(if (s == "100") "100%" else s) })
            }
        }
        when (val st = vm.state.collectAsState().value) {
            is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
            is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("No games") }
            is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(st.message) }
            is UiState.Success -> LazyVerticalGrid(GridCells.Adaptive(110.dp), Modifier.fillMaxSize()) {
                items(st.data, key = { it.id }) { g ->
                    Column(Modifier.padding(6.dp).clickable { onOpenGame(g.id) }) {
                        CoverImage(g.coverUrl, g.title, Modifier.fillMaxWidth().height(150.dp))
                        Text(g.title, maxLines = 2, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }
}
