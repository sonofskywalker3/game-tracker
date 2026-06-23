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
