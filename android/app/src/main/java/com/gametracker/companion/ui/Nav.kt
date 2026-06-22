package com.gametracker.companion.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
    Tab("settings", "Settings", Icons.Filled.Settings),
)

@Composable
fun AppNav() {
    val nav = rememberNavController()
    Scaffold(bottomBar = {
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
    }) { padding ->
        NavHost(nav, startDestination = "picks", modifier = Modifier.padding(padding)) {
            composable("picks") {
                com.gametracker.companion.ui.picks.PicksScreen(onOpenGame = { id -> nav.navigate("detail/$id") })
            }
            composable("library") {
                com.gametracker.companion.ui.library.LibraryScreen(onOpenGame = { id -> nav.navigate("detail/$id") })
            }
            composable("settings") { com.gametracker.companion.ui.settings.SettingsScreen() }
            composable("detail/{id}") { entry ->
                val id = entry.arguments?.getString("id")?.toIntOrNull() ?: return@composable
                com.gametracker.companion.ui.detail.DetailScreen(gameId = id)
            }
        }
    }
}
