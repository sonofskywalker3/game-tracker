package com.backlogquest.companion.widget

import com.backlogquest.companion.data.Slot
import com.backlogquest.companion.data.SlotCandidate
import com.backlogquest.companion.data.SlotsResponse
import com.backlogquest.companion.schedule.ScheduleWindow
import org.junit.Assert.*
import org.junit.Test

class WidgetCycleTest {
    private fun win(days: Int, start: Int, end: Int) = ScheduleWindow(days, start, end)
    private val allDays = 0b1111111
    private fun slot(id: Int, label: String, game: SlotCandidate?, windows: List<ScheduleWindow>,
                     goal: String? = null, sortOrder: Int = 0) =
        Slot(id = id, label = label, goal = goal, sortOrder = sortOrder, currentGame = game, windows = windows)

    // now = Mon 21:00 (weekday 0, minute 1260) unless stated otherwise.

    @Test fun cycleList_activeFirst_thenUpcomingBySoonest_skipsEmpty() {
        val activeTight = slot(1, "Evening", SlotCandidate(10, "Hades"),
            windows = listOf(win(allDays, 1200, 1380)))          // active now, restrictive
        val activeAnytime = slot(2, "Anytime", SlotCandidate(11, "Tunic"), windows = emptyList())
        val emptyActive = slot(3, "EmptyNow", null, windows = listOf(win(allDays, 1200, 1380)))
        val tomorrowMorning = slot(4, "Morning", SlotCandidate(12, "Celeste"),
            windows = listOf(win(allDays, 540, 600)))            // next fires Tue 09:00
        val laterTonight = slot(5, "Late", SlotCandidate(13, "Ori"),
            windows = listOf(win(allDays, 1320, 1440)))          // fires 22:00 tonight

        val cycle = buildCycleList(
            listOf(tomorrowMorning, emptyActive, activeAnytime, laterTonight, activeTight),
            weekday = 0, minute = 1260)

        // active (restrictive first, anytime last) -> upcoming by minutes-until
        assertEquals(listOf(1, 2, 5, 4), cycle.map { it.id })
    }

    @Test fun cycleList_indexZero_matchesBuildWidgetCardPrimary() {
        val active = slot(1, "Evening", SlotCandidate(10, "Hades"),
            windows = listOf(win(allDays, 1200, 1380)))
        val upcoming = slot(2, "Morning", SlotCandidate(12, "Celeste"),
            windows = listOf(win(allDays, 540, 600)))
        val slots = listOf(upcoming, active)
        val cycle = buildCycleList(slots, weekday = 0, minute = 1260)
        val card = buildWidgetCard(SlotsResponse(slots = slots), weekday = 0, minute = 1260)
        assertEquals(card.title, cycle.first().currentGame!!.title)
    }

    @Test fun cycleList_indexZero_matchesUpcomingFallback() {
        // nothing active with a game -> index 0 must be the soonest upcoming (same as the card).
        val soon = slot(1, "Late", SlotCandidate(13, "Ori"), windows = listOf(win(allDays, 1320, 1440)))
        val later = slot(2, "Morning", SlotCandidate(12, "Celeste"), windows = listOf(win(allDays, 540, 600)))
        val slots = listOf(later, soon)
        val cycle = buildCycleList(slots, weekday = 0, minute = 1260)
        assertEquals(1, cycle.first().id)
        val card = buildWidgetCard(SlotsResponse(slots = slots), weekday = 0, minute = 1260)
        assertEquals("Ori", card.title)
    }

    @Test fun cardAtIndex_activeSlot_hasActiveHintAndSlotIds() {
        val active = slot(1, "Evening", SlotCandidate(10, "Hades", "http://x/h.png"),
            windows = listOf(win(allDays, 1200, 1380)), goal = "Beat ch.2")
        val upcoming = slot(2, "Morning", SlotCandidate(12, "Celeste"),
            windows = listOf(win(allDays, 540, 600)))
        val snapshot = SlotsResponse(slots = listOf(active, upcoming))
        val card0 = buildWidgetCard(snapshot, weekday = 0, minute = 1260, index = 0)
        assertEquals("Hades", card0.title)
        assertEquals(1, card0.deepLinkSlotId)
        assertTrue(card0.hint.contains("until 11:00pm"))
        val card1 = buildWidgetCard(snapshot, weekday = 0, minute = 1260, index = 1)
        assertEquals("Celeste", card1.title)
        assertEquals(2, card1.deepLinkSlotId)
        assertEquals("Next: Morning tomorrow at 9:00am", card1.hint)
    }

    @Test fun cardAtIndex_wrapsBothDirections() {
        val a = slot(1, "A", SlotCandidate(10, "Hades"), windows = emptyList())
        val b = slot(2, "B", SlotCandidate(11, "Tunic"), windows = emptyList(), sortOrder = 1)
        val snapshot = SlotsResponse(slots = listOf(a, b))
        assertEquals("Hades", buildWidgetCard(snapshot, 0, 1260, index = 2).title)   // 2 % 2 = 0
        assertEquals("Tunic", buildWidgetCard(snapshot, 0, 1260, index = -1).title)  // wraps to last
    }

    @Test fun wrapIndex_handlesNegativesAndOverflow() {
        assertEquals(0, wrapIndex(3, 3))
        assertEquals(2, wrapIndex(-1, 3))
        assertEquals(1, wrapIndex(4, 3))
        assertEquals(0, wrapIndex(0, 3))
    }

    @Test fun effectiveIndex_freshSelectionSticks_staleFallsBackToZero() {
        val now = 1_000_000L
        assertEquals(2, effectiveIndex(2, now - SELECTION_TTL_MILLIS + 1, now, listSize = 4))
        assertEquals(0, effectiveIndex(2, now - SELECTION_TTL_MILLIS, now, listSize = 4))
        assertEquals(0, effectiveIndex(2, now - SELECTION_TTL_MILLIS - 1, now, listSize = 4))
    }

    @Test fun effectiveIndex_outOfRangeOrEmptyList_fallsBackToZero() {
        val now = 1_000_000L
        assertEquals(0, effectiveIndex(7, now, now, listSize = 3))   // index beyond list
        assertEquals(0, effectiveIndex(-2, now, now, listSize = 3))  // negative index
        assertEquals(0, effectiveIndex(1, now, now, listSize = 0))   // empty list
    }
}
