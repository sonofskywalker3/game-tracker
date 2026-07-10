package com.backlogquest.companion.widget

import android.content.Context
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.glance.GlanceId
import androidx.glance.action.ActionParameters
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.state.updateAppWidgetState
import com.backlogquest.companion.App
import com.backlogquest.companion.ui.picks.deviceNowWeekdayMinute

/** Per-widget manual selection: cycle index + when it was made (for the TTL). */
val SelIndexKey = intPreferencesKey("sel_index")
val SelAtKey = longPreferencesKey("sel_at")

/** +1 (next) or -1 (prev), delivered by the widget's chevron buttons. */
val DirectionParam = ActionParameters.Key<Int>("cycle_direction")

class CycleAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val direction = parameters[DirectionParam] ?: return
        val store = (context.applicationContext as App).container.scheduleSnapshotStore
        val snapshot = store.load() ?: return
        val (weekday, minute) = deviceNowWeekdayMinute()
        val size = buildCycleList(snapshot.slots.slots, weekday, minute).size
        if (size == 0) return
        val now = System.currentTimeMillis()
        updateAppWidgetState(context, glanceId) { prefs ->
            val base = effectiveIndex(prefs[SelIndexKey] ?: 0, prefs[SelAtKey] ?: 0L, now, size)
            prefs[SelIndexKey] = wrapIndex(base + direction, size)
            prefs[SelAtKey] = now
        }
        PicksWidget().update(context, glanceId)
    }
}
