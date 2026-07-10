package com.backlogquest.companion.ui

import com.backlogquest.companion.data.Slot
import com.backlogquest.companion.ui.picks.slotPageIndex
import org.junit.Assert.assertEquals
import org.junit.Test

class SlotPageIndexTest {
    private fun slot(id: Int) = Slot(id = id, label = "S$id", goal = null, sortOrder = 0,
        currentGame = null, windows = emptyList())

    @Test fun findsSlotIndexById() {
        assertEquals(2, slotPageIndex(listOf(slot(5), slot(9), slot(7)), slotId = 7))
    }

    @Test fun nullSlotId_defaultsToFirstPage() {
        assertEquals(0, slotPageIndex(listOf(slot(5), slot(9)), slotId = null))
    }

    @Test fun unknownSlotId_fallsBackToFirstPage() {
        assertEquals(0, slotPageIndex(listOf(slot(5), slot(9)), slotId = 42))
    }

    @Test fun emptySlots_returnsZero() {
        assertEquals(0, slotPageIndex(emptyList(), slotId = 3))
    }
}
