package com.gametracker.companion.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private class FakeSettings(initial: String) : SettingsStore {
    private val state = MutableStateFlow(initial)
    override val baseUrl: Flow<String> = state
    override fun baseUrlBlocking(): String = state.value
    override suspend fun setBaseUrl(url: String) { state.value = url }
}

class RepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repo: Repository

    @Before fun setUp() {
        server = MockWebServer()
        server.start()
        val settings = FakeSettings(server.url("/").toString().trimEnd('/'))
        val client = OkHttpClient.Builder()
            .addInterceptor(dynamicHostInterceptor(settings))
            .build()
        repo = Repository(buildApi(client, appJson()))
    }

    @After fun tearDown() { server.shutdown() }

    @Test fun games_parses_list_and_nullable_fields() = runTest {
        server.enqueue(MockResponse().setBody(
            """[{"id":1,"title":"Halo","cover_url":null,"status":"backlog",
                 "rating":null,"hours_played":null,"platforms":["xbox"],
                 "categories":["console"],"tags":[],"physical":true,"series_name":null}]"""
        ))
        val result = repo.games()
        assertTrue(result.isSuccess)
        val games = result.getOrThrow()
        assertEquals(1, games.size)
        assertEquals("Halo", games[0].title)
        assertEquals(null, games[0].coverUrl)
        assertEquals(listOf("xbox"), games[0].platforms)
    }

    @Test fun dynamic_host_uses_current_setting() = runTest {
        // The request must hit the MockWebServer host:port from settings, not a baked URL.
        server.enqueue(MockResponse().setBody("[]"))
        repo.games()
        val recorded = server.takeRequest()
        assertEquals("/api/games", recorded.path)
    }

    @Test fun slots_parses_wrapper_and_nullable_current_game() = runTest {
        server.enqueue(MockResponse().setBody(
            """{"slots":[{"id":1,"label":"Quick","goal":null,"sort_order":0,
                 "current_game":null,"candidates":[]}],"recently_finished":[]}"""
        ))
        val result = repo.slots()
        assertTrue(result.isSuccess)
        assertEquals(1, result.getOrThrow().slots.size)
        assertEquals(null, result.getOrThrow().slots[0].currentGame)
    }

    @Test fun http_error_is_failure_not_crash() = runTest {
        server.enqueue(MockResponse().setResponseCode(500))
        val result = repo.games()
        assertTrue(result.isFailure)
    }

    @Test fun set_status_sends_put_with_body() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true}"""))
        repo.setStatus(7, "playing")
        val recorded = server.takeRequest()
        assertEquals("PUT", recorded.method)
        assertEquals("/api/games/7", recorded.path)
        assertTrue(recorded.body.readUtf8().contains("\"playing\""))
    }
}
