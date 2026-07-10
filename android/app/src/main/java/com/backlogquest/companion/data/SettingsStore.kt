package com.backlogquest.companion.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking

/** Single-user cloud build: the backend address is fixed. */
const val DEFAULT_BASE_URL = "https://backlogquest.xyz"

interface SettingsStore {
    /** Bearer token from a successful /login, attached to every API request.
     *  Empty string means "not signed in (yet)" — TokenAuthenticator fills it. */
    val authToken: Flow<String>
    fun authTokenBlocking(): String
    suspend fun setAuthToken(token: String)
}

private val Context.dataStore by preferencesDataStore(name = "settings")
private val AUTH_TOKEN_KEY = stringPreferencesKey("auth_token")

class DataStoreSettings(private val context: Context) : SettingsStore {
    override val authToken: Flow<String> =
        context.dataStore.data.map { it[AUTH_TOKEN_KEY] ?: "" }

    override fun authTokenBlocking(): String = runBlocking { authToken.first() }

    override suspend fun setAuthToken(token: String) {
        context.dataStore.edit { it[AUTH_TOKEN_KEY] = token.trim() }
    }
}
