package com.gametracker.companion.widget

import com.gametracker.companion.data.Slot
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.schedule.DAY_MINUTES
import com.gametracker.companion.schedule.nextUpcoming
import com.gametracker.companion.schedule.orderActive
import com.gametracker.companion.schedule.windowCovers

data class WidgetCard(
    val title: String,
    val slotLabel: String,
    val hint: String,
    val goal: String?,
    val coverUrl: String?,
    val deepLinkGameId: Int?,
)

/** 12-hour clock label, e.g. 0 -> "12:00am", 1380 -> "11:00pm". */
fun formatMinute(minute: Int): String {
    val m = ((minute % DAY_MINUTES) + DAY_MINUTES) % DAY_MINUTES
    val h24 = m / 60
    val min = m % 60
    val ampm = if (h24 < 12) "am" else "pm"
    val h12 = when (val h = h24 % 12) { 0 -> 12; else -> h }
    return "%d:%02d%s".format(h12, min, ampm)
}

private fun hasGame(slot: Slot): Boolean = slot.currentGame != null

/** The end-of-active-window hint for an active slot, e.g. "Active · until 11:00pm". */
private fun activeHint(slot: Slot, weekday: Int, minute: Int): String {
    val covering = slot.windows.firstOrNull { windowCovers(it, weekday, minute) }
    return if (covering != null) "Active · until ${formatMinute(covering.endMin)}" else "Active now"
}

/**
 * Headline card off the device clock: primary = top active slot with a game; else the
 * next-upcoming slot with a game; else an empty-state message.
 */
fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int): WidgetCard {
    val slots = snapshot.slots
    val primary = orderActive(slots, weekday, minute).firstOrNull { hasGame(it) }
    if (primary != null) {
        val g = primary.currentGame!!
        return WidgetCard(
            title = g.title,
            slotLabel = primary.label,
            hint = activeHint(primary, weekday, minute),
            goal = primary.goal,
            coverUrl = g.coverUrl,
            deepLinkGameId = g.id,
        )
    }
    val upcoming = nextUpcoming(slots, weekday, minute, ::hasGame)
    if (upcoming != null) {
        val g = upcoming.slot.currentGame!!
        val startMinute = (minute + upcoming.minutesUntil) % DAY_MINUTES
        return WidgetCard(
            title = g.title,
            slotLabel = upcoming.slot.label,
            hint = "Next: ${upcoming.slot.label} at ${formatMinute(startMinute)}",
            goal = upcoming.slot.goal,
            coverUrl = g.coverUrl,
            deepLinkGameId = g.id,
        )
    }
    return WidgetCard(
        title = "No picks scheduled",
        slotLabel = "",
        hint = "Set windows on the web",
        goal = null,
        coverUrl = null,
        deepLinkGameId = null,
    )
}
