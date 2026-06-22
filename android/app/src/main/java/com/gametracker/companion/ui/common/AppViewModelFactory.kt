package com.gametracker.companion.ui.common

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import com.gametracker.companion.App
import com.gametracker.companion.AppContainer
import com.gametracker.companion.ui.settings.SettingsViewModel

class AppViewModelFactory(private val c: AppContainer) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T =
        when {
            modelClass.isAssignableFrom(SettingsViewModel::class.java) ->
                SettingsViewModel(c.settings, c.repository) as T
            else -> throw IllegalArgumentException("Unknown VM ${modelClass.name}")
        }
}

@Composable
fun rememberAppFactory(): AppViewModelFactory {
    val app = LocalContext.current.applicationContext as App
    return AppViewModelFactory(app.container)
}
