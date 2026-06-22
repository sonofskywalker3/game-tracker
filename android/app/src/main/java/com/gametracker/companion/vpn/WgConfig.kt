package com.gametracker.companion.vpn

data class WgConfig(
    val privateKey: String,
    val address: String,
    val dns: String?,
    val peerPublicKey: String,
    val endpoint: String,
    val allowedIps: String,
    val presharedKey: String? = null,
)

private const val SECTION_INTERFACE = "interface"
private const val SECTION_PEER = "peer"

/** Parse a WireGuard .conf (Firewalla QR payload or pasted text) into a typed config.
 *  Returns Result.failure(IllegalArgumentException) when a required key/section is absent. */
fun parseWgConfig(text: String): Result<WgConfig> = runCatching {
    val iface = HashMap<String, String>()
    val peer = HashMap<String, String>()
    var section: String? = null

    for (raw in text.lineSequence()) {
        val line = raw.substringBefore('#').trim()
        if (line.isEmpty()) continue
        if (line.startsWith("[") && line.endsWith("]")) {
            section = line.substring(1, line.length - 1).trim().lowercase()
            continue
        }
        val key = line.substringBefore('=', "").trim().lowercase()
        val value = line.substringAfter('=', "").trim()
        if (key.isEmpty() || value.isEmpty()) continue
        when (section) {
            SECTION_INTERFACE -> iface[key] = value
            SECTION_PEER -> peer[key] = value
        }
    }

    fun require(map: Map<String, String>, key: String, where: String): String =
        map[key] ?: throw IllegalArgumentException("WireGuard config missing $key in [$where]")

    WgConfig(
        privateKey = require(iface, "privatekey", "Interface"),
        address = require(iface, "address", "Interface"),
        dns = iface["dns"],
        peerPublicKey = require(peer, "publickey", "Peer"),
        endpoint = require(peer, "endpoint", "Peer"),
        allowedIps = require(peer, "allowedips", "Peer"),
        presharedKey = peer["presharedkey"],
    )
}
