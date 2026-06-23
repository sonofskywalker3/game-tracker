package com.gametracker.companion.data

import com.gametracker.companion.schedule.ScheduleWindow
import org.junit.Assert.*
import org.junit.Test

class ScheduleSnapshotTest {
    @Test fun roundTripsSlotsAndTimestamp() {
        val resp = SlotsResponse(
            slots = listOf(
                Slot(
                    id = 1, label = "Evening", goal = "Beat ch.1", sortOrder = 0,
                    currentGame = SlotCandidate(42, "Hades", "http://x/h.png"),
                    windows = listOf(ScheduleWindow(days = 127, startMin = 1200, endMin = 1380, id = 9)),
                ),
            ),
        )
        val snap = ScheduleSnapshot(resp, savedAtMillis = 1_700_000_000_000L)
        val decoded = decodeSnapshot(encodeSnapshot(snap))
        assertNotNull(decoded)
        assertEquals(1_700_000_000_000L, decoded!!.savedAtMillis)
        val slot = decoded.slots.slots.single()
        assertEquals("Evening", slot.label)
        assertEquals(42, slot.currentGame?.id)
        assertEquals(1200, slot.windows.single().startMin)
    }

    @Test fun decodeReturnsNullOnGarbage() {
        assertNull(decodeSnapshot("not json {{{"))
        assertNull(decodeSnapshot(""))
    }
}
