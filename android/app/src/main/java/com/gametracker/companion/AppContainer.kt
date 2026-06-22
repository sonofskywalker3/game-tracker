package com.gametracker.companion

import android.content.Context

/** Manual DI root. Real wiring (Settings, networking, Repository) lands in Task 2. */
class AppContainer(private val appContext: Context)
