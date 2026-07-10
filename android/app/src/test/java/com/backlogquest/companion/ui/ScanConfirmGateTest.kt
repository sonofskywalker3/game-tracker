package com.backlogquest.companion.ui

import com.backlogquest.companion.ui.scan.ScanConfirmGate
import org.junit.Assert.*
import org.junit.Test

class ScanConfirmGateTest {
    @Test fun confirms_after_threshold_consecutive_reads() {
        val gate = ScanConfirmGate(threshold = 2)
        assertNull(gate.onScan("ABC"))
        assertEquals("ABC", gate.onScan("ABC"))
    }

    @Test fun a_single_frame_misread_is_rejected() {
        val gate = ScanConfirmGate(threshold = 2)
        assertNull(gate.onScan("BADREAD"))
        assertNull(gate.onScan("REAL"))
        assertEquals("REAL", gate.onScan("REAL"))
    }

    @Test fun null_frames_do_not_reset_progress() {
        val gate = ScanConfirmGate(threshold = 2)
        assertNull(gate.onScan("X"))
        assertNull(gate.onScan(null))
        assertEquals("X", gate.onScan("X"))
    }

    @Test fun reset_clears_progress() {
        val gate = ScanConfirmGate(threshold = 2)
        gate.onScan("X")
        gate.reset()
        assertNull(gate.onScan("X"))
    }

    @Test fun threshold_three_needs_three() {
        val gate = ScanConfirmGate(threshold = 3)
        assertNull(gate.onScan("Y"))
        assertNull(gate.onScan("Y"))
        assertEquals("Y", gate.onScan("Y"))
    }
}
