package com.gametracker.companion.schedule

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Pure schedule matcher for slots — active-now + restrictiveness ordering.
 * Canonical mirror of the backend's slot_schedule.py; keep the two in lockstep.
 *
 * A window's `days` is a 7-bit mask (bit 0 = Monday .. bit 6 = Sunday). start_min/
 * end_min are minutes since local midnight (0..1439). end_min > start_min is a normal
 * same-day window; end_min < start_min crosses midnight; end_min == start_min is
 * degenerate (never active). A slot with zero windows is 'anytime'.
 */

const val DAY_MINUTES = 1440

@Serializable
data class ScheduleWindow(
    val days: Int,
    @SerialName("start_min") val startMin: Int,
    @SerialName("end_min") val endMin: Int,
    val id: Int? = null,
)

interface ScheduleSlot {
    val id: Int
    val sortOrder: Int
    val windows: List<ScheduleWindow>
}

private fun daySet(days: Int, weekday: Int): Boolean = (days and (1 shl weekday)) != 0

/** True if (weekday, minute) falls inside this window. Handles midnight-cross. */
fun windowCovers(window: ScheduleWindow, weekday: Int, minute: Int): Boolean {
    val days = window.days
    val start = window.startMin
    val end = window.endMin
    if (end > start) {                       // normal, same-day window
        return daySet(days, weekday) && minute in start until end
    }
    if (end < start) {                       // crosses midnight
        val onStartDay = daySet(days, weekday) && minute >= start
        val prevDay = ((weekday - 1) % 7 + 7) % 7   // morning portion belongs to day-after a set day
        val onNextDay = daySet(days, prevDay) && minute < end
        return onStartDay || onNextDay
    }
    return false                             // degenerate (end == start)
}

/** A slot is active if it has no windows (anytime) or any window covers now. */
fun slotActiveAt(windows: List<ScheduleWindow>, weekday: Int, minute: Int): Boolean {
    if (windows.isEmpty()) return true
    return windows.any { windowCovers(it, weekday, minute) }
}

private fun windowLength(startMin: Int, endMin: Int): Int = when {
    endMin > startMin -> endMin - startMin
    endMin < startMin -> (DAY_MINUTES - startMin) + endMin
    else -> 0
}

/**
 * Total active minutes per week. Smaller = more restrictive. Zero windows ('anytime')
 * scores +infinity so it always sorts last. Overlapping windows are summed (an
 * acceptable approximation for ordering).
 */
fun restrictivenessScore(windows: List<ScheduleWindow>): Double {
    if (windows.isEmpty()) return Double.POSITIVE_INFINITY
    var total = 0
    for (w in windows) {
        total += Integer.bitCount(w.days) * windowLength(w.startMin, w.endMin)
    }
    return total.toDouble()
}

/**
 * Active slots only, most-restrictive-first (score asc, then sortOrder, then id).
 * Pure: rank is the returned list index (mirrors order_active's restrictiveness_rank
 * output WITHOUT mutating the inputs).
 */
fun <T : ScheduleSlot> orderActive(slots: List<T>, weekday: Int, minute: Int): List<T> =
    slots.filter { slotActiveAt(it.windows, weekday, minute) }
        .sortedWith(
            compareBy({ restrictivenessScore(it.windows) }, { it.sortOrder }, { it.id })
        )

/** Active slots (ranked) followed by the inactive slots in their incoming order. */
fun <T : ScheduleSlot> scheduleAwareOrder(slots: List<T>, weekday: Int, minute: Int): List<T> {
    val active = orderActive(slots, weekday, minute)
    val activeIds = active.mapTo(HashSet()) { it.id }
    val inactive = slots.filter { it.id !in activeIds }
    return active + inactive
}

/**
 * Minutes until the slot next becomes active, scanning the next 7 days minute-by-minute.
 * 0 if active now (incl. anytime). null if it never activates within a week.
 */
fun minutesUntilActive(windows: List<ScheduleWindow>, weekday: Int, minute: Int): Int? {
    if (slotActiveAt(windows, weekday, minute)) return 0
    for (delta in 1..(7 * DAY_MINUTES)) {
        val abs = weekday * DAY_MINUTES + minute + delta
        val wd = (abs / DAY_MINUTES) % 7
        val m = abs % DAY_MINUTES
        if (slotActiveAt(windows, wd, m)) return delta
    }
    return null
}

data class Upcoming<T>(val slot: T, val minutesUntil: Int)

/**
 * Among inactive slots passing [hasGame], the soonest to activate (tie-break sortOrder,
 * then id). null if none qualify.
 */
fun <T : ScheduleSlot> nextUpcoming(
    slots: List<T>,
    weekday: Int,
    minute: Int,
    hasGame: (T) -> Boolean,
): Upcoming<T>? =
    slots.asSequence()
        .filter { hasGame(it) && !slotActiveAt(it.windows, weekday, minute) }
        .mapNotNull { s -> minutesUntilActive(s.windows, weekday, minute)?.let { Upcoming(s, it) } }
        .sortedWith(compareBy({ it.minutesUntil }, { it.slot.sortOrder }, { it.slot.id }))
        .firstOrNull()
