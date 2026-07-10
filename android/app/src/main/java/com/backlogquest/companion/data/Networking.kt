package com.backlogquest.companion.data

import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory

fun appJson(): Json = Json { ignoreUnknownKeys = true; explicitNulls = false }

/** Attaches the stored bearer token as `Authorization: Bearer <token>` to every
 *  request when signed in. When no token is stored yet the request is sent
 *  unchanged; the server's 401 then triggers TokenAuthenticator's auto-login. */
fun authInterceptor(settings: SettingsStore): Interceptor = Interceptor { chain ->
    val token = settings.authTokenBlocking()
    val req = chain.request()
    val authed = if (token.isNotEmpty())
        req.newBuilder().header("Authorization", "Bearer $token").build()
    else req
    chain.proceed(authed)
}

fun buildApi(client: OkHttpClient, json: Json, baseUrl: String = DEFAULT_BASE_URL): BacklogQuestApi =
    Retrofit.Builder()
        .baseUrl(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/")
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(BacklogQuestApi::class.java)
