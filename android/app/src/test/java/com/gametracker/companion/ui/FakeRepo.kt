package com.gametracker.companion.ui

import com.gametracker.companion.data.*

/** Builds a real Repository backed by a stub GameTrackerApi, so ViewModels under
 *  test exercise the real Repository code path. */
class FakeRepo(
    private val reachable: Boolean = true,
    private val gamesList: List<GameSummary> = emptyList(),
    private val detail: GameDetail? = null,
    private val slotsResp: SlotsResponse = SlotsResponse(),
    private val igdb: List<IgdbResult> = emptyList(),
) {
    val pinned = mutableListOf<Triple<Int, Int, String?>>()
    val outcomes = mutableListOf<Pair<Int, String>>()
    val statusSets = mutableListOf<Pair<Int, String>>()
    val reorders = mutableListOf<List<Int>>()

    private val api = object : GameTrackerApi {
        override suspend fun games(status: String?, platform: String?, search: String?, sort: String?) =
            if (reachable) gamesList else throw RuntimeException("unreachable")
        override suspend fun game(id: Int) = detail ?: throw RuntimeException("no detail")
        override suspend fun updateGame(id: Int, body: StatusBody) { statusSets += id to body.status }
        override suspend fun igdbSearch(q: String) = igdb
        override suspend fun createGame(body: CreateGameBody) = CreateGameResponse(gameId = 1)
        override suspend fun slots() = slotsResp
        override suspend fun pin(id: Int, body: PinBody) { pinned += Triple(id, body.game_id, body.goal) }
        override suspend fun outcome(id: Int, body: OutcomeBody) { outcomes += id to body.outcome }
        override suspend fun goal(id: Int, body: GoalBody) {}
        override suspend fun reorderSlots(body: ReorderBody) { reorders += body.slot_ids }
    }

    fun asRepository() = Repository(api)
}
