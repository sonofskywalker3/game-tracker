package com.gametracker.companion.vpn

import org.junit.Assert.*
import org.junit.Test

private val SAMPLE = """
    [Interface]
    PrivateKey = aGVsbG9wcml2YXRla2V5MDAwMDAwMDAwMDAwMDAwMD0=
    Address = 10.99.0.2/32
    DNS = 10.99.0.1

    [Peer]
    PublicKey = c2VydmVycHVibGlja2V5MDAwMDAwMDAwMDAwMDAwMD0=
    Endpoint = vpn.example.com:51820
    AllowedIPs = 192.168.1.0/24
    PersistentKeepalive = 25
""".trimIndent()

class WgConfigParserTest {
    @Test fun parses_all_required_fields() {
        val cfg = parseWgConfig(SAMPLE).getOrThrow()
        assertTrue(cfg.privateKey.startsWith("aGVsbG"))
        assertEquals("10.99.0.2/32", cfg.address)
        assertEquals("10.99.0.1", cfg.dns)
        assertTrue(cfg.peerPublicKey.startsWith("c2Vydm"))
        assertEquals("vpn.example.com:51820", cfg.endpoint)
        assertEquals("192.168.1.0/24", cfg.allowedIps)
    }

    @Test fun missing_private_key_is_failure() {
        val text = SAMPLE.replace(Regex("PrivateKey =.*\n"), "")
        assertTrue(parseWgConfig(text).isFailure)
    }

    @Test fun missing_peer_section_is_failure() {
        val text = SAMPLE.substringBefore("[Peer]")
        assertTrue(parseWgConfig(text).isFailure)
    }

    @Test fun blank_input_is_failure() {
        assertTrue(parseWgConfig("   ").isFailure)
    }

    @Test fun ignores_comments_and_blank_lines() {
        val cfg = parseWgConfig("# header\n\n$SAMPLE\n# trailing").getOrThrow()
        assertEquals("vpn.example.com:51820", cfg.endpoint)
    }
}
