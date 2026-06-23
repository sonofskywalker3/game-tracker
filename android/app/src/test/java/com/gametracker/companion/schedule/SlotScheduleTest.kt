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
}
