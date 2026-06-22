package com.gametracker.companion.vpn

import android.content.Context
import com.wireguard.android.backend.Backend
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import com.wireguard.config.Interface
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream

enum class TunnelStatus { Down, Connecting, Up }

private const val APP_PACKAGE = "com.gametracker.companion"
private const val TUNNEL_NAME = "gametracker"

class VpnController(private val appContext: Context, private val store: WgConfigStore) {

    private val _status = MutableStateFlow(TunnelStatus.Down)
    val status: StateFlow<TunnelStatus> = _status

    // GoBackend manages its own VpnService (GoBackend$VpnService); we just hand it a Context.
    private val backend: Backend by lazy { GoBackend(appContext) }

    private val tunnel = object : Tunnel {
        override fun getName(): String = TUNNEL_NAME
        override fun onStateChange(newState: Tunnel.State) {
            _status.value = when (newState) {
                Tunnel.State.UP -> TunnelStatus.Up
                else -> TunnelStatus.Down
            }
        }
    }

    /**
     * Build a Config from the raw .conf text, add a per-app split-tunnel constraint
     * (only this app's traffic routes through the VPN), then bring the tunnel UP.
     * Returns true on success, false if no config is stored or the tunnel fails to start.
     *
     * API notes vs. brief:
     *  - Config.parse(InputStream) used directly — no ByteArrayInputStream wrapping needed
     *    beyond what is already passed.
     *  - Interface.Builder.addAddresses(Collection) / addDnsServers(Collection) take typed
     *    InetNetwork / InetAddress collections from the parsed Interface — no string parsing.
     *  - Interface.Builder.includeApplication(String) exists and is used as brief specified.
     *  - backend.setState signature: (Tunnel, Tunnel.State, Config?) — matches Backend interface.
     */
    suspend fun connect(rawConf: String): Boolean = withContext(Dispatchers.IO) {
        _status.value = TunnelStatus.Connecting
        val base: Config = Config.parse(ByteArrayInputStream(rawConf.toByteArray(Charsets.UTF_8)))
        val baseIface = base.`interface`
        val scoped: Config = try {
            val scopedIface: Interface = Interface.Builder()
                .setKeyPair(baseIface.keyPair)
                .addAddresses(baseIface.addresses)
                .addDnsServers(baseIface.dnsServers)
                .also { ib ->
                    baseIface.listenPort.ifPresent { ib.setListenPort(it) }
                    baseIface.mtu.ifPresent { ib.setMtu(it) }
                }
                .includeApplication(APP_PACKAGE)   // per-app split tunnel
                .build()
            Config.Builder()
                .setInterface(scopedIface)
                .addPeers(base.peers)
                .build()
        } catch (e: Exception) {
            _status.value = TunnelStatus.Down
            return@withContext false
        }
        runCatching { backend.setState(tunnel, Tunnel.State.UP, scoped) }
            .onFailure { _status.value = TunnelStatus.Down }
            .isSuccess
    }

    suspend fun disconnect() = withContext(Dispatchers.IO) {
        runCatching { backend.setState(tunnel, Tunnel.State.DOWN, null) }
        _status.value = TunnelStatus.Down
    }
}
