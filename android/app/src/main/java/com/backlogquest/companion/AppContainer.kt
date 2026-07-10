package com.backlogquest.companion

import android.content.Context
import com.backlogquest.companion.data.DataStoreScheduleSnapshotStore
import com.backlogquest.companion.data.DataStoreSettings
import com.backlogquest.companion.data.Repository
import com.backlogquest.companion.data.ScheduleSnapshotStore
import com.backlogquest.companion.data.SettingsStore
import com.backlogquest.companion.data.appJson
import com.backlogquest.companion.data.authInterceptor
import com.backlogquest.companion.data.buildApi
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor

class AppContainer(private val appContext: Context) {
    val settings: SettingsStore = DataStoreSettings(appContext)

    private val client: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(authInterceptor(settings))
        .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
        .build()

    val repository: Repository = Repository(buildApi(client, appJson()))
    val scheduleSnapshotStore: ScheduleSnapshotStore = DataStoreScheduleSnapshotStore(appContext)
}
