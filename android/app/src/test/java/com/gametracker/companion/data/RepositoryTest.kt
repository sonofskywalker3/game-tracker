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

private class FakeSettings(initial: String, initialToken: String = "") : SettingsStore {
    private val state = MutableStateFlow(initial)
    private val tokenState = MutableStateFlow(initialToken)
    override val baseUrl: Flow<String> = state
    override fun baseUrlBlocking(): String = state.value
    override suspend fun setBaseUrl(url: String) { state.value = url }
    override val authToken: Flow<String> = tokenState
    override fun authTokenBlocking(): String = tokenState.value
    override suspend fun setAuthToken(token: String) { tokenState.value = token }
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

    @Test fun resolveBarcode_parses_candidates_and_ownership() = runTest {
        server.enqueue(MockResponse().setBody(
            """{"upc":"711","source":"upc_api","candidates":[
                 {"igdb_id":119171,"title":"Spider-Man 2","platform":"ps5",
                  "cover_url":"https://x/c.jpg","owned_game_id":42}]}"""
        ))
        val r = repo.resolveBarcode("711")
        assertTrue(r.isSuccess)
        val body = r.getOrThrow()
        assertEquals("upc_api", body.source)
        assertEquals(119171, body.candidates[0].igdbId)
        assertEquals(42, body.candidates[0].ownedGameId)
    }

    @Test fun resolveBarcode_source_none_empty_candidates() = runTest {
        server.enqueue(MockResponse().setBody("""{"upc":"999","source":"none","candidates":[]}"""))
        val body = repo.resolveBarcode("999").getOrThrow()
        assertEquals("none", body.source)
        assertTrue(body.candidates.isEmpty())
    }

    @Test fun resolveBarcode_http_error_is_failure() = runTest {
        server.enqueue(MockResponse().setResponseCode(500))
        assertTrue(repo.resolveBarcode("1").isFailure)
    }

    @Test fun createGame_posts_upc_and_physical() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true,"game_id":7}"""))
        val r = repo.createGame(title = "Halo", platforms = listOf("xbox"),
                                physical = true, upc = "upc-1")
        assertTrue(r.isSuccess)
        assertEquals(7, r.getOrThrow().gameId)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/api/games", recorded.path)
        val sent = recorded.body.readUtf8()
        assertTrue(sent.contains("\"upc\":\"upc-1\""))
        assertTrue(sent.contains("\"physical\":true"))
    }

    @Test fun addPlatform_sends_put_with_add_platform_body() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true}"""))
        val r = repo.addPlatform(341, "Switch", "physical", "upc-9")
        assertTrue(r.isSuccess)
        val recorded = server.takeRequest()
        assertEquals("PUT", recorded.method)
        assertEquals("/api/games/341", recorded.path)
        val sent = recorded.body.readUtf8()
        assertTrue(sent.contains("\"add_platform\""))
        assertTrue(sent.contains("\"short_name\":\"Switch\""))
        assertTrue(sent.contains("\"format\":\"physical\""))
        assertTrue(sent.contains("\"upc\":\"upc-9\""))
    }

    @Test fun slots_parses_real_payload_with_wrapper_candidates_and_recently_finished() = runTest {
        // Mirrors the live /api/slots shape: current_game is a full game row (extra keys
        // ignored); each candidate is a {game, reasons, score, time_to_beat_minutes} wrapper;
        // recently_finished rows are keyed by game_id (not id). Regression for the parse
        // failure that left Picks stuck on its error state.
        server.enqueue(MockResponse().setBody(
            """{"slots":[{"id":2,"label":"Switch · Long","goal":null,"sort_order":0,
                 "current_game_id":688,"completionist":0,
                 "current_game":{"id":688,"title":"Kirby","cover_url":"https://x/k.jpg",
                                 "igdb_id":1,"normalized_title":"kirby"},
                 "candidates":[
                   {"game":{"id":42,"title":"Advance Wars","cover_url":null,"igdb_id":9},
                    "reasons":["short"],"score":1.5,"time_to_beat_minutes":600}]}],
               "recently_finished":[
                 {"game_id":7,"title":"FF7R","cover_url":null,"outcome":"completed",
                  "removed_at":"2026-06-20"}]}"""
        ))
        val result = repo.slots()
        assertTrue(result.isSuccess)
        val state = result.getOrThrow()
        assertEquals(1, state.slots.size)
        assertEquals(688, state.slots[0].currentGame?.id)
        assertEquals("Advance Wars", state.slots[0].candidates[0].game.title)
        assertEquals(42, state.slots[0].candidates[0].game.id)
        assertEquals(7, state.recentlyFinished[0].gameId)
    }

    @Test fun link_posts_barcode_link_body() = runTest {
        server.enqueue(MockResponse().setBody("""{"success":true}"""))
        val result = repo.link("U1", 7, "Celeste", "cov", "Switch", null)
        assertTrue(result.isSuccess)
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/api/barcode/link", recorded.path)
        val sent = recorded.body.readUtf8()
        assertTrue(sent.contains("\"upc\":\"U1\""))
        assertTrue(sent.contains("\"igdb_id\":7"))
        assertTrue(sent.contains("\"title\":\"Celeste\""))
        assertTrue(sent.contains("\"cover_url\":\"cov\""))
        assertTrue(sent.contains("\"platform\":\"Switch\""))
    }

    @Test fun login_posts_password_and_returns_token() = runTest {
        server.enqueue(MockResponse().setBody("""{"token":"abc123"}"""))
        val result = repo.login("hunter2")
        assertTrue(result.isSuccess)
        assertEquals("abc123", result.getOrThrow())
        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/login", recorded.path)
        assertTrue(recorded.body.readUtf8().contains("\"password\":\"hunter2\""))
    }

    @Test fun login_wrong_password_is_failure() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"error":"invalid password"}"""))
        assertTrue(repo.login("nope").isFailure)
    }

    @Test fun auth_interceptor_adds_bearer_header_when_token_present() = runTest {
        val settings = FakeSettings(server.url("/").toString().trimEnd('/'), initialToken = "tok-xyz")
        val client = OkHttpClient.Builder()
            .addInterceptor(dynamicHostInterceptor(settings))
            .addInterceptor(authInterceptor(settings))
            .build()
        val authedRepo = Repository(buildApi(client, appJson()))
        server.enqueue(MockResponse().setBody("[]"))
        authedRepo.games()
        assertEquals("Bearer tok-xyz", server.takeRequest().getHeader("Authorization"))
    }

    @Test fun auth_interceptor_omits_header_when_no_token() = runTest {
        val settings = FakeSettings(server.url("/").toString().trimEnd('/'), initialToken = "")
        val client = OkHttpClient.Builder()
            .addInterceptor(dynamicHostInterceptor(settings))
            .addInterceptor(authInterceptor(settings))
            .build()
        val plainRepo = Repository(buildApi(client, appJson()))
        server.enqueue(MockResponse().setBody("[]"))
        plainRepo.games()
        assertEquals(null, server.takeRequest().getHeader("Authorization"))
    }
}
