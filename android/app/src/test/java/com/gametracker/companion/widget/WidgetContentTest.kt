package com.gametracker.companion.widget

import com.gametracker.companion.data.Slot
import com.gametracker.companion.data.SlotCandidate
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.schedule.ScheduleWindow
import org.junit.Assert.*
import org.junit.Test

class WidgetContentTest {
    private fun win(days: Int, start: Int, end: Int) = ScheduleWindow(days, start, end)
    private val allDays = 0b1111111
    private fun slot(id: Int, label: String, game: SlotCandidate?, windows: List<ScheduleWindow>,
                     goal: String? = null, sortOrder: Int = 0) =
        Slot(id = id, label = label, goal = goal, sortOrder = sortOrder, currentGame = game, windows = windows)

    @Test fun formatMinute_twelveHour() {
        assertEquals("12:00am", formatMinute(0))
        assertEquals("9:00am", formatMinute(540))
        assertEquals("12:00pm", formatMinute(720))
        assertEquals("8:00pm", formatMinute(1200))
        assertEquals("11:00pm", formatMinute(1380))
        assertEquals("11:30pm", formatMinute(1410))
    }

    @Test fun primary_isTopActiveSlotWithGame_skipsActiveButEmpty() {
        // now Mon 21:00. tightEmpty is most-restrictive but has no game -> skipped.
        val tightEmpty = slot(1, "Lunch", game = null, windows = listOf(win(0b0000001, 1200, 1380)))
        val eveningGame = slot(2, "Evening",
            game = SlotCandidate(42, "Hades", "http://x/h.png"),
            windows = listOf(win(allDays, 1200, 1380)), goal = "Beat ch.1")
        val card = buildWidgetCard(SlotsResponse(slots = listOf(tightEmpty, eveningGame)),
                                   weekday = 0, minute = 1260)
        assertEquals("Hades", card.title)
        assertEquals("Evening", card.slotLabel)
        assertEquals(42, card.deepLinkGameId)
        assertEquals("http://x/h.png", card.coverUrl)
        assertEquals("Beat ch.1", card.goal)
        assertTrue(card.hint.contains("until 11:00pm"))  // active-window end hint
    }

    @Test fun fallback_nextUpcomingWhenNoneActiveWithGame() {
        // now Mon 18:00 (1080). evening slot has a game, activates 20:00.
        val evening = slot(2, "Evening", game = SlotCandidate(7, "Celeste"),
            windows = listOf(win(allDays, 1200, 1380)))
        val card = buildWidgetCard(SlotsResponse(slots = listOf(evening)), weekday = 0, minute = 1080)
        assertEquals("Celeste", card.title)
        assertTrue(card.hint.startsWith("Next:"))
        assertTrue(card.hint.contains("8:00pm"))
        assertEquals(7, card.deepLinkGameId)
    }

    @Test fun empty_whenNothingScheduledWithAGame() {
        val card = buildWidgetCard(SlotsResponse(slots = emptyList()), weekday = 0, minute = 1080)
        assertNull(card.deepLinkGameId)
        assertNull(card.coverUrl)
        assertTrue(card.title.contains("No picks"))
    }
}
