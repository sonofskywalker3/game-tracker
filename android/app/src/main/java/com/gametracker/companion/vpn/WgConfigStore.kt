package com.gametracker.companion.vpn

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.wgStore by preferencesDataStore(name = "wg")
private val WG_TEXT = stringPreferencesKey("wg_conf")

class WgConfigStore(private val context: Context) {
    val raw: Flow<String?> = context.wgStore.data.map { it[WG_TEXT] }
    suspend fun save(text: String) { context.wgStore.edit { it[WG_TEXT] = text } }
    suspend fun clear() { context.wgStore.edit { it.remove(WG_TEXT) } }
}
