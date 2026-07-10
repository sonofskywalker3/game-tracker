package com.backlogquest.companion.ui

import com.backlogquest.companion.ui.scan.HandsFreeScanGate
import com.backlogquest.companion.ui.scan.HandsFreeScanGate.Action
import org.junit.Assert.*
import org.junit.Test

class HandsFreeScanGateTest {
    @Test fun fires_a_confirmed_barcode_once_and_suppresses_repeats() {
        val g = HandsFreeScanGate(confirmFrames = 2, clearFrames = 10)
        assertEquals(Action.None, g.onFrame("A"))
        assertEquals(Action.Fire("A"), g.onFrame("A"))
        assertEquals(Action.None, g.onFrame("A"))
        assertEquals(Action.None, g.onFrame("A"))
    }

    @Test fun intermittent_loss_of_same_barcode_does_not_refire() {
        val g = HandsFreeScanGate(confirmFrames = 2, clearFrames = 5)
        g.onFrame("A"); g.onFrame("A")
        repeat(3) { assertEquals(Action.None, g.onFrame(null)) }   // brief drops (< clearFrames)
        assertEquals(Action.None, g.onFrame("A"))
        assertEquals(Action.None, g.onFrame("A"))
    }

    @Test fun a_different_barcode_fires_immediately() {
        val g = HandsFreeScanGate(confirmFrames = 2, clearFrames = 10)
        g.onFrame("A"); g.onFrame("A")
        assertEquals(Action.None, g.onFrame("B"))
        assertEquals(Action.Fire("B"), g.onFrame("B"))
    }

    @Test fun sustained_absence_clears_then_same_barcode_can_refire() {
        val g = HandsFreeScanGate(confirmFrames = 2, clearFrames = 3)
        g.onFrame("A"); g.onFrame("A")
        assertEquals(Action.None, g.onFrame(null))
        assertEquals(Action.None, g.onFrame(null))
        assertEquals(Action.Clear, g.onFrame(null))
        assertEquals(Action.None, g.onFrame("A"))
        assertEquals(Action.Fire("A"), g.onFrame("A"))
    }

    @Test fun single_frame_misread_between_real_reads_is_rejected() {
        val g = HandsFreeScanGate(confirmFrames = 2, clearFrames = 10)
        assertEquals(Action.None, g.onFrame("BAD"))
        assertEquals(Action.None, g.onFrame("A"))
        assertEquals(Action.Fire("A"), g.onFrame("A"))
    }

    @Test fun no_clear_before_anything_is_fired() {
        val g = HandsFreeScanGate(confirmFrames = 2, clearFrames = 2)
        assertEquals(Action.None, g.onFrame(null))
        assertEquals(Action.None, g.onFrame(null))
        assertEquals(Action.None, g.onFrame(null))   // nothing fired -> never Clear
    }
}
