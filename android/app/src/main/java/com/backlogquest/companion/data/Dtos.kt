package com.backlogquest.companion.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class GameSummary(
    val id: Int,
    val title: String,
    @SerialName("cover_url") val coverUrl: String? = null,
    val status: String? = null,
    val rating: Int? = null,
    @SerialName("hours_played") val hoursPlayed: Double? = null,
    val platforms: List<String> = emptyList(),
    val categories: List<String> = emptyList(),
    val tags: List<TagRef> = emptyList(),
    val physical: Boolean = false,
    @SerialName("series_name") val seriesName: String? = null,
)

@Serializable
data class TagRef(val name: String, val category: String? = null)

@Serializable
data class PlatformRef(val id: Int? = null, val name: String? = null,
                       @SerialName("short_name") val shortName: String? = null)

@Serializable
data class DlcRef(val id: Int, val name: String, val kind: String? = null,
                  val owned: Boolean = false, val source: String? = null)

@Serializable
data class GameDetail(
    val id: Int,
    val title: String,
    @SerialName("cover_url") val coverUrl: String? = null,
    val status: String? = null,
    val rating: Int? = null,
    @SerialName("hours_played") val hoursPlayed: Double? = null,
    val notes: String? = null,
    val platforms: List<PlatformRef> = emptyList(),
    val tags: List<TagRef> = emptyList(),
    val dlc: List<DlcRef> = emptyList(),
)

@Serializable
data class SlotCandidate(
    val id: Int,
    val title: String,
    @SerialName("cover_url") val coverUrl: String? = null,
    val status: String? = null,
)

// /api/slots returns each ranked candidate as a wrapper: {game: <full game row>,
// reasons, score, time_to_beat_minutes}. The game's id/title live under `game`.
@Serializable
data class RankedCandidate(
    val game: SlotCandidate,
    val reasons: List<String> = emptyList(),
    val score: Double? = null,
    @SerialName("time_to_beat_minutes") val timeToBeatMinutes: Int? = null,
)

// recently_finished rows are slot_history joined to game title/cover — keyed by
// game_id (not id), so they need their own shape.
@Serializable
data class RecentlyFinished(
    @SerialName("game_id") val gameId: Int? = null,
    val title: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    val outcome: String? = null,
    @SerialName("removed_at") val removedAt: String? = null,
)

@Serializable
data class Slot(
    override val id: Int,
    val label: String,
    val goal: String? = null,
    @SerialName("sort_order") override val sortOrder: Int = 0,
    @SerialName("current_game") val currentGame: SlotCandidate? = null,
    val candidates: List<RankedCandidate> = emptyList(),
    override val windows: List<com.backlogquest.companion.schedule.ScheduleWindow> = emptyList(),
    @SerialName("active_now") val activeNow: Boolean = false,
    @SerialName("restrictiveness_rank") val restrictivenessRank: Int? = null,
) : com.backlogquest.companion.schedule.ScheduleSlot

@Serializable
data class SlotsResponse(
    val slots: List<Slot> = emptyList(),
    @SerialName("recently_finished") val recentlyFinished: List<RecentlyFinished> = emptyList(),
)

@Serializable
data class IgdbResult(
    val name: String,
    val slug: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    @SerialName("igdb_url") val igdbUrl: String? = null,
    val year: Int? = null,
    val platforms: List<String> = emptyList(),
)

@Serializable
data class CreateGameResponse(
    @SerialName("game_id") val gameId: Int? = null,
    val error: String? = null,
)

@Serializable
data class OwnedPlatform(
    @SerialName("short_name") val shortName: String? = null,
    val format: String? = null,
    @SerialName("has_digital_market") val hasDigitalMarket: Int = 0,
)

@Serializable
data class BarcodeConstituent(
    val title: String? = null,
    @SerialName("owned_game_id") val ownedGameId: Int? = null,
    @SerialName("owned_platforms") val ownedPlatforms: List<OwnedPlatform> = emptyList(),
)

@Serializable
data class BarcodeCandidate(
    @SerialName("igdb_id") val igdbId: Int? = null,
    val title: String? = null,
    val platform: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    @SerialName("owned_game_id") val ownedGameId: Int? = null,
    @SerialName("game_type") val gameType: Int? = null,
    @SerialName("owned_platforms") val ownedPlatforms: List<OwnedPlatform> = emptyList(),
    val constituents: List<BarcodeConstituent> = emptyList(),
)

@Serializable
data class BarcodeResolveResponse(
    val upc: String,
    val source: String,
    val candidates: List<BarcodeCandidate> = emptyList(),
    @SerialName("product_title") val productTitle: String? = null,
    @SerialName("scanned_platform") val scannedPlatform: String? = null,
)
