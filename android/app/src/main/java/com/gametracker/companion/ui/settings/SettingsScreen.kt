package com.gametracker.companion.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun SettingsScreen() {
    val vm: SettingsViewModel = viewModel(factory = rememberAppFactory())
    val saved by vm.baseUrl.collectAsState()
    val result by vm.testResult.collectAsState()
    var field by remember(saved) { mutableStateOf(saved) }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Backend URL", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(value = field, onValueChange = { field = it },
            label = { Text("http://192.168.1.x:5000") }, singleLine = true,
            modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.save(field) }) { Text("Save") }
            OutlinedButton(onClick = { vm.test() }) { Text("Test connection") }
        }
        when (result) {
            TestResult.Testing -> Text("Testing…")
            TestResult.Ok -> Text("Connected ✓")
            TestResult.Failed -> Text("Can't reach Game Tracker — VPN connected?")
            TestResult.Idle -> {}
        }
        Spacer(Modifier.height(8.dp))
        Text("VPN", style = MaterialTheme.typography.titleMedium)
        Text("Set up in the VPN task.", style = MaterialTheme.typography.bodySmall)
    }
}
