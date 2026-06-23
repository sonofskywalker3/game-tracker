package com.gametracker.companion.ui.scan

import com.gametracker.companion.data.BarcodeCandidate
import com.gametracker.companion.data.OwnedPlatform

enum class Ownership { NOT_OWNED, SAME_PLATFORM, OTHER_PLATFORM }

/** "PS5 (Physical)" / "3DS (Digital)" / "SNES". The (Physical/Digital) qualifier is
 *  shown only for platforms with a digital storefront (has_digital_market). */
fun platformLabel(p: OwnedPlatform): String {
    val base = p.shortName ?: "?"
    val fmt = p.format
    return if (p.hasDigitalMarket == 1 && fmt != null)
        "$base (${fmt.replaceFirstChar { it.uppercase() }})" else base
}

fun ownedLabels(platforms: List<OwnedPlatform>): String =
    platforms.joinToString(", ") { platformLabel(it) }

/** Ownership of a resolved title relative to the platform the barcode was scanned on. */
fun ownershipOf(candidate: BarcodeCandidate, scannedPlatform: String?): Ownership {
    val owned = candidate.ownedPlatforms
    if (owned.isEmpty()) return Ownership.NOT_OWNED
    val onScanned = scannedPlatform != null && owned.any { it.shortName == scannedPlatform }
    return if (onScanned) Ownership.SAME_PLATFORM else Ownership.OTHER_PLATFORM
}
