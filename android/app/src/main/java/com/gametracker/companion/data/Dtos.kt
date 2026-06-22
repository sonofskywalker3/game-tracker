package com.gametracker.companion.data

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

@Serializable
data class Slot(
    val id: Int,
    val label: String,
    val goal: String? = null,
    @SerialName("sort_order") val sortOrder: Int = 0,
    @SerialName("current_game") val currentGame: SlotCandidate? = null,
    val candidates: List<SlotCandidate> = emptyList(),
)

@Serializable
data class SlotsResponse(
    val slots: List<Slot> = emptyList(),
    @SerialName("recently_finished") val recentlyFinished: List<SlotCandidate> = emptyList(),
)

@Serializable
data class IgdbResult(
    val name: String,
    val slug: String? = null,
    @SerialName("cover_url") val coverUrl: String? = null,
    val platforms: List<String> = emptyList(),
)

@Serializable
data class CreateGameResponse(
    @SerialName("game_id") val gameId: Int? = null,
    val error: String? = null,
)
