package com.backlogquest.companion.data

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private class AuthFakeSettings(initialToken: String = "") : SettingsStore {
    private val tokenState = MutableStateFlow(initialToken)
    override val authToken: Flow<String> = tokenState
    override fun authTokenBlocking(): String = tokenState.value
    override suspend fun setAuthToken(token: String) { tokenState.value = token }
}

class TokenAuthenticatorTest {
    private lateinit var server: MockWebServer

    @Before fun setUp() { server = MockWebServer(); server.start() }
    @After fun tearDown() { server.shutdown() }

    private fun base() = server.url("/").toString().trimEnd('/')

    private fun repo(settings: SettingsStore): Repository {
        val client = OkHttpClient.Builder()
            .addInterceptor(authInterceptor(settings))
            .authenticator(TokenAuthenticator(settings, "pw-baked", baseUrl = base()))
            .build()
        return Repository(buildApi(client, appJson(), base()))
    }

    @Test fun fresh_401_logs_in_stores_token_and_retries() = runTest {
        val settings = AuthFakeSettings()
        server.enqueue(MockResponse().setResponseCode(401))            // API rejects (no token yet)
        server.enqueue(MockResponse().setBody("""{"token":"t1"}"""))   // /login succeeds
        server.enqueue(MockResponse().setBody("[]"))                   // retried API call
        val result = repo(settings).games()
        assertTrue(result.isSuccess)
        assertEquals("t1", settings.authTokenBlocking())
        assertEquals(null, server.takeRequest().getHeader("Authorization")) // original, tokenless
        val login = server.takeRequest()
        assertEquals("/login", login.path)
        assertTrue(login.body.readUtf8().contains("\"password\":\"pw-baked\""))
        assertEquals("Bearer t1", server.takeRequest().getHeader("Authorization"))
    }

    @Test fun wrong_password_gives_up_without_looping() = runTest {
        val settings = AuthFakeSettings()
        server.enqueue(MockResponse().setResponseCode(401))            // API rejects
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"invalid password"}"""))
        val result = repo(settings).games()
        assertTrue(result.isFailure)
        assertEquals(2, server.requestCount)                           // exactly one login attempt
        assertEquals("", settings.authTokenBlocking())
    }

    @Test fun reuses_token_already_refreshed_by_a_concurrent_request() {
        val settings = AuthFakeSettings(initialToken = "fresh")
        val auth = TokenAuthenticator(settings, "pw-baked", baseUrl = base())
        val retry = auth.authenticate(null, response401(token = "stale"))
        assertEquals("Bearer fresh", retry?.header("Authorization"))
        assertEquals(0, server.requestCount)                           // no /login call
    }

    @Test fun gives_up_after_one_authenticated_retry() {
        val settings = AuthFakeSettings(initialToken = "t1")
        val auth = TokenAuthenticator(settings, "pw-baked", baseUrl = base())
        val first = response401(token = "t1")
        val second = response401(token = "t1", prior = first)
        assertNull(auth.authenticate(null, second))
        assertEquals(0, server.requestCount)
    }

    private fun response401(token: String?, prior: Response? = null): Response {
        val req = Request.Builder().url(server.url("/api/games"))
            .apply { if (token != null) header("Authorization", "Bearer $token") }
            .build()
        return Response.Builder().request(req).protocol(Protocol.HTTP_1_1)
            .code(401).message("Unauthorized")
            .apply { if (prior != null) priorResponse(prior) }
            .build()
    }
}
