package com.gametracker.companion.ui.picks

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.data.Slot
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun PicksScreen(onOpenGame: (Int) -> Unit) {
    val vm: PicksViewModel = viewModel(factory = rememberAppFactory())
    LaunchedEffect(Unit) { vm.load() }
    when (val s = vm.state.collectAsState().value) {
        is UiState.Loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        is UiState.Empty -> Box(Modifier.fillMaxSize(), Alignment.Center) { Text("No slots yet") }
        is UiState.Error -> Box(Modifier.fillMaxSize(), Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Can't reach Game Tracker — VPN connected?")
                Button(onClick = { vm.load() }) { Text("Retry") }
            }
        }
        is UiState.Success -> PicksContent(s.data, vm, onOpenGame)
    }
}

@Composable
private fun PicksContent(data: SlotsResponse, vm: PicksViewModel, onOpenGame: (Int) -> Unit) {
    val active = data.slots.filter { it.currentGame != null }
    Column(Modifier.fillMaxSize()) {
        if (active.isNotEmpty()) {
            val pager = rememberPagerState(pageCount = { active.size })
            HorizontalPager(
                state = pager,
                pageSpacing = 12.dp,
                contentPadding = PaddingValues(horizontal = 32.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(320.dp),
            ) { page ->
                val slot = active[page]
                Card(onClick = { slot.currentGame?.let { onOpenGame(it.id) } }) {
                    CoverImage(
                        slot.currentGame?.coverUrl,
                        slot.currentGame?.title ?: "",
                        Modifier
                            .fillMaxWidth()
                            .height(260.dp),
                    )
                    Text(slot.label, Modifier.padding(8.dp), style = MaterialTheme.typography.titleSmall)
                    slot.goal?.let { Text(it, Modifier.padding(horizontal = 8.dp)) }
                }
            }
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(data.slots, key = { it.id }) { slot ->
                SlotRow(slot, data.slots, vm, onOpenGame)
            }
        }
    }
}

@Composable
private fun SlotRow(slot: Slot, allSlots: List<Slot>, vm: PicksViewModel, onOpenGame: (Int) -> Unit) {
    var showGoalDialog by remember { mutableStateOf(false) }
    var goalText by remember(slot.id) { mutableStateOf(slot.goal ?: "") }

    if (showGoalDialog) {
        AlertDialog(
            onDismissRequest = { showGoalDialog = false },
            title = { Text("Edit goal") },
            text = {
                OutlinedTextField(
                    value = goalText,
                    onValueChange = { goalText = it },
                    label = { Text("Goal") },
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    vm.editGoal(slot.id, goalText.ifBlank { null })
                    showGoalDialog = false
                }) { Text("Save") }
            },
            dismissButton = {
                TextButton(onClick = { showGoalDialog = false }) { Text("Cancel") }
            },
        )
    }

    Card(Modifier.fillMaxWidth().padding(8.dp)) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(slot.label, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
                // Up/Down reorder buttons
                val idx = allSlots.indexOfFirst { it.id == slot.id }
                IconButton(
                    onClick = {
                        if (idx > 0) {
                            val ids = allSlots.map { it.id }.toMutableList()
                            ids[idx] = ids[idx - 1].also { ids[idx - 1] = ids[idx] }
                            vm.reorder(ids)
                        }
                    },
                    enabled = idx > 0,
                ) { Icon(Icons.Filled.KeyboardArrowUp, contentDescription = "Move up") }
                IconButton(
                    onClick = {
                        if (idx < allSlots.size - 1) {
                            val ids = allSlots.map { it.id }.toMutableList()
                            ids[idx] = ids[idx + 1].also { ids[idx + 1] = ids[idx] }
                            vm.reorder(ids)
                        }
                    },
                    enabled = idx < allSlots.size - 1,
                ) { Icon(Icons.Filled.KeyboardArrowDown, contentDescription = "Move down") }
            }
            val g = slot.currentGame
            if (g != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(g.title, Modifier.weight(1f))
                    IconButton(onClick = { showGoalDialog = true }) {
                        Icon(Icons.Filled.Edit, contentDescription = "Edit goal")
                    }
                }
                slot.goal?.let { Text("Goal: $it", style = MaterialTheme.typography.bodySmall) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "beat") }) { Text("Complete") }
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "complete") }) { Text("100%") }
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "dropped") }) { Text("Drop") }
                    OutlinedButton(onClick = { vm.applyOutcome(slot.id, "swap") }) { Text("Swap") }
                }
            } else {
                Text("Empty — candidates:", style = MaterialTheme.typography.bodySmall)
                slot.candidates.take(3).forEach { c ->
                    TextButton(onClick = { vm.pin(slot.id, c.id, null) }) { Text(c.title) }
                }
            }
        }
    }
}
