package com.backlogquest.companion

import android.app.Application
import com.backlogquest.companion.widget.enqueuePicksWidgetRefresh

class App : Application() {
    // AppContainer is wired in Task 2 (manual DI).
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        enqueuePicksWidgetRefresh(this)
    }
}
