package com.backlogquest.companion.ui

import com.backlogquest.companion.data.BarcodeCandidate
import com.backlogquest.companion.data.OwnedPlatform
import com.backlogquest.companion.ui.scan.Ownership
import com.backlogquest.companion.ui.scan.ownedLabels
import com.backlogquest.companion.ui.scan.ownershipOf
import com.backlogquest.companion.ui.scan.platformLabel
import org.junit.Assert.*
import org.junit.Test

class OwnershipTest {
    @Test fun label_both_format_reads_physical_and_digital() {
        assertEquals("Switch (Physical & Digital)",
            platformLabel(OwnedPlatform("Switch", "both", 1)))
    }

    @Test fun label_adds_qualifier_only_for_digital_market() {
        assertEquals("PS5 (Physical)",
            platformLabel(OwnedPlatform("PS5", "physical", 1)))
        assertEquals("3DS (Digital)",
            platformLabel(OwnedPlatform("3DS", "digital", 1)))
        assertEquals("SNES",  // cartridge-only legacy: no qualifier
            platformLabel(OwnedPlatform("SNES", "physical", 0)))
    }

    @Test fun not_owned_when_no_owned_platforms() {
        val c = BarcodeCandidate(title = "X", ownedPlatforms = emptyList())
        assertEquals(Ownership.NOT_OWNED, ownershipOf(c, "Switch"))
    }

    @Test fun same_platform_when_owned_on_scanned() {
        val c = BarcodeCandidate(title = "X",
            ownedPlatforms = listOf(OwnedPlatform("Switch", "digital", 1)))
        assertEquals(Ownership.SAME_PLATFORM, ownershipOf(c, "Switch"))
    }

    @Test fun other_platform_when_owned_elsewhere() {
        val c = BarcodeCandidate(title = "X",
            ownedPlatforms = listOf(OwnedPlatform("PS5", "physical", 1)))
        assertEquals(Ownership.OTHER_PLATFORM, ownershipOf(c, "Switch"))
        assertEquals("PS5 (Physical)", ownedLabels(c.ownedPlatforms))
    }
}
