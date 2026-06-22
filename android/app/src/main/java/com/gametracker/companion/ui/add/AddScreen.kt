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
    LaunchedEffect(query) { delay(300); vm.search(query) }   // debounce

    Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { pad ->
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
                            scope.launch {
                                val gid = vm.add(r, physical = pendingUpc != null, upc = pendingUpc)
                                if (gid != null) onOpenGame(gid)
                                else snackbar.showSnackbar("Couldn't add — it may already be in your library")
                            }
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
