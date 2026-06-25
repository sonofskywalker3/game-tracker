package com.gametracker.companion.ui.scan

/** Guards against single-frame barcode misreads: only accepts a decoded value
 *  after it has been seen [threshold] consecutive frames. A one-off bad decode
 *  never repeats identically, so it is rejected; a steady barcode locks in within
 *  a few frames. Empty frames (null) are tolerated and do not reset progress. */
class ScanConfirmGate(private val threshold: Int = 2) {
    private var last: String? = null
    private var count = 0

    /** Feed one frame's decoded barcode (null if none decoded this frame).
     *  Returns the value once it has been seen [threshold] consecutive non-null
     *  frames with no different value in between, else null. */
    fun onScan(value: String?): String? {
        if (value == null) return null
        if (value == last) count++ else { last = value; count = 1 }
        return if (count >= threshold) value else null
    }

    fun reset() { last = null; count = 0 }
}
