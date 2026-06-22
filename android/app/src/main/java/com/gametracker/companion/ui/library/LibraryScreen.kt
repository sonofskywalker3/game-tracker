package com.gametracker.companion.ui.library

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.ui.common.AppScaffold
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

private val STATUSES = listOf("backlog", "playing", "completed", "100", "dropped")
private fun statusLabel(s: String) = if (s == "100") "100%" else s

@Composable
fun LibraryScreen(onOpenGame: (Int) -> Unit) {
    val vm: LibraryViewModel = viewModel(factory = rememberAppFactory())
    val query by vm.query.collectAsState()
    val status by vm.status.collectAsState()
    val platform by vm.platform.collectAsState()
    val knownPlatforms by vm.platforms.collectAsState()
    var searchOpen by rememberSaveable { mutableStateOf(false) }
    val focus = remember { FocusRequester() }

    AppScaffold(
        title = "Library",
        actions = {
            IconButton(onClick = {
                searchOpen = !searchOpen
                if (!searchOpen) vm.onSearch("")
            }) { Icon(Icons.Filled.Search, contentDescription = "Search") }
        },
    ) { pad ->
        if (searchOpen) {
            AlertDialog(
                onDismissRequest = { searchOpen = false },
                title = { Text("Search library") },
                text = {
                    OutlinedTextField(
                        query, { vm.onSearch(it) },
                        placeholder = { Text("Title…") },
                        singleLine = true,
                        trailingIcon = {
                            if (query.isNotEmpty()) {
                                IconButton(onClick = { vm.onSearch("") }) {
                                    Icon(Icons.Filled.Close, contentDescription = "Clear")
                                }
                            }
                        },
                        modifier = Modifier.fillMaxWidth().focusRequester(focus),
                    )
                    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
                },
                confirmButton = { TextButton(onClick = { searchOpen = false }) { Text("Done") } },
                dismissButton = {
                    TextButton(onClick = { vm.onSearch(""); searchOpen = false }) { Text("Clear") }
                },
            )
        }

        Column(Modifier.padding(pad).fillMaxSize()) {
            Row(
                Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterDropdown("Status", status, STATUSES, ::statusLabel) { vm.setStatusFilter(it) }
                FilterDropdown("Platform", platform, knownPlatforms) { vm.setPlatformFilter(it) }
            }

            when (val st = vm.state.collectAsState().value) {
                is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
                is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("No games") }
                is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(st.message) }
                is UiState.Success -> {
                    LazyVerticalGrid(GridCells.Adaptive(110.dp), Modifier.fillMaxSize()) {
                        items(st.data, key = { it.id }) { g ->
                            Column(Modifier.padding(6.dp).clickable { onOpenGame(g.id) }) {
                                CoverImage(g.coverUrl, g.title, Modifier.fillMaxWidth())
                                Text(g.title, maxLines = 2, style = MaterialTheme.typography.labelSmall)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Outlined-button dropdown for a single-select filter. `null` selection = "All". */
@Composable
private fun FilterDropdown(
    label: String,
    selected: String?,
    options: List<String>,
    display: (String) -> String = { it },
    onSelect: (String?) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { expanded = true }) {
            Text(selected?.let(display) ?: label)
            Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text("All ${label.lowercase()}") },
                onClick = { onSelect(null); expanded = false },
            )
            options.forEach { opt ->
                DropdownMenuItem(
                    text = { Text(display(opt)) },
                    onClick = { onSelect(opt); expanded = false },
                )
            }
        }
    }
}
