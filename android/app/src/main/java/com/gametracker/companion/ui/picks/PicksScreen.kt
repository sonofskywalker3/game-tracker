package com.gametracker.companion.ui.picks

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
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
import com.gametracker.companion.ui.common.AppScaffold
import com.gametracker.companion.ui.common.CoverImage
import com.gametracker.companion.ui.common.UiState
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun PicksScreen(onOpenGame: (Int) -> Unit) {
    val vm: PicksViewModel = viewModel(factory = rememberAppFactory())
    LaunchedEffect(Unit) { vm.load() }
    AppScaffold(title = "Picks") { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
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
    }
}

@Composable
private fun PicksContent(data: SlotsResponse, vm: PicksViewModel, onOpenGame: (Int) -> Unit) {
    if (data.slots.isEmpty()) {
        Box(Modifier.fillMaxSize(), Alignment.Center) { Text("No slots yet") }
        return
    }

    var assignForSlot by remember { mutableStateOf<Int?>(null) }

    // Assign search dialog
    if (assignForSlot != null) {
        val slotId = assignForSlot!!
        var query by remember { mutableStateOf("") }
        val pickerResults by vm.picker.collectAsState()
        AlertDialog(
            onDismissRequest = {
                query = ""
                vm.searchLibrary("")
                assignForSlot = null
            },
            title = { Text("Assign game to slot") },
            text = {
                Column {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it; vm.searchLibrary(it) },
                        label = { Text("Search library") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(8.dp))
                    LazyColumn(Modifier.heightIn(max = 240.dp)) {
                        items(pickerResults, key = { it.id }) { game ->
                            TextButton(
                                onClick = {
                                    vm.pin(slotId, game.id, null)
                                    query = ""
                                    vm.searchLibrary("")
                                    assignForSlot = null
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                val platformLabel = game.platforms.firstOrNull()?.let { " ($it)" } ?: ""
                                Text(game.title + platformLabel)
                            }
                        }
                    }
                }
            },
            confirmButton = {},
            dismissButton = {
                TextButton(onClick = {
                    query = ""
                    vm.searchLibrary("")
                    assignForSlot = null
                }) { Text("Cancel") }
            },
        )
    }

    val pager = rememberPagerState(pageCount = { data.slots.size })
    val currentSlot = data.slots[pager.currentPage]

    Column(Modifier.fillMaxSize()) {
        // Carousel over all slots (including empty)
        HorizontalPager(
            state = pager,
            pageSpacing = 12.dp,
            contentPadding = PaddingValues(horizontal = 40.dp),
            modifier = Modifier.fillMaxWidth(),
        ) { page ->
            val slot = data.slots[page]
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .then(
                        if (slot.currentGame != null)
                            Modifier.clickable { onOpenGame(slot.currentGame.id) }
                        else
                            Modifier
                    ),
                contentAlignment = Alignment.Center,
            ) {
                CoverImage(
                    slot.currentGame?.coverUrl,
                    slot.currentGame?.title ?: slot.label,
                    Modifier.height(240.dp),
                )
            }
        }

        // Page indicator dots
        Row(
            Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            repeat(data.slots.size) { i ->
                val selected = i == pager.currentPage
                Surface(
                    modifier = Modifier
                        .padding(horizontal = 3.dp)
                        .size(if (selected) 8.dp else 6.dp),
                    shape = CircleShape,
                    color = if (selected) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.outlineVariant,
                ) {}
            }
        }

        // Detail panel for the currently selected slot
        SlotDetailPanel(
            slot = currentSlot,
            allSlots = data.slots,
            vm = vm,
            onAssign = { assignForSlot = currentSlot.id },
        )
    }
}

@Composable
private fun SlotDetailPanel(
    slot: Slot,
    allSlots: List<Slot>,
    vm: PicksViewModel,
    onAssign: () -> Unit,
) {
    var showGoalDialog by remember(slot.id) { mutableStateOf(false) }
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

    val idx = allSlots.indexOfFirst { it.id == slot.id }

    Card(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            // Slot label + reorder buttons
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    slot.label,
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.titleMedium,
                )
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
                Text(g.title, style = MaterialTheme.typography.bodyLarge)

                // Goal row
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        if (slot.goal != null) "Goal: ${slot.goal}" else "No goal set",
                        Modifier.weight(1f),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    IconButton(onClick = { showGoalDialog = true }) {
                        Icon(Icons.Filled.Edit, contentDescription = "Edit goal")
                    }
                }

                // Outcome buttons — two rows of two, then Assign full-width
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    OutlinedButton(
                        onClick = { vm.applyOutcome(slot.id, "beat") },
                        modifier = Modifier.weight(1f),
                    ) { Text("Complete", maxLines = 1) }
                    OutlinedButton(
                        onClick = { vm.applyOutcome(slot.id, "complete") },
                        modifier = Modifier.weight(1f),
                    ) { Text("100%", maxLines = 1) }
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    OutlinedButton(
                        onClick = { vm.applyOutcome(slot.id, "dropped") },
                        modifier = Modifier.weight(1f),
                    ) { Text("Drop", maxLines = 1) }
                    OutlinedButton(
                        onClick = { vm.applyOutcome(slot.id, "swap") },
                        modifier = Modifier.weight(1f),
                    ) { Text("Swap", maxLines = 1) }
                }
                Button(
                    onClick = onAssign,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Assign game") }
            } else {
                Text(
                    "Empty slot",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (slot.candidates.isNotEmpty()) {
                    Text("Quick picks:", style = MaterialTheme.typography.labelMedium)
                    slot.candidates.take(3).forEach { c ->
                        OutlinedButton(
                            onClick = { vm.pin(slot.id, c.game.id, null) },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(c.game.title, maxLines = 1) }
                    }
                }
                Button(
                    onClick = onAssign,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Assign game") }
            }
        }
    }
}
