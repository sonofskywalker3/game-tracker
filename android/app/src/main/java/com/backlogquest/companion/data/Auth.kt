package com.backlogquest.companion.data

import java.io.IOException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.Route

/** Zero-touch auth: on any 401, exchange the build-time password for a bearer
 *  token, persist it, and retry the original request with the new token.
 *  Single-flight: concurrent 401s serialize on this object's lock and reuse
 *  the first refresh instead of logging in again. A wrong baked password makes
 *  /login itself 401 → we return null → the original failure surfaces through
 *  the normal Result/error path (one login attempt per request, never a loop). */
class TokenAuthenticator(
    private val settings: SettingsStore,
    private val password: String,
    private val baseUrl: String = DEFAULT_BASE_URL,
    private val loginClient: OkHttpClient = OkHttpClient(),
    private val json: Json = appJson(),
) : Authenticator {

    override fun authenticate(route: Route?, response: Response): Request? {
        if (responseCount(response) >= MAX_ATTEMPTS_PER_REQUEST) return null
        val failedToken = response.request.header(AUTH_HEADER)?.removePrefix(BEARER_PREFIX)
        val token = synchronized(this) {
            val current = settings.authTokenBlocking()
            if (current.isNotEmpty() && current != failedToken) current
            else login()?.also { fresh -> runBlocking { settings.setAuthToken(fresh) } }
        } ?: return null
        return response.request.newBuilder()
            .header(AUTH_HEADER, "$BEARER_PREFIX$token")
            .build()
    }

    /** POST /login {"password": …} → token, or null on any failure (wrong
     *  password, unreachable, malformed payload). Null = give up cleanly. */
    private fun login(): String? = try {
        val body = json.encodeToString(LoginBody.serializer(), LoginBody(password))
            .toRequestBody(JSON_MEDIA_TYPE.toMediaType())
        loginClient.newCall(Request.Builder().url("$baseUrl/login").post(body).build())
            .execute().use { resp ->
                val text = resp.body?.string()
                if (!resp.isSuccessful || text == null) null
                else json.decodeFromString(LoginResponse.serializer(), text).token
            }
    } catch (e: IOException) {
        null
    } catch (e: SerializationException) {
        null
    }

    private fun responseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) { count++; prior = prior.priorResponse }
        return count
    }

    private companion object {
        /** 1 original try + 1 retry with a freshly acquired token. */
        const val MAX_ATTEMPTS_PER_REQUEST = 2
        const val AUTH_HEADER = "Authorization"
        const val BEARER_PREFIX = "Bearer "
        const val JSON_MEDIA_TYPE = "application/json"
    }
}
