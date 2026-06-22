package com.gametracker.companion

import android.content.Context
import com.gametracker.companion.data.DataStoreSettings
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SettingsStore
import com.gametracker.companion.data.appJson
import com.gametracker.companion.data.buildApi
import com.gametracker.companion.data.dynamicHostInterceptor
import com.gametracker.companion.vpn.VpnController
import com.gametracker.companion.vpn.WgConfigStore
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor

class AppContainer(private val appContext: Context) {
    val settings: SettingsStore = DataStoreSettings(appContext)

    private val client: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(dynamicHostInterceptor(settings))
        .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
        .build()

    val repository: Repository = Repository(buildApi(client, appJson()))

    val wgConfigStore: WgConfigStore = WgConfigStore(appContext)
    val vpnController: VpnController = VpnController(appContext, wgConfigStore)
}
