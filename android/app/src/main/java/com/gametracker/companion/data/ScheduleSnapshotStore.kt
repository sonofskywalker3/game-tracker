package com.gametracker.companion.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
data class ScheduleSnapshot(
    val slots: SlotsResponse,
    val savedAtMillis: Long,
)

fun encodeSnapshot(snapshot: ScheduleSnapshot, json: Json = appJson()): String =
    json.encodeToString(ScheduleSnapshot.serializer(), snapshot)

/** Decode a persisted snapshot; null (never throws) on malformed input. */
fun decodeSnapshot(raw: String, json: Json = appJson()): ScheduleSnapshot? =
    runCatching { json.decodeFromString(ScheduleSnapshot.serializer(), raw) }.getOrNull()

interface ScheduleSnapshotStore {
    suspend fun save(snapshot: ScheduleSnapshot)
    suspend fun load(): ScheduleSnapshot?
}

private val Context.scheduleDataStore by preferencesDataStore(name = "schedule_snapshot")
private val SNAPSHOT_KEY = stringPreferencesKey("snapshot_json")

class DataStoreScheduleSnapshotStore(private val context: Context) : ScheduleSnapshotStore {
    override suspend fun save(snapshot: ScheduleSnapshot) {
        context.scheduleDataStore.edit { it[SNAPSHOT_KEY] = encodeSnapshot(snapshot) }
    }

    override suspend fun load(): ScheduleSnapshot? {
        val raw = context.scheduleDataStore.data.first()[SNAPSHOT_KEY] ?: return null
        return decodeSnapshot(raw)
    }
}
