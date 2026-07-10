package com.backlogquest.companion.data

import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory

fun appJson(): Json = Json { ignoreUnknownKeys = true; explicitNulls = false }

/** Rewrites every request's scheme/host/port to the current Settings base URL,
 *  so changing the URL (or pointing at the VPN endpoint) needs no rebuild. */
fun dynamicHostInterceptor(settings: SettingsStore): Interceptor = Interceptor { chain ->
    val base = settings.baseUrlBlocking().toHttpUrl()
    val req = chain.request()
    val newUrl = req.url.newBuilder()
        .scheme(base.scheme)
        .host(base.host)
        .port(base.port)
        .build()
    chain.proceed(req.newBuilder().url(newUrl).build())
}

/** Attaches the stored bearer token as `Authorization: Bearer <token>` to every
 *  request when signed in, so the authenticated cloud backend accepts the app's
 *  calls. When no token is stored the request is sent unchanged (the backend's
 *  /login is the only route reachable unauthenticated). */
fun authInterceptor(settings: SettingsStore): Interceptor = Interceptor { chain ->
    val token = settings.authTokenBlocking()
    val req = chain.request()
    val authed = if (token.isNotEmpty())
        req.newBuilder().header("Authorization", "Bearer $token").build()
    else req
    chain.proceed(authed)
}

fun buildApi(client: OkHttpClient, json: Json): BacklogQuestApi =
    Retrofit.Builder()
        // Placeholder base; the interceptor swaps host/port per request.
        .baseUrl("http://placeholder.invalid/")
        .client(client)
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(BacklogQuestApi::class.java)
