package com.gametracker.companion.ui.common

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable

/** Shared chrome for every screen: a Material 3 [TopAppBar] with [title], an optional
 *  back affordance, optional [actions], and an optional snackbar host. Content receives
 *  the inset [PaddingValues] (top-bar height + any snackbar) to apply itself. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppScaffold(
    title: String,
    onBack: (() -> Unit)? = null,
    snackbarHostState: SnackbarHostState? = null,
    actions: @Composable RowScope.() -> Unit = {},
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title) },
                navigationIcon = {
                    if (onBack != null) {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                        }
                    }
                },
                actions = actions,
                colors = TopAppBarDefaults.topAppBarColors(),
                // The outer nav Scaffold already insets content below the status bar;
                // don't let the top bar add it a second time (was a blank row above).
                windowInsets = WindowInsets(0, 0, 0, 0),
            )
        },
        snackbarHost = { if (snackbarHostState != null) SnackbarHost(snackbarHostState) },
        // Nested inside the nav Scaffold, which already owns all system-bar insets —
        // so this one consumes none (the top bar handles its own via windowInsets=0).
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        content = content,
    )
}
