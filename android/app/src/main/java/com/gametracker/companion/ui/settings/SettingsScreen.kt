package com.gametracker.companion.ui.settings

import android.app.Activity
import android.net.VpnService
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavBackStackEntry
import com.gametracker.companion.App
import com.gametracker.companion.ui.common.rememberAppFactory
import com.gametracker.companion.vpn.TunnelStatus
import com.gametracker.companion.vpn.parseWgConfig
import kotlinx.coroutines.launch

private const val SCANNED_CONF_KEY = "scanned_conf"

@Composable
fun SettingsScreen(
    backStackEntry: NavBackStackEntry? = null,
    onNavigateToQrScan: () -> Unit = {},
) {
    val vm: SettingsViewModel = viewModel(factory = rememberAppFactory())
    val saved by vm.baseUrl.collectAsState()
    val result by vm.testResult.collectAsState()
    var field by remember(saved) { mutableStateOf(saved) }

    val context = LocalContext.current
    val app = context.applicationContext as App
    val wgConfigStore = app.container.wgConfigStore
    val vpnController = app.container.vpnController

    val tunnelStatus by vpnController.status.collectAsState()
    var pasteField by remember { mutableStateOf("") }
    var configError by remember { mutableStateOf<String?>(null) }
    var savedConf by remember { mutableStateOf<String?>(null) }

    val scope = rememberCoroutineScope()

    // VPN consent launcher — Android requires an explicit user-consent intent before
    // first VpnService activation. VpnService.prepare() returns null if consent was
    // already granted, or a non-null Intent to launch for the one-time OS dialog.
    var pendingConnect by remember { mutableStateOf(false) }
    val vpnConsentLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { activityResult ->
        if (activityResult.resultCode == Activity.RESULT_OK && pendingConnect) {
            pendingConnect = false
            savedConf?.let { conf ->
                scope.launch { vpnController.connect(conf) }
            }
        } else {
            pendingConnect = false
        }
    }

    // Collect stored config on composition
    LaunchedEffect(Unit) {
        wgConfigStore.raw.collect { raw -> savedConf = raw }
    }

    // Pick up QR-scan result from the back-stack saved state (written by Nav.kt on pop)
    LaunchedEffect(backStackEntry) {
        backStackEntry?.savedStateHandle?.apply {
            get<String>(SCANNED_CONF_KEY)?.let { conf ->
                remove<String>(SCANNED_CONF_KEY)
                val parsed = parseWgConfig(conf)
                if (parsed.isSuccess) {
                    wgConfigStore.save(conf)
                    savedConf = conf
                    configError = null
                } else {
                    configError = parsed.exceptionOrNull()?.message ?: "Invalid QR config"
                }
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // ── Backend URL section ──────────────────────────────────────────────────
        Text("Backend URL", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(
            value = field,
            onValueChange = { field = it },
            label = { Text("http://192.168.1.x:5000") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { vm.save(field) }) { Text("Save") }
            OutlinedButton(onClick = { vm.test() }) { Text("Test connection") }
        }
        when (result) {
            TestResult.Testing -> Text("Testing…")
            TestResult.Ok     -> Text("Connected ✓")
            TestResult.Failed -> Text("Can't reach Game Tracker — VPN connected?")
            TestResult.Idle   -> {}
        }

        Spacer(Modifier.height(8.dp))

        // ── VPN section ──────────────────────────────────────────────────────────
        Text("VPN", style = MaterialTheme.typography.titleMedium)
        Text(
            "Import a Firewalla WireGuard profile to reach your home backend over cellular.",
            style = MaterialTheme.typography.bodySmall
        )

        // Status indicator
        Text(
            "Status: ${
                when (tunnelStatus) {
                    TunnelStatus.Down       -> "Down"
                    TunnelStatus.Connecting -> "Connecting…"
                    TunnelStatus.Up         -> "Up ✓"
                }
            }",
            style = MaterialTheme.typography.bodyMedium
        )

        // Scan QR button
        Button(
            onClick = onNavigateToQrScan,
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Scan Firewalla QR")
        }

        // Paste .conf fallback
        Text("Or paste .conf text:", style = MaterialTheme.typography.labelMedium)
        OutlinedTextField(
            value = pasteField,
            onValueChange = {
                pasteField = it
                configError = null
            },
            label = { Text("[Interface] / [Peer] …") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 4,
            maxLines = 8,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                val text = pasteField.trim()
                val parsed = parseWgConfig(text)
                if (parsed.isFailure) {
                    configError = parsed.exceptionOrNull()?.message ?: "Invalid config"
                } else {
                    configError = null
                    scope.launch {
                        wgConfigStore.save(text)
                        savedConf = text
                        pasteField = ""
                    }
                }
            }) { Text("Save config") }

            OutlinedButton(onClick = {
                scope.launch {
                    wgConfigStore.clear()
                    savedConf = null
                }
            }) { Text("Clear") }
        }
        if (configError != null) {
            Text(
                configError!!,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall
            )
        }
        if (savedConf != null) {
            Text("Profile saved.", style = MaterialTheme.typography.bodySmall)
        }

        // Connect / Disconnect
        val canConnect = savedConf != null && tunnelStatus == TunnelStatus.Down
        val canDisconnect = tunnelStatus == TunnelStatus.Up
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                enabled = canConnect,
                onClick = {
                    val conf = savedConf ?: return@Button
                    val consentIntent = VpnService.prepare(context)
                    if (consentIntent != null) {
                        pendingConnect = true
                        vpnConsentLauncher.launch(consentIntent)
                    } else {
                        scope.launch { vpnController.connect(conf) }
                    }
                }
            ) { Text("Connect") }

            OutlinedButton(
                enabled = canDisconnect,
                onClick = { scope.launch { vpnController.disconnect() } }
            ) { Text("Disconnect") }
        }
    }
}
