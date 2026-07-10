package com.backlogquest.companion.ui.common

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import com.backlogquest.companion.App
import com.backlogquest.companion.AppContainer

class AppViewModelFactory(private val c: AppContainer) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T =
        when {
            modelClass.isAssignableFrom(com.backlogquest.companion.ui.picks.PicksViewModel::class.java) ->
                com.backlogquest.companion.ui.picks.PicksViewModel(c.repository) as T
            modelClass.isAssignableFrom(com.backlogquest.companion.ui.library.LibraryViewModel::class.java) ->
                com.backlogquest.companion.ui.library.LibraryViewModel(c.repository) as T
            modelClass.isAssignableFrom(com.backlogquest.companion.ui.detail.DetailViewModel::class.java) ->
                com.backlogquest.companion.ui.detail.DetailViewModel(c.repository) as T
            modelClass.isAssignableFrom(com.backlogquest.companion.ui.add.AddViewModel::class.java) ->
                com.backlogquest.companion.ui.add.AddViewModel(c.repository) as T
            modelClass.isAssignableFrom(com.backlogquest.companion.ui.scan.ScanViewModel::class.java) ->
                com.backlogquest.companion.ui.scan.ScanViewModel(c.repository) as T
            else -> throw IllegalArgumentException("Unknown VM ${modelClass.name}")
        }
}

@Composable
fun rememberAppFactory(): AppViewModelFactory {
    val app = LocalContext.current.applicationContext as App
    return AppViewModelFactory(app.container)
}
