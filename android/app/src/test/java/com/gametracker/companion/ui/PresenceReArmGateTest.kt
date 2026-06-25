package com.gametracker.companion.ui

import com.gametracker.companion.ui.scan.PresenceReArmGate
import org.junit.Assert.*
import org.junit.Test

class PresenceReArmGateTest {
    @Test fun rearms_only_after_threshold_empty_frames() {
        val gate = PresenceReArmGate(threshold = 3)
        assertFalse(gate.onFrame(barcodePresent = true))   // held in frame
        assertFalse(gate.onFrame(false))                   // 1 empty
        assertFalse(gate.onFrame(false))                   // 2 empty
        assertTrue(gate.onFrame(false))                    // 3 empty -> re-arm
    }

    @Test fun a_present_frame_resets_the_empty_run() {
        val gate = PresenceReArmGate(threshold = 3)
        gate.onFrame(false); gate.onFrame(false)
        assertFalse(gate.onFrame(true))    // item back in view resets
        assertFalse(gate.onFrame(false))   // only 1 empty again
    }

    @Test fun reset_clears_counter() {
        val gate = PresenceReArmGate(threshold = 2)
        gate.onFrame(false)
        gate.reset()
        assertFalse(gate.onFrame(false))   // back to 1, not 2
    }
}
