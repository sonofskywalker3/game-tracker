package com.gametracker.companion.data

class Repository(private val api: GameTrackerApi) {

    suspend fun games(status: String? = null, platform: String? = null,
                      search: String? = null): Result<List<GameSummary>> =
        runCatching { api.games(status = status, platform = platform, search = search) }

    suspend fun game(id: Int): Result<GameDetail> = runCatching { api.game(id) }

    suspend fun setStatus(id: Int, status: String): Result<Unit> =
        runCatching { api.updateGame(id, StatusBody(status)) }

    suspend fun igdbSearch(q: String): Result<List<IgdbResult>> =
        runCatching { api.igdbSearch(q) }

    suspend fun slots(): Result<SlotsResponse> = runCatching { api.slots() }

    suspend fun pin(slotId: Int, gameId: Int, goal: String?): Result<Unit> =
        runCatching { api.pin(slotId, PinBody(gameId, goal)) }

    suspend fun outcome(slotId: Int, outcome: String, chase: Boolean = false,
                        newGoal: String? = null): Result<Unit> =
        runCatching { api.outcome(slotId, OutcomeBody(outcome, chase, newGoal)) }

    suspend fun setGoal(slotId: Int, goal: String?): Result<Unit> =
        runCatching { api.goal(slotId, GoalBody(goal)) }

    suspend fun reorderSlots(slotIds: List<Int>): Result<Unit> =
        runCatching { api.reorderSlots(ReorderBody(slotIds)) }
}
