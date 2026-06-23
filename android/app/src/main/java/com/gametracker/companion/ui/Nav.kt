package com.gametracker.companion.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.FabPosition
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController

private data class Tab(val route: String, val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)

private val TABS = listOf(
    Tab("picks", "Picks", Icons.Filled.Home),
    Tab("library", "Library", Icons.AutoMirrored.Filled.List),
    Tab("add", "Add", Icons.Filled.Add),
    Tab("settings", "Settings", Icons.Filled.Settings),
)

@Composable
fun AppNav(initialTab: String? = null) {
    val nav = rememberNavController()
    LaunchedEffect(initialTab) {
        if (initialTab != null) nav.navigate(initialTab) { launchSingleTop = true }
    }
    Scaffold(
        bottomBar = {
            val entry by nav.currentBackStackEntryAsState()
            val current = entry?.destination?.route
            NavigationBar {
                TABS.forEach { tab ->
                    NavigationBarItem(
                        selected = current == tab.route,
                        onClick = { nav.navigate(tab.route) { launchSingleTop = true } },
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
        floatingActionButton = {
            val entry by nav.currentBackStackEntryAsState()
            val route = entry?.destination?.route
            // Show the scan FAB only on the Add screen (not on scan, detail, settings, etc.).
            if (route?.startsWith("add") == true) {
                FloatingActionButton(onClick = { nav.navigate("scan") }) {
                    Icon(Icons.Filled.QrCodeScanner, contentDescription = "Scan barcode")
                }
            }
        },
        floatingActionButtonPosition = FabPosition.Center,
    ) { padding ->
        NavHost(nav, startDestination = "picks", modifier = Modifier.padding(padding)) {
            composable("picks") {
                com.gametracker.companion.ui.picks.PicksScreen(onOpenGame = { id -> nav.navigate("detail/$id") })
            }
            composable("library") {
                com.gametracker.companion.ui.library.LibraryScreen(onOpenGame = { id -> nav.navigate("detail/$id") })
            }
            composable(
                "add?prefill={prefill}&upc={upc}",
                arguments = listOf(
                    androidx.navigation.navArgument("prefill") { nullable = true; defaultValue = null },
                    androidx.navigation.navArgument("upc") { nullable = true; defaultValue = null },
                ),
            ) { entry ->
                com.gametracker.companion.ui.add.AddScreen(
                    initialQuery = entry.arguments?.getString("prefill"),
                    pendingUpc = entry.arguments?.getString("upc"),
                    onOpenGame = { id -> nav.navigate("detail/$id?added=true") },
                )
            }
            composable("settings") { backStackEntry ->
                com.gametracker.companion.ui.settings.SettingsScreen(
                    backStackEntry = backStackEntry,
                    onNavigateToQrScan = { nav.navigate("vpn-scan") }
                )
            }
            composable(
                "detail/{id}?added={added}",
                arguments = listOf(
                    androidx.navigation.navArgument("added") { defaultValue = "false" },
                ),
            ) { entry ->
                val id = entry.arguments?.getString("id")?.toIntOrNull() ?: return@composable
                val added = entry.arguments?.getString("added") == "true"
                com.gametracker.companion.ui.detail.DetailScreen(
                    gameId = id,
                    justAdded = added,
                    onBack = { nav.popBackStack() },
                )
            }
            composable("scan") {
                com.gametracker.companion.ui.scan.ScanScreen(
                    onOpenGame = { id -> nav.navigate("detail/$id") },
                    onManualSearch = { productTitle, upc ->
                        val q = productTitle?.let { java.net.URLEncoder.encode(it, "UTF-8") } ?: ""
                        nav.navigate("add?prefill=$q&upc=$upc") {
                            popUpTo("scan") { inclusive = true }
                        }
                    },
                )
            }
            composable("vpn-scan") {
                com.gametracker.companion.ui.vpn.QrScanScreen(onConfig = { conf ->
                    // Pass the raw conf text back to the Settings back-stack entry,
                    // then pop back to Settings. SettingsScreen reads + validates it.
                    nav.previousBackStackEntry
                        ?.savedStateHandle
                        ?.set("scanned_conf", conf)
                    nav.popBackStack()
                })
            }
        }
    }
}
