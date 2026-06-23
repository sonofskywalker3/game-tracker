package com.gametracker.companion.widget

import android.content.Context
import androidx.glance.appwidget.updateAll
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.gametracker.companion.App
import com.gametracker.companion.data.ScheduleSnapshot
import java.util.concurrent.TimeUnit

const val FETCH_STALE_MILLIS: Long = 90L * 60L * 1000L     // refetch network only every ~90 min
const val WIDGET_TICK_MINUTES: Long = 30L                  // re-evaluate the primary every ~30 min
private const val WORK_NAME = "picks_widget_refresh"

/** Pure: fetch from network only when there is no cache or it has gone stale. */
fun shouldFetch(lastSavedMillis: Long?, nowMillis: Long, staleAfterMillis: Long = FETCH_STALE_MILLIS): Boolean {
    if (lastSavedMillis == null) return true
    return nowMillis - lastSavedMillis >= staleAfterMillis
}

class RefreshWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val container = (applicationContext as App).container
        val store = container.scheduleSnapshotStore
        val now = System.currentTimeMillis()
        val cached = store.load()
        if (shouldFetch(cached?.savedAtMillis, now)) {
            container.repository.slots().onSuccess { resp ->
                store.save(ScheduleSnapshot(resp, savedAtMillis = now))
            }
            // On failure: keep the existing cache (offline-resilient).
        }
        // Always re-render so the widget advances through the day off the phone clock.
        PicksWidget().updateAll(applicationContext)
        return Result.success()
    }
}

/** Enqueue the periodic widget refresh (~30 min tick). Idempotent (KEEP existing). */
fun enqueuePicksWidgetRefresh(context: Context) {
    val request = PeriodicWorkRequestBuilder<RefreshWorker>(WIDGET_TICK_MINUTES, TimeUnit.MINUTES).build()
    WorkManager.getInstance(context).enqueueUniquePeriodicWork(
        WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request,
    )
}
