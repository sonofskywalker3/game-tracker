package com.gametracker.companion.ui.add

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
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
fun AddScreen(initialQuery: String?, pendingUpc: String?, infoMode: Boolean, onOpenGame: (Int) -> Unit, onLinked: () -> Unit) {
    val vm: AddViewModel = viewModel(factory = rememberAppFactory())
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var query by remember { mutableStateOf(initialQuery ?: "") }
    var pendingAdd by remember { mutableStateOf<IgdbResult?>(null) }
    LaunchedEffect(query) { delay(300); vm.search(query) }   // debounce

    // Commit an add with chosen platforms and format, then open the new game's detail.
    fun commit(result: IgdbResult, platforms: List<String>, physical: Boolean) {
        scope.launch {
            if (infoMode) {
                val platform = platforms.firstOrNull() ?: result.platforms.firstOrNull() ?: ""
                if (platform.isBlank()) {
                    snackbar.showSnackbar("Pick a platform to link this barcode")
                    return@launch
                }
                val res = vm.linkInfo(result, platform, pendingUpc ?: "")
                if (res.isSuccess) { pendingAdd = null; onLinked() }
                else snackbar.showSnackbar("Couldn't link — try again")
            } else {
                val gid = vm.add(result, platforms = platforms, physical = physical, upc = pendingUpc)
                pendingAdd = null
                if (gid != null) onOpenGame(gid)
                else snackbar.showSnackbar("Couldn't add — it may already be in your library")
            }
        }
    }

    pendingAdd?.let { result ->
        GameDetailPickDialog(
            result = result,
            defaultPhysical = pendingUpc != null,   // barcode→physical default, search→digital
            onConfirm = { chosen, physical -> commit(result, chosen, physical) },
            onDismiss = { pendingAdd = null },
        )
    }

    AppScaffold(title = "Add", snackbarHostState = snackbar) { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            OutlinedTextField(query, { query = it }, label = { Text("Search to add a game") },
                singleLine = true, modifier = Modifier.fillMaxWidth().padding(8.dp))
when (val st = vm.results.collectAsState().value) {
                is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
                is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("Search IGDB to add") }
                is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text(st.message) }
                is UiState.Success -> LazyColumn(Modifier.fillMaxSize()) {
                    items(st.data) { r: IgdbResult ->
                        Row(Modifier.fillMaxWidth().clickable {
                            pendingAdd = r
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
private fun GameDetailPickDialog(
    result: IgdbResult,
    defaultPhysical: Boolean,
    onConfirm: (List<String>, Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    val selected = remember(result) {
        mutableStateListOf<String>().apply { if (result.platforms.size == 1) add(result.platforms[0]) }
    }
    var physical by remember(result) { mutableStateOf(defaultPhysical) }
    val uriHandler = LocalUriHandler.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(result.name) },
        text = {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    CoverImage(result.coverUrl, result.name, Modifier.width(96.dp))
                    Column {
                        result.year?.let { Text("Released $it", style = MaterialTheme.typography.bodyMedium) }
                        if (!result.igdbUrl.isNullOrBlank()) {
                            TextButton(contentPadding = PaddingValues(0.dp),
                                onClick = { uriHandler.openUri(result.igdbUrl) }) { Text("View on IGDB") }
                        }
                    }
                }
                if (result.platforms.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Text("Which platform(s) do you own?", style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(4.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        result.platforms.forEach { p ->
                            FilterChip(selected = p in selected,
                                onClick = { if (!selected.remove(p)) selected.add(p) },
                                label = { Text(p) })
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text("Format", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(4.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = physical, onClick = { physical = true }, label = { Text("Physical") })
                    FilterChip(selected = !physical, onClick = { physical = false }, label = { Text("Digital") })
                }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(selected.toList(), physical) }) { Text("Add") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
