package com.backlogquest.companion.ui

import com.backlogquest.companion.data.*

/** Builds a real Repository backed by a stub BacklogQuestApi, so ViewModels under
 *  test exercise the real Repository code path. */
class FakeRepo(
    private val reachable: Boolean = true,
    private val gamesList: List<GameSummary> = emptyList(),
    private val detail: GameDetail? = null,
    private val slotsResp: SlotsResponse = SlotsResponse(),
    private val igdb: List<IgdbResult> = emptyList(),
    private val resolveResp: BarcodeResolveResponse = BarcodeResolveResponse("", "none"),
) {
    val pinned = mutableListOf<Triple<Int, Int, String?>>()
    val outcomes = mutableListOf<Pair<Int, String>>()
    val statusSets = mutableListOf<Pair<Int, String>>()
    val reorders = mutableListOf<List<Int>>()
    val created = mutableListOf<CreateGameBody>()
    val addedPlatforms = mutableListOf<Pair<Int, AddPlatformPayload>>()
    val linked = mutableListOf<BarcodeLinkBody>()

    private val api = object : BacklogQuestApi {
        override suspend fun login(body: LoginBody): LoginResponse =
            if (reachable) LoginResponse("fake-token-123") else throw RuntimeException("bad password")
        override suspend fun games(status: String?, platform: String?, search: String?, sort: String?) =
            if (reachable) gamesList else throw RuntimeException("unreachable")
        override suspend fun game(id: Int) =
            if (!reachable) throw RuntimeException("unreachable")
            else detail ?: throw RuntimeException("no detail")
        override suspend fun updateGame(id: Int, body: StatusBody) { statusSets += id to body.status }
        override suspend fun igdbSearch(q: String) = igdb
        override suspend fun createGame(body: CreateGameBody): CreateGameResponse {
            created += body
            return if (reachable) CreateGameResponse(gameId = 1)
                   else throw RuntimeException("unreachable")
        }
        override suspend fun addPlatform(id: Int, body: AddPlatformBody) {
            addedPlatforms += id to body.add_platform
            if (!reachable) throw RuntimeException("unreachable")
        }
        override suspend fun resolveBarcode(upc: String): BarcodeResolveResponse =
            if (reachable) resolveResp else throw RuntimeException("unreachable")
        override suspend fun linkBarcode(body: BarcodeLinkBody) {
            linked += body
            if (!reachable) throw RuntimeException("unreachable")
        }
        override suspend fun slots() =
            if (reachable) slotsResp else throw RuntimeException("unreachable")
        override suspend fun pin(id: Int, body: PinBody) { pinned += Triple(id, body.game_id, body.goal) }
        override suspend fun outcome(id: Int, body: OutcomeBody) { outcomes += id to body.outcome }
        override suspend fun goal(id: Int, body: GoalBody) {}
        override suspend fun reorderSlots(body: ReorderBody) { reorders += body.slot_ids }
    }

    fun asRepository() = Repository(api)
}
