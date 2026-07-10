package com.backlogquest.companion.widget

import com.backlogquest.companion.data.Slot
import com.backlogquest.companion.data.SlotsResponse
import com.backlogquest.companion.schedule.DAY_MINUTES
import com.backlogquest.companion.schedule.minutesUntilActive
import com.backlogquest.companion.schedule.orderActive
import com.backlogquest.companion.schedule.slotActiveAt
import com.backlogquest.companion.schedule.windowCovers

data class WidgetCard(
    val title: String,
    val slotLabel: String,
    val hint: String,
    val goal: String?,
    val coverUrl: String?,
    val deepLinkGameId: Int?,
    val deepLinkSlotId: Int? = null,
)

/** Short weekday names indexed Mon=0 .. Sun=6 (matches the schedule weekday convention). */
private val DAY_NAMES = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

/**
 * Day qualifier for a future activation: "" today, "tomorrow " one day out, else the
 * weekday name (e.g. "Wed "). Includes a trailing space so callers can splice it inline.
 */
private fun dayQualifier(weekday: Int, minute: Int, minutesUntil: Int): String {
    val daysAhead = (minute + minutesUntil) / DAY_MINUTES
    return when (daysAhead) {
        0 -> ""
        1 -> "tomorrow "
        else -> {
            val targetWeekday = ((weekday * DAY_MINUTES + minute + minutesUntil) / DAY_MINUTES) % 7
            "${DAY_NAMES[targetWeekday]} "
        }
    }
}

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

/** Selection made via the widget's prev/next buttons goes stale after this. */
const val SELECTION_TTL_MILLIS: Long = 5L * 60L * 1000L

/** Wrap any index (including negatives) into [0, size). Size must be > 0. */
fun wrapIndex(index: Int, size: Int): Int = ((index % size) + size) % size

/**
 * The stored manual selection, or 0 (schedule's best pick) when it is stale,
 * out of range for the current cycle list, or the list is empty.
 */
fun effectiveIndex(storedIndex: Int, selectedAtMillis: Long, nowMillis: Long, listSize: Int): Int {
    if (listSize <= 0) return 0
    if (storedIndex !in 0 until listSize) return 0
    if (nowMillis - selectedAtMillis >= SELECTION_TTL_MILLIS) return 0
    return storedIndex
}

/**
 * Slots the widget can cycle through: slots with a game, active ones first
 * (most-restrictive first, mirroring orderActive), then not-yet-active ones by
 * soonest activation (tie-break sortOrder then id, mirroring nextUpcoming),
 * then never-activating ones in incoming order. Index 0 always equals what
 * buildWidgetCard(snapshot, weekday, minute) shows.
 */
fun buildCycleList(slots: List<Slot>, weekday: Int, minute: Int): List<Slot> {
    val withGame = slots.filter(::hasGame)
    val active = orderActive(withGame, weekday, minute)
    val activeIds = active.mapTo(HashSet()) { it.id }
    val pending = withGame.filter { it.id !in activeIds }
        .map { it to minutesUntilActive(it.windows, weekday, minute) }
    val (reachable, never) = pending.partition { it.second != null }
    val upcoming = reachable
        .sortedWith(compareBy({ it.second }, { it.first.sortOrder }, { it.first.id }))
        .map { it.first }
    return active + upcoming + never.map { it.first }
}

/** Card for one concrete slot (must have a game): active hint or Next-at hint. */
private fun cardForSlot(slot: Slot, weekday: Int, minute: Int): WidgetCard {
    val g = slot.currentGame!!
    val hint = if (slotActiveAt(slot.windows, weekday, minute)) {
        activeHint(slot, weekday, minute)
    } else {
        val until = minutesUntilActive(slot.windows, weekday, minute)
        if (until == null) {
            "Next: ${slot.label}"
        } else {
            val startMinute = (minute + until) % DAY_MINUTES
            "Next: ${slot.label} ${dayQualifier(weekday, minute, until)}at ${formatMinute(startMinute)}"
        }
    }
    return WidgetCard(
        title = g.title,
        slotLabel = slot.label,
        hint = hint,
        goal = slot.goal,
        coverUrl = g.coverUrl,
        deepLinkGameId = g.id,
        deepLinkSlotId = slot.id,
    )
}

/**
 * Headline card off the device clock: primary = top active slot with a game; else the
 * next-upcoming slot with a game; else an empty-state message.
 */
fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int): WidgetCard {
    val cycle = buildCycleList(snapshot.slots, weekday, minute)
    if (cycle.isNotEmpty()) return cardForSlot(cycle.first(), weekday, minute)
    return WidgetCard(
        title = "No picks scheduled",
        slotLabel = "",
        hint = "Set windows on the web",
        goal = null,
        coverUrl = null,
        deepLinkGameId = null,
        deepLinkSlotId = null,
    )
}

/** Card at a cycle position; any index wraps. Falls back to the empty card. */
fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int, index: Int): WidgetCard {
    val cycle = buildCycleList(snapshot.slots, weekday, minute)
    if (cycle.isEmpty()) return buildWidgetCard(snapshot, weekday, minute)
    return cardForSlot(cycle[wrapIndex(index, cycle.size)], weekday, minute)
}
