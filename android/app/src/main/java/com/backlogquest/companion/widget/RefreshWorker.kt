package com.backlogquest.companion.widget

import android.content.Context
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.state.updateAppWidgetState
import androidx.glance.appwidget.updateAll
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.backlogquest.companion.App
import com.backlogquest.companion.data.ScheduleSnapshot
import java.util.concurrent.TimeUnit

const val FETCH_STALE_MILLIS: Long = 90L * 60L * 1000L     // refetch network only every ~90 min
const val WIDGET_TICK_MINUTES: Long = 15L                  // re-evaluate the primary every ~15 min
private const val WORK_NAME = "picks_widget_refresh"

/** Pure: fetch from network only when there is no cache or it has gone stale. */
fun shouldFetch(lastSavedMillis: Long?, nowMillis: Long, staleAfterMillis: Long = FETCH_STALE_MILLIS): Boolean {
    if (lastSavedMillis == null) return true
    return nowMillis - lastSavedMillis >= staleAfterMillis
}

class RefreshWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        try {
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
        } catch (e: Exception) {
            // Cache load/save failed — keep whatever is cached; still re-render below.
        }
        // Every tick returns the widget to the schedule's best pick.
        try {
            val manager = GlanceAppWidgetManager(applicationContext)
            manager.getGlanceIds(PicksWidget::class.java).forEach { gid ->
                updateAppWidgetState(applicationContext, gid) { prefs ->
                    prefs.remove(SelIndexKey)
                    prefs.remove(SelAtKey)
                }
            }
        } catch (e: Exception) {
            // State reset is best-effort; the TTL in effectiveIndex still bounds staleness.
        }
        // Always re-render so the widget advances through the day off the phone clock.
        PicksWidget().updateAll(applicationContext)
        return Result.success()
    }
}

/** Enqueue the periodic widget refresh (~15 min tick). UPDATE so interval changes apply. */
fun enqueuePicksWidgetRefresh(context: Context) {
    val request = PeriodicWorkRequestBuilder<RefreshWorker>(WIDGET_TICK_MINUTES, TimeUnit.MINUTES).build()
    WorkManager.getInstance(context).enqueueUniquePeriodicWork(
        WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request,
    )
}
