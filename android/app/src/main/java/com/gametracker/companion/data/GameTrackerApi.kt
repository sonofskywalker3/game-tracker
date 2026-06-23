package com.gametracker.companion.data

import kotlinx.serialization.Serializable
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

@Serializable data class StatusBody(val status: String)
@Serializable data class PinBody(val game_id: Int, val goal: String? = null)
@Serializable data class OutcomeBody(val outcome: String, val chase: Boolean? = null, val new_goal: String? = null)
@Serializable data class GoalBody(val goal: String?)
@Serializable data class ReorderBody(val slot_ids: List<Int>)
@Serializable data class CreateGameBody(val title: String, val cover_url: String? = null,
                                        val platforms: List<String> = emptyList(),
                                        val physical: Boolean = false,
                                        val upc: String? = null)
@Serializable data class AddPlatformPayload(val short_name: String, val format: String? = null,
                                            val upc: String? = null)
@Serializable data class AddPlatformBody(val add_platform: AddPlatformPayload)

interface GameTrackerApi {
    @GET("api/games")
    suspend fun games(
        @Query("status") status: String? = null,
        @Query("platform") platform: String? = null,
        @Query("search") search: String? = null,
        @Query("sort") sort: String? = null,
    ): List<GameSummary>

    @GET("api/games/{id}")
    suspend fun game(@Path("id") id: Int): GameDetail

    @PUT("api/games/{id}")
    suspend fun updateGame(@Path("id") id: Int, @Body body: StatusBody)

    @PUT("api/games/{id}")
    suspend fun addPlatform(@Path("id") id: Int, @Body body: AddPlatformBody)

    @GET("api/igdb/search")
    suspend fun igdbSearch(@Query("q") q: String): List<IgdbResult>

    @GET("api/barcode/resolve")
    suspend fun resolveBarcode(@Query("upc") upc: String): BarcodeResolveResponse

    @POST("api/games")
    suspend fun createGame(@Body body: CreateGameBody): CreateGameResponse

    @GET("api/slots")
    suspend fun slots(): SlotsResponse

    @POST("api/slots/{id}/pin")
    suspend fun pin(@Path("id") id: Int, @Body body: PinBody)

    @POST("api/slots/{id}/outcome")
    suspend fun outcome(@Path("id") id: Int, @Body body: OutcomeBody)

    @PATCH("api/slots/{id}/goal")
    suspend fun goal(@Path("id") id: Int, @Body body: GoalBody)

    @POST("api/slots/reorder")
    suspend fun reorderSlots(@Body body: ReorderBody)
}
