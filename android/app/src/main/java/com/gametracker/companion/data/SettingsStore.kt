package com.gametracker.companion.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking

const val DEFAULT_BASE_URL = "https://backlogquest.xyz"

interface SettingsStore {
    val baseUrl: Flow<String>
    fun baseUrlBlocking(): String
    suspend fun setBaseUrl(url: String)

    /** Bearer token from a successful /login, attached to every API request.
     *  Empty string means "not signed in". */
    val authToken: Flow<String>
    fun authTokenBlocking(): String
    suspend fun setAuthToken(token: String)
}

private val Context.dataStore by preferencesDataStore(name = "settings")
private val BASE_URL_KEY = stringPreferencesKey("base_url")
private val AUTH_TOKEN_KEY = stringPreferencesKey("auth_token")

class DataStoreSettings(private val context: Context) : SettingsStore {
    override val baseUrl: Flow<String> =
        context.dataStore.data.map { it[BASE_URL_KEY] ?: DEFAULT_BASE_URL }

    override fun baseUrlBlocking(): String = runBlocking { baseUrl.first() }

    override suspend fun setBaseUrl(url: String) {
        context.dataStore.edit { it[BASE_URL_KEY] = url.trim().trimEnd('/') }
    }

    override val authToken: Flow<String> =
        context.dataStore.data.map { it[AUTH_TOKEN_KEY] ?: "" }

    override fun authTokenBlocking(): String = runBlocking { authToken.first() }

    override suspend fun setAuthToken(token: String) {
        context.dataStore.edit { it[AUTH_TOKEN_KEY] = token.trim() }
    }
}
