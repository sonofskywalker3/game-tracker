package com.gametracker.companion.ui.add

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.IgdbResult
import com.gametracker.companion.ui.common.AppScaffold
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun AddScreen(initialQuery: String?, pendingUpc: String?, onOpenGame: (Int) -> Unit, onScan: () -> Unit) {
    val vm: AddViewModel = viewModel(factory = rememberAppFactory())
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var query by remember { mutableStateOf(initialQuery ?: "") }
    var pendingAdd by remember { mutableStateOf<IgdbResult?>(null) }
    LaunchedEffect(query) { delay(300); vm.search(query) }   // debounce

    // Commit an add (optionally with chosen platforms) then open the new game's detail.
    fun commit(result: IgdbResult, platforms: List<String>) {
        scope.launch {
            val gid = vm.add(result, platforms = platforms, physical = pendingUpc != null, upc = pendingUpc)
            pendingAdd = null
            if (gid != null) onOpenGame(gid)
            else snackbar.showSnackbar("Couldn't add — it may already be in your library")
        }
    }

    pendingAdd?.let { result ->
        PlatformPickDialog(
            result = result,
            onConfirm = { chosen -> commit(result, chosen) },
            onDismiss = { pendingAdd = null },
        )
    }

    AppScaffold(title = "Add", snackbarHostState = snackbar) { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            OutlinedTextField(query, { query = it }, label = { Text("Search to add a game") },
                singleLine = true, modifier = Modifier.fillMaxWidth().padding(8.dp))
            Button(onClick = onScan, modifier = Modifier.padding(horizontal = 8.dp)) {
                Text("Scan barcode")
            }
            when (val st = vm.results.collectAsState().value) {
                is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
                is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("Search IGDB to add") }
                is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(st.message) }
                is UiState.Success -> LazyColumn(Modifier.fillMaxSize()) {
                    items(st.data) { r: IgdbResult ->
                        Row(Modifier.fillMaxWidth().clickable {
                            // No platforms to choose from → add straight away.
                            if (r.platforms.isEmpty()) commit(r, emptyList()) else pendingAdd = r
                        }.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            CoverImage(r.coverUrl, r.name, Modifier.width(48.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(r.name)
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PlatformPickDialog(
    result: IgdbResult,
    onConfirm: (List<String>) -> Unit,
    onDismiss: () -> Unit,
) {
    // Pre-select nothing — the point is the owner picks the ones they actually own,
    // not every platform IGDB lists for the title.
    val selected = remember(result) { mutableStateListOf<String>() }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add ${result.name}") },
        text = {
            Column {
                Text("Which platform(s) do you own?", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    result.platforms.forEach { p ->
                        FilterChip(
                            selected = p in selected,
                            onClick = { if (!selected.remove(p)) selected.add(p) },
                            label = { Text(p) },
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(selected.toList()) }) { Text("Add") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
