package com.gametracker.companion.ui.scan

/** Gates scanner re-arm on barcode *absence*: after a scan fires, re-arm only
 *  once the item has left the frame for [threshold] consecutive frames. A
 *  short debounce avoids re-arming on ML Kit's momentary detection gaps. */
class PresenceReArmGate(private val threshold: Int = 3) {
    private var emptyFrames = 0

    /** Feed one analyzer frame; returns true when it is time to re-arm. */
    fun onFrame(barcodePresent: Boolean): Boolean {
        if (barcodePresent) { emptyFrames = 0; return false }
        emptyFrames++
        return emptyFrames >= threshold
    }

    fun reset() { emptyFrames = 0 }
}
