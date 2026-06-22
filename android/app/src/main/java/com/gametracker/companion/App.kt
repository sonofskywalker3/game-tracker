package com.gametracker.companion

import android.app.Application

class App : Application() {
    // AppContainer is wired in Task 2 (manual DI).
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
