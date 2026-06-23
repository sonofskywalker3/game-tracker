package com.gametracker.companion.data

import com.gametracker.companion.schedule.ScheduleSlot
import org.junit.Assert.*
import org.junit.Test

class ScheduleDtoTest {
    private val json = appJson()

    @Test fun parsesSlotWindowsAndIgnoresUnknownServerFields() {
        // shape mirrors GET /api/slots (Plan A): windows[] + active_now + restrictiveness_rank
        val body = """
        {"slots":[
          {"id":5,"label":"Evening","goal":"Beat ch.1","sort_order":2,
           "current_game":{"id":42,"title":"Hades","cover_url":"http://x/h.png"},
           "candidates":[],
           "windows":[{"id":11,"days":127,"start_min":1200,"end_min":1380}],
           "active_now":true,"restrictiveness_rank":0}
        ],"recently_finished":[]}
        """.trimIndent()
        val resp = json.decodeFromString(SlotsResponse.serializer(), body)
        val slot = resp.slots.single()
        assertEquals(5, slot.id)
        assertEquals(2, slot.sortOrder)
        assertEquals(1, slot.windows.size)
        assertEquals(127, slot.windows[0].days)
        assertEquals(1200, slot.windows[0].startMin)
        assertEquals(1380, slot.windows[0].endMin)
        assertTrue(slot.activeNow)
        assertEquals(0, slot.restrictivenessRank)
        // implements the matcher interface
        val asSchedule: ScheduleSlot = slot
        assertEquals(5, asSchedule.id)
    }

    @Test fun defaultsWhenWindowsAbsent() {
        val body = """{"slots":[{"id":1,"label":"X"}],"recently_finished":[]}"""
        val resp = json.decodeFromString(SlotsResponse.serializer(), body)
        assertTrue(resp.slots.single().windows.isEmpty())
        assertFalse(resp.slots.single().activeNow)
        assertNull(resp.slots.single().restrictivenessRank)
    }
}
