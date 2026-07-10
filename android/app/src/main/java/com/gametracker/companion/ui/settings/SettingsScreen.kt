package com.gametracker.companion.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavBackStackEntry
import com.gametracker.companion.ui.common.AppScaffold
import com.gametracker.companion.ui.common.rememberAppFactory

@Composable
fun SettingsScreen(
    backStackEntry: NavBackStackEntry? = null,
    onNavigateToQrScan: () -> Unit = {},
) {
    val vm: SettingsViewModel = viewModel(factory = rememberAppFactory())
    val saved by vm.baseUrl.collectAsState()
    val result by vm.testResult.collectAsState()
    val loginState by vm.loginState.collectAsState()
    val loggedIn by vm.loggedIn.collectAsState()
    var field by remember(saved) { mutableStateOf(saved) }
    var password by remember { mutableStateOf("") }

    AppScaffold(title = "Settings") { pad ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(pad)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // ── Backend URL ──────────────────────────────────────────────────────
            Text("Backend URL", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = field,
                onValueChange = { field = it },
                label = { Text("https://backlogquest.xyz") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { vm.save(field) }) { Text("Save") }
                OutlinedButton(onClick = { vm.test() }) { Text("Test connection") }
            }
            when (result) {
                TestResult.Testing -> Text("Testing…")
                TestResult.Ok      -> Text("Connected ✓")
                TestResult.Failed  -> Text("Can't reach the server — check the URL and that you're signed in.")
                TestResult.Idle    -> {}
            }

            Spacer(Modifier.height(8.dp))

            // ── Sign in ──────────────────────────────────────────────────────────
            Text("Sign in", style = MaterialTheme.typography.titleMedium)
            if (loggedIn) {
                Text("Signed in ✓", style = MaterialTheme.typography.bodyMedium)
                OutlinedButton(onClick = { vm.logout() }) { Text("Sign out") }
            } else {
                Text(
                    "Enter your BacklogQuest password once; this device stays signed in.",
                    style = MaterialTheme.typography.bodySmall
                )
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text("Password") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth()
                )
                Button(
                    enabled = password.isNotBlank() && loginState != LoginState.LoggingIn,
                    onClick = { vm.login(password) }
                ) { Text("Sign in") }
                when (loginState) {
                    LoginState.LoggingIn -> Text("Signing in…")
                    LoginState.Ok        -> Text("Signed in ✓")
                    LoginState.Failed    -> Text(
                        "Wrong password or can't reach the server.",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall
                    )
                    LoginState.Idle      -> {}
                }
            }
        }
    }
}
