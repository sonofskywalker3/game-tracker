package com.gametracker.companion.schedule

import org.junit.Assert.*
import org.junit.Test

// weekday: 0=Mon..6=Sun ; minute: minutes since local midnight (0..1439)
class SlotScheduleTest {

    // ---- windowCovers: normal same-day window (end > start) ----
    @Test fun sameDayWindow_coversInside_excludesEnd() {
        // Mon only (bit0), 18:00 (1080) .. 23:00 (1380)
        val w = ScheduleWindow(days = 0b0000001, startMin = 1080, endMin = 1380)
        assertTrue(windowCovers(w, weekday = 0, minute = 1080))   // start inclusive
        assertTrue(windowCovers(w, weekday = 0, minute = 1200))
        assertFalse(windowCovers(w, weekday = 0, minute = 1380))  // end exclusive
        assertFalse(windowCovers(w, weekday = 0, minute = 1079))
        assertFalse(windowCovers(w, weekday = 1, minute = 1200))  // wrong day
    }

    // ---- windowCovers: crosses midnight (end < start) ----
    @Test fun midnightCross_coversStartDayEvening_andNextDayMorning() {
        // Mon set (bit0), 22:00 (1320) .. 02:00 (120) — wraps past midnight into Tue
        val w = ScheduleWindow(days = 0b0000001, startMin = 1320, endMin = 120)
        assertTrue(windowCovers(w, weekday = 0, minute = 1320))   // Mon 22:00 (start day, >= start)
        assertTrue(windowCovers(w, weekday = 0, minute = 1439))   // Mon 23:59
        assertTrue(windowCovers(w, weekday = 1, minute = 30))     // Tue 00:30 — morning belongs to (weekday-1)%7 == Mon
        assertTrue(windowCovers(w, weekday = 1, minute = 119))    // Tue 01:59
        assertFalse(windowCovers(w, weekday = 1, minute = 120))   // Tue 02:00 (end exclusive)
        assertFalse(windowCovers(w, weekday = 1, minute = 1320))  // Tue 22:00 — Tue not a start day
        assertFalse(windowCovers(w, weekday = 0, minute = 60))    // Mon 01:00 — Sun would own this, Sun not set
    }

    @Test fun midnightCross_wrapsWeekFromSundayToMonday() {
        // Sun set (bit6), 23:00 (1380) .. 01:00 (60). Mon 00:30 belongs to (Mon-1)%7 == Sun.
        val w = ScheduleWindow(days = 0b1000000, startMin = 1380, endMin = 60)
        assertTrue(windowCovers(w, weekday = 6, minute = 1400))   // Sun 23:20
        assertTrue(windowCovers(w, weekday = 0, minute = 30))     // Mon 00:30 via (0-1)%7==6==Sun
    }

    // ---- windowCovers: degenerate (end == start) never active ----
    @Test fun degenerateWindow_neverActive() {
        val w = ScheduleWindow(days = 0b1111111, startMin = 600, endMin = 600)
        assertFalse(windowCovers(w, weekday = 0, minute = 600))
        assertFalse(windowCovers(w, weekday = 3, minute = 600))
    }

    // ---- slotActiveAt ----
    @Test fun emptyWindows_isAnytimeActive() {
        assertTrue(slotActiveAt(emptyList(), weekday = 2, minute = 0))
        assertTrue(slotActiveAt(emptyList(), weekday = 5, minute = 1439))
    }

    @Test fun anyWindowCovering_makesSlotActive() {
        val morning = ScheduleWindow(days = 0b1111111, startMin = 360, endMin = 540)   // 06:00-09:00
        val evening = ScheduleWindow(days = 0b1111111, startMin = 1200, endMin = 1380) // 20:00-23:00
        assertTrue(slotActiveAt(listOf(morning, evening), weekday = 0, minute = 1260)) // in evening
        assertFalse(slotActiveAt(listOf(morning, evening), weekday = 0, minute = 720))  // noon, neither
    }

    // ---- restrictivenessScore: total active minutes/week; empty -> +inf ----
    @Test fun emptyWindows_scoreInfinity() {
        assertEquals(Double.POSITIVE_INFINITY, restrictivenessScore(emptyList()), 0.0)
    }

    @Test fun score_sumsDaysCountTimesLength_sameDay() {
        // Mon+Tue (2 days), 20:00..23:00 = 180 min -> 2*180 = 360
        val w = ScheduleWindow(days = 0b0000011, startMin = 1200, endMin = 1380)
        assertEquals(360.0, restrictivenessScore(listOf(w)), 0.0)
    }

    @Test fun score_midnightCrossLength_andSumsMultipleWindows() {
        // 1 day, 22:00 (1320)..02:00 (120) -> (1440-1320)+120 = 240 ; plus Mon+Tue 1h window = 2*60=120
        val cross = ScheduleWindow(days = 0b0000001, startMin = 1320, endMin = 120)
        val short = ScheduleWindow(days = 0b0000011, startMin = 600, endMin = 660)
        assertEquals(240.0 + 120.0, restrictivenessScore(listOf(cross, short)), 0.0)
    }

    // ---- test slot impl ----
    private data class TestSlot(
        override val id: Int,
        override val sortOrder: Int,
        override val windows: List<ScheduleWindow>,
    ) : ScheduleSlot

    private fun win(days: Int, start: Int, end: Int) = ScheduleWindow(days, start, end)
    private val allDays = 0b1111111

    // ---- orderActive: most-restrictive-first, anytime last, tie-break sortOrder then id ----
    @Test fun orderActive_filtersInactive_andSortsByScore_anytimeLast() {
        // now = Mon 21:00 (weekday 0, minute 1260)
        val tight = TestSlot(1, 0, listOf(win(0b0000001, 1200, 1380)))   // Mon 20-23, 1 day*180=180
        val wide  = TestSlot(2, 0, listOf(win(allDays, 1200, 1380)))     // every day 20-23, 7*180=1260
        val anytime = TestSlot(3, 0, emptyList())                        // inf -> last
        val inactive = TestSlot(4, 0, listOf(win(allDays, 360, 540)))    // 06-09, not active at 21:00
        val ordered = orderActive(listOf(anytime, wide, tight, inactive), weekday = 0, minute = 1260)
        assertEquals(listOf(1, 2, 3), ordered.map { it.id })             // tight, wide, anytime; inactive dropped
    }

    @Test fun orderActive_tieBreak_sortOrderThenId() {
        // two slots with identical scores -> sortOrder asc, then id asc
        val a = TestSlot(id = 9, sortOrder = 1, windows = listOf(win(allDays, 1200, 1380)))
        val b = TestSlot(id = 8, sortOrder = 0, windows = listOf(win(allDays, 1200, 1380)))
        val c = TestSlot(id = 7, sortOrder = 1, windows = listOf(win(allDays, 1200, 1380)))
        val ordered = orderActive(listOf(a, b, c), weekday = 0, minute = 1260)
        assertEquals(listOf(8, 7, 9), ordered.map { it.id })  // b(so0), then so1 by id: 7,9
    }

    @Test fun orderActive_midnightCross_activeInMorningPortion() {
        // window Mon 22:00..02:00 ; now Tue 00:30 -> active via (weekday-1)%7
        val late = TestSlot(1, 0, listOf(win(0b0000001, 1320, 120)))
        val ordered = orderActive(listOf(late), weekday = 1, minute = 30)
        assertEquals(listOf(1), ordered.map { it.id })
    }

    // ---- scheduleAwareOrder: active (ranked) then inactive (incoming order) ----
    @Test fun scheduleAwareOrder_activeFirstThenInactiveInOrder() {
        val activeWide = TestSlot(1, 0, listOf(win(allDays, 1200, 1380)))  // active
        val inactiveA  = TestSlot(2, 0, listOf(win(allDays, 360, 540)))    // inactive
        val activeTight= TestSlot(3, 0, listOf(win(0b0000001, 1200, 1380)))// active, more restrictive
        val inactiveB  = TestSlot(4, 0, listOf(win(allDays, 0, 60)))       // inactive
        val out = scheduleAwareOrder(listOf(activeWide, inactiveA, activeTight, inactiveB),
                                     weekday = 0, minute = 1260)
        assertEquals(listOf(3, 1, 2, 4), out.map { it.id })  // tight, wide, then inactive A,B in order
    }

    // ---- minutesUntilActive ----
    @Test fun minutesUntilActive_zeroWhenActiveOrAnytime() {
        assertEquals(0, minutesUntilActive(emptyList(), 0, 0))
        val active = listOf(win(allDays, 1200, 1380))
        assertEquals(0, minutesUntilActive(active, weekday = 0, minute = 1260))
    }

    @Test fun minutesUntilActive_laterToday() {
        // 20:00 window, now 18:00 -> 120 min
        assertEquals(120, minutesUntilActive(listOf(win(allDays, 1200, 1380)), weekday = 0, minute = 1080))
    }

    @Test fun minutesUntilActive_nextDay() {
        // Tue-only 09:00 window, now Mon 23:00 -> until Tue 09:00 = 60 (to midnight) + 540 = 600
        assertEquals(600, minutesUntilActive(listOf(win(0b0000010, 540, 600)), weekday = 0, minute = 1380))
    }

    @Test fun minutesUntilActive_nullWhenNever() {
        assertNull(minutesUntilActive(listOf(win(0b0000000, 540, 600)), weekday = 0, minute = 0)) // no days set
    }

    // ---- nextUpcoming ----
    @Test fun nextUpcoming_picksSoonestInactiveWithGame() {
        val soon = TestSlot(1, 0, listOf(win(allDays, 1320, 1380)))  // 22:00, now 21:00 -> 60 min
        val later= TestSlot(2, 0, listOf(win(allDays, 1380, 1410)))  // 23:00 -> 120 min
        val out = nextUpcoming(listOf(later, soon), weekday = 0, minute = 1260, hasGame = { true })
        assertEquals(1, out!!.slot.id)
        assertEquals(60, out.minutesUntil)
    }

    @Test fun nextUpcoming_skipsSlotsWithoutGame_andReturnsNullWhenNone() {
        val noGame = TestSlot(1, 0, listOf(win(allDays, 1320, 1380)))
        assertNull(nextUpcoming(listOf(noGame), weekday = 0, minute = 1260, hasGame = { false }))
    }

    @Test fun nextUpcoming_tieBreak_sortOrderThenId() {
        // now Mon 21:00 (1260); all candidates inactive, all activate at 22:00 -> equal minutesUntil
        val w = win(allDays, 1320, 1380)  // 22:00-23:00
        // sortOrder tie-break: so=0 beats so=1 even with a higher id
        val a = TestSlot(id = 5, sortOrder = 1, windows = listOf(w))
        val b = TestSlot(id = 9, sortOrder = 0, windows = listOf(w))
        assertEquals(9, nextUpcoming(listOf(a, b), weekday = 0, minute = 1260, hasGame = { true })!!.slot.id)
        // id tie-break when sortOrder equal: lower id wins
        val c = TestSlot(id = 7, sortOrder = 0, windows = listOf(w))
        val d = TestSlot(id = 3, sortOrder = 0, windows = listOf(w))
        assertEquals(3, nextUpcoming(listOf(c, d), weekday = 0, minute = 1260, hasGame = { true })!!.slot.id)
    }
}
