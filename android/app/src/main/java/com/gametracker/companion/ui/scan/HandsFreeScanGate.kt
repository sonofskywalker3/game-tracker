package com.gametracker.companion.ui.scan

/** Drives hands-free continuous scanning (Database mode). Fires a confirmed
 *  barcode once per presentation: the SAME value, while it stays in view —
 *  tolerating intermittent ML Kit detection drops up to [clearFrames] empty
 *  frames — is never re-fired. A DIFFERENT confirmed value fires immediately
 *  (snappy game-to-game advance). A sustained absence ([clearFrames] empty
 *  frames after something was fired) clears the session so the UI resets and
 *  the same item can be scanned again if re-presented. Confirmation
 *  ([confirmFrames] via ScanConfirmGate) guards every fire against single-frame
 *  misreads. */
class HandsFreeScanGate(confirmFrames: Int = 2, private val clearFrames: Int = 10) {
    private val confirm = ScanConfirmGate(confirmFrames)
    private var lastHandled: String? = null
    private var emptyFrames = 0

    sealed interface Action {
        /** A newly confirmed barcode to resolve + save. */
        data class Fire(val value: String) : Action
        /** Sustained absence after a fire: reset the UI to the live scanner. */
        data object Clear : Action
        /** Nothing to do this frame. */
        data object None : Action
    }

    /** Feed one frame's decoded barcode (null if none decoded this frame). */
    fun onFrame(hit: String?): Action {
        if (hit != null) {
            emptyFrames = 0
            val confirmed = confirm.onScan(hit)
            if (confirmed != null && confirmed != lastHandled) {
                lastHandled = confirmed
                return Action.Fire(confirmed)
            }
            return Action.None
        }
        emptyFrames++
        if (lastHandled != null && emptyFrames >= clearFrames) {
            lastHandled = null
            confirm.reset()
            return Action.Clear
        }
        return Action.None
    }

    fun reset() { confirm.reset(); lastHandled = null; emptyFrames = 0 }
}
