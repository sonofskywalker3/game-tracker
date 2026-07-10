# Widget Nav Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the Glance picks widget: card pinned to the top, a prev/checklist/next button row below it that cycles through the slate from the cached snapshot, 5-minute selection TTL, and a deep link that opens the Picks pager already paged to the shown slot.

**Architecture:** All cycling logic is pure Kotlin in `WidgetContent.kt` (unit-tested). Button taps run a Glance `ActionCallback` that mutates per-widget Preferences state (`sel_index`, `sel_at`) and re-renders from the cached `SlotsResponse` — no network. `RefreshWorker` ticks every 15 min, clears the selection, and re-renders. The middle button / card fire the existing `actionStartActivity` pattern with a new `open_slot_id` extra consumed by `MainActivity → AppNav → PicksScreen`.

**Tech Stack:** Kotlin, Glance appwidget (`ActionCallback`, `updateAppWidgetState`, `PreferencesGlanceStateDefinition`), WorkManager, Compose `HorizontalPager`, JUnit4.

## Global Constraints

- Working dir: `C:\Users\Jeff\Documents\Projects\Game Tracker` (android project in `android/`).
- Run tests: `cd android; .\gradlew.bat :app:testDebugUnitTest` (PowerShell).
- Commit directly to `main`, push after each task (owner preference; no branches).
- Do NOT touch the running Flask app or real `games.db` (Android-only work).
- Existing `WidgetContentTest` tests must keep passing unmodified — `buildWidgetCard(snapshot, weekday, minute)` (index 0) behavior is frozen, including exact hint strings.
- Selection TTL: 5 minutes (`SELECTION_TTL_MILLIS`). Widget tick: 15 minutes (`WIDGET_TICK_MINUTES`).

---

### Task 1: Pure cycle model in WidgetContent.kt

**Files:**
- Modify: `android/app/src/main/java/com/backlogquest/companion/widget/WidgetContent.kt`
- Test: `android/app/src/test/java/com/backlogquest/companion/widget/WidgetCycleTest.kt` (create)

**Interfaces:**
- Consumes: `orderActive`, `slotActiveAt`, `minutesUntilActive`, `DAY_MINUTES` from `com.backlogquest.companion.schedule`; `Slot`, `SlotsResponse`, `SlotCandidate` from `data`.
- Produces (used by Tasks 2–4):
  - `WidgetCard` gains `val deepLinkSlotId: Int? = null` (last constructor param).
  - `fun buildCycleList(slots: List<Slot>, weekday: Int, minute: Int): List<Slot>`
  - `fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int, index: Int): WidgetCard`
  - `fun wrapIndex(index: Int, size: Int): Int`
  - `fun effectiveIndex(storedIndex: Int, selectedAtMillis: Long, nowMillis: Long, listSize: Int): Int`
  - `const val SELECTION_TTL_MILLIS: Long` (5 min in millis)

- [ ] **Step 1: Write the failing tests**

Create `android/app/src/test/java/com/backlogquest/companion/widget/WidgetCycleTest.kt`:

```kotlin
package com.backlogquest.companion.widget

import com.backlogquest.companion.data.Slot
import com.backlogquest.companion.data.SlotCandidate
import com.backlogquest.companion.data.SlotsResponse
import com.backlogquest.companion.schedule.ScheduleWindow
import org.junit.Assert.*
import org.junit.Test

class WidgetCycleTest {
    private fun win(days: Int, start: Int, end: Int) = ScheduleWindow(days, start, end)
    private val allDays = 0b1111111
    private fun slot(id: Int, label: String, game: SlotCandidate?, windows: List<ScheduleWindow>,
                     goal: String? = null, sortOrder: Int = 0) =
        Slot(id = id, label = label, goal = goal, sortOrder = sortOrder, currentGame = game, windows = windows)

    // now = Mon 21:00 (weekday 0, minute 1260) unless stated otherwise.

    @Test fun cycleList_activeFirst_thenUpcomingBySoonest_skipsEmpty() {
        val activeTight = slot(1, "Evening", SlotCandidate(10, "Hades"),
            windows = listOf(win(allDays, 1200, 1380)))          // active now, restrictive
        val activeAnytime = slot(2, "Anytime", SlotCandidate(11, "Tunic"), windows = emptyList())
        val emptyActive = slot(3, "EmptyNow", null, windows = listOf(win(allDays, 1200, 1380)))
        val tomorrowMorning = slot(4, "Morning", SlotCandidate(12, "Celeste"),
            windows = listOf(win(allDays, 540, 600)))            // next fires Tue 09:00
        val laterTonight = slot(5, "Late", SlotCandidate(13, "Ori"),
            windows = listOf(win(allDays, 1320, 1440)))          // fires 22:00 tonight

        val cycle = buildCycleList(
            listOf(tomorrowMorning, emptyActive, activeAnytime, laterTonight, activeTight),
            weekday = 0, minute = 1260)

        // active (restrictive first, anytime last) -> upcoming by minutes-until
        assertEquals(listOf(1, 2, 5, 4), cycle.map { it.id })
    }

    @Test fun cycleList_indexZero_matchesBuildWidgetCardPrimary() {
        val active = slot(1, "Evening", SlotCandidate(10, "Hades"),
            windows = listOf(win(allDays, 1200, 1380)))
        val upcoming = slot(2, "Morning", SlotCandidate(12, "Celeste"),
            windows = listOf(win(allDays, 540, 600)))
        val slots = listOf(upcoming, active)
        val cycle = buildCycleList(slots, weekday = 0, minute = 1260)
        val card = buildWidgetCard(SlotsResponse(slots = slots), weekday = 0, minute = 1260)
        assertEquals(card.title, cycle.first().currentGame!!.title)
    }

    @Test fun cycleList_indexZero_matchesUpcomingFallback() {
        // nothing active with a game -> index 0 must be the soonest upcoming (same as the card).
        val soon = slot(1, "Late", SlotCandidate(13, "Ori"), windows = listOf(win(allDays, 1320, 1440)))
        val later = slot(2, "Morning", SlotCandidate(12, "Celeste"), windows = listOf(win(allDays, 540, 600)))
        val slots = listOf(later, soon)
        val cycle = buildCycleList(slots, weekday = 0, minute = 1260)
        assertEquals(1, cycle.first().id)
        val card = buildWidgetCard(SlotsResponse(slots = slots), weekday = 0, minute = 1260)
        assertEquals("Ori", card.title)
    }

    @Test fun cardAtIndex_activeSlot_hasActiveHintAndSlotIds() {
        val active = slot(1, "Evening", SlotCandidate(10, "Hades", "http://x/h.png"),
            windows = listOf(win(allDays, 1200, 1380)), goal = "Beat ch.2")
        val upcoming = slot(2, "Morning", SlotCandidate(12, "Celeste"),
            windows = listOf(win(allDays, 540, 600)))
        val snapshot = SlotsResponse(slots = listOf(active, upcoming))
        val card0 = buildWidgetCard(snapshot, weekday = 0, minute = 1260, index = 0)
        assertEquals("Hades", card0.title)
        assertEquals(1, card0.deepLinkSlotId)
        assertTrue(card0.hint.contains("until 11:00pm"))
        val card1 = buildWidgetCard(snapshot, weekday = 0, minute = 1260, index = 1)
        assertEquals("Celeste", card1.title)
        assertEquals(2, card1.deepLinkSlotId)
        assertEquals("Next: Morning tomorrow at 9:00am", card1.hint)
    }

    @Test fun cardAtIndex_wrapsBothDirections() {
        val a = slot(1, "A", SlotCandidate(10, "Hades"), windows = emptyList())
        val b = slot(2, "B", SlotCandidate(11, "Tunic"), windows = emptyList(), sortOrder = 1)
        val snapshot = SlotsResponse(slots = listOf(a, b))
        assertEquals("Hades", buildWidgetCard(snapshot, 0, 1260, index = 2).title)   // 2 % 2 = 0
        assertEquals("Tunic", buildWidgetCard(snapshot, 0, 1260, index = -1).title)  // wraps to last
    }

    @Test fun wrapIndex_handlesNegativesAndOverflow() {
        assertEquals(0, wrapIndex(3, 3))
        assertEquals(2, wrapIndex(-1, 3))
        assertEquals(1, wrapIndex(4, 3))
        assertEquals(0, wrapIndex(0, 3))
    }

    @Test fun effectiveIndex_freshSelectionSticks_staleFallsBackToZero() {
        val now = 1_000_000L
        assertEquals(2, effectiveIndex(2, now - SELECTION_TTL_MILLIS + 1, now, listSize = 4))
        assertEquals(0, effectiveIndex(2, now - SELECTION_TTL_MILLIS, now, listSize = 4))
        assertEquals(0, effectiveIndex(2, now - SELECTION_TTL_MILLIS - 1, now, listSize = 4))
    }

    @Test fun effectiveIndex_outOfRangeOrEmptyList_fallsBackToZero() {
        val now = 1_000_000L
        assertEquals(0, effectiveIndex(7, now, now, listSize = 3))   // index beyond list
        assertEquals(0, effectiveIndex(-2, now, now, listSize = 3))  // negative index
        assertEquals(0, effectiveIndex(1, now, now, listSize = 0))   // empty list
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run (PowerShell): `cd "C:\Users\Jeff\Documents\Projects\Game Tracker\android"; .\gradlew.bat :app:testDebugUnitTest --tests "com.backlogquest.companion.widget.WidgetCycleTest" --console=plain 2>&1 | Select-Object -Last 20`
Expected: compilation FAILURE — `buildCycleList`, `wrapIndex`, `effectiveIndex`, `SELECTION_TTL_MILLIS` unresolved.

- [ ] **Step 3: Implement the cycle model**

In `android/app/src/main/java/com/backlogquest/companion/widget/WidgetContent.kt`:

(a) Extend imports:

```kotlin
import com.backlogquest.companion.data.Slot
import com.backlogquest.companion.data.SlotsResponse
import com.backlogquest.companion.schedule.DAY_MINUTES
import com.backlogquest.companion.schedule.minutesUntilActive
import com.backlogquest.companion.schedule.nextUpcoming
import com.backlogquest.companion.schedule.orderActive
import com.backlogquest.companion.schedule.slotActiveAt
import com.backlogquest.companion.schedule.windowCovers
```

(b) Add `deepLinkSlotId` to `WidgetCard`:

```kotlin
data class WidgetCard(
    val title: String,
    val slotLabel: String,
    val hint: String,
    val goal: String?,
    val coverUrl: String?,
    val deepLinkGameId: Int?,
    val deepLinkSlotId: Int? = null,
)
```

(c) Below `hasGame`/`activeHint`, add the new pure functions and rewrite `buildWidgetCard` on top of them (the empty-state branch and all hint strings are byte-identical to today):

```kotlin
/** Selection made via the widget's prev/next buttons goes stale after this. */
const val SELECTION_TTL_MILLIS: Long = 5L * 60L * 1000L

/** Wrap any index (including negatives) into [0, size). Size must be > 0. */
fun wrapIndex(index: Int, size: Int): Int = ((index % size) + size) % size

/**
 * The stored manual selection, or 0 (schedule's best pick) when it is stale,
 * out of range for the current cycle list, or the list is empty.
 */
fun effectiveIndex(storedIndex: Int, selectedAtMillis: Long, nowMillis: Long, listSize: Int): Int {
    if (listSize <= 0) return 0
    if (storedIndex !in 0 until listSize) return 0
    if (nowMillis - selectedAtMillis >= SELECTION_TTL_MILLIS) return 0
    return storedIndex
}

/**
 * Slots the widget can cycle through: slots with a game, active ones first
 * (most-restrictive first, mirroring orderActive), then not-yet-active ones by
 * soonest activation (tie-break sortOrder then id, mirroring nextUpcoming),
 * then never-activating ones in incoming order. Index 0 always equals what
 * buildWidgetCard(snapshot, weekday, minute) shows.
 */
fun buildCycleList(slots: List<Slot>, weekday: Int, minute: Int): List<Slot> {
    val withGame = slots.filter(::hasGame)
    val active = orderActive(withGame, weekday, minute)
    val activeIds = active.mapTo(HashSet()) { it.id }
    val pending = withGame.filter { it.id !in activeIds }
        .map { it to minutesUntilActive(it.windows, weekday, minute) }
    val (reachable, never) = pending.partition { it.second != null }
    val upcoming = reachable
        .sortedWith(compareBy({ it.second }, { it.first.sortOrder }, { it.first.id }))
        .map { it.first }
    return active + upcoming + never.map { it.first }
}

/** Card for one concrete slot (must have a game): active hint or Next-at hint. */
private fun cardForSlot(slot: Slot, weekday: Int, minute: Int): WidgetCard {
    val g = slot.currentGame!!
    val hint = if (slotActiveAt(slot.windows, weekday, minute)) {
        activeHint(slot, weekday, minute)
    } else {
        val until = minutesUntilActive(slot.windows, weekday, minute)
        if (until == null) {
            "Next: ${slot.label}"
        } else {
            val startMinute = (minute + until) % DAY_MINUTES
            "Next: ${slot.label} ${dayQualifier(weekday, minute, until)}at ${formatMinute(startMinute)}"
        }
    }
    return WidgetCard(
        title = g.title,
        slotLabel = slot.label,
        hint = hint,
        goal = slot.goal,
        coverUrl = g.coverUrl,
        deepLinkGameId = g.id,
        deepLinkSlotId = slot.id,
    )
}

/** Card at a cycle position; any index wraps. Falls back to the empty card. */
fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int, index: Int): WidgetCard {
    val cycle = buildCycleList(snapshot.slots, weekday, minute)
    if (cycle.isEmpty()) return buildWidgetCard(snapshot, weekday, minute)
    return cardForSlot(cycle[wrapIndex(index, cycle.size)], weekday, minute)
}
```

(d) Simplify the existing 3-arg `buildWidgetCard` to delegate for the non-empty case — replace its body with:

```kotlin
fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int): WidgetCard {
    val cycle = buildCycleList(snapshot.slots, weekday, minute)
    if (cycle.isNotEmpty()) return cardForSlot(cycle.first(), weekday, minute)
    return WidgetCard(
        title = "No picks scheduled",
        slotLabel = "",
        hint = "Set windows on the web",
        goal = null,
        coverUrl = null,
        deepLinkGameId = null,
        deepLinkSlotId = null,
    )
}
```

Note the mutual ordering: define the 3-arg version ABOVE the 4-arg version, and have the 4-arg version call the 3-arg for the empty case (no recursion loop: empty list short-circuits). The `nextUpcoming` import becomes unused — remove it.

- [ ] **Step 4: Run the widget test classes**

Run: `cd "C:\Users\Jeff\Documents\Projects\Game Tracker\android"; .\gradlew.bat :app:testDebugUnitTest --tests "com.backlogquest.companion.widget.*" --console=plain 2>&1 | Select-Object -Last 15`
Expected: BUILD SUCCESSFUL — `WidgetCycleTest` AND the untouched `WidgetContentTest` both pass (hint strings must match exactly; if `WidgetContentTest` fails, the refactor changed frozen behavior — fix the implementation, not the old test).

- [ ] **Step 5: Run the full unit suite**

Run: `.\gradlew.bat :app:testDebugUnitTest --console=plain 2>&1 | Select-Object -Last 10`
Expected: BUILD SUCCESSFUL (all ~106+ tests).

- [ ] **Step 6: Commit**

```powershell
cd "C:\Users\Jeff\Documents\Projects\Game Tracker"
git add android/app/src/main/java/com/backlogquest/companion/widget/WidgetContent.kt android/app/src/test/java/com/backlogquest/companion/widget/WidgetCycleTest.kt
git commit -m "feat(android): pure cycle model for widget prev/next (list, wrap, TTL)"
git push
```

---

### Task 2: Button row UI + Glance cycle action

**Files:**
- Create: `android/app/src/main/java/com/backlogquest/companion/widget/CycleAction.kt`
- Create: `android/app/src/main/res/drawable/ic_widget_prev.xml`
- Create: `android/app/src/main/res/drawable/ic_widget_next.xml`
- Create: `android/app/src/main/res/drawable/ic_widget_checklist.xml`
- Modify: `android/app/src/main/java/com/backlogquest/companion/widget/PicksWidget.kt`

**Interfaces:**
- Consumes: `buildCycleList`, `buildWidgetCard(snapshot, weekday, minute, index)`, `effectiveIndex`, `wrapIndex` from Task 1; `scheduleSnapshotStore` via `App.container`; `deviceNowWeekdayMinute()` from `ui.picks`.
- Produces (used by Task 3): `SelIndexKey: Preferences.Key<Int>`, `SelAtKey: Preferences.Key<Long>` (in `CycleAction.kt`). Produces (used by Task 4): widget fires `actionStartActivity<MainActivity>` with `EXTRA_OPEN_TAB` and (when known) `EXTRA_OPEN_SLOT_ID` params.

No practical unit test exists for Glance render/callbacks (no Robolectric in this project) — this task is verified by compilation + Task 5's on-device smoke. Keep every branchable decision inside the Task 1 pure functions.

- [ ] **Step 1: Create the three vector icons**

`android/app/src/main/res/drawable/ic_widget_prev.xml` (chevron-left, body-text grey):

```xml
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp" android:height="24dp"
    android:viewportWidth="24" android:viewportHeight="24">
    <path android:fillColor="#B7B9C6"
        android:pathData="M15.41,7.41L14,6l-6,6 6,6 1.41,-1.41L10.83,12z" />
</vector>
```

`android/app/src/main/res/drawable/ic_widget_next.xml` (chevron-right):

```xml
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp" android:height="24dp"
    android:viewportWidth="24" android:viewportHeight="24">
    <path android:fillColor="#B7B9C6"
        android:pathData="M10,6L8.59,7.41 13.17,12l-4.58,4.59L10,18l6,-6z" />
</vector>
```

`android/app/src/main/res/drawable/ic_widget_checklist.xml` (checklist, indigo like the slot label):

```xml
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp" android:height="24dp"
    android:viewportWidth="24" android:viewportHeight="24">
    <path android:fillColor="#8B93FF"
        android:pathData="M22,7h-9v2h9V7z M22,15h-9v2h9V15z M5.54,11L2,7.46l1.41,-1.41l2.12,2.12l4.24,-4.24l1.41,1.41L5.54,11z M5.54,19L2,15.46l1.41,-1.41l2.12,2.12l4.24,-4.24l1.41,1.41L5.54,19z" />
</vector>
```

- [ ] **Step 2: Create CycleAction.kt**

```kotlin
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
```

(`ScheduleSnapshot.slots` is the whole `SlotsResponse`; `.slots.slots` is the list — matches existing `PicksWidget` usage.)

- [ ] **Step 3: Rework PicksWidget.kt**

Add imports:

```kotlin
import androidx.datastore.preferences.core.Preferences
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.state.getAppWidgetState
import androidx.glance.currentState
import androidx.glance.layout.Box
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.size
import androidx.glance.state.PreferencesGlanceStateDefinition
```

Add the new extra + param key next to the existing ones:

```kotlin
/** Intent extra: slot id whose panel the Picks pager should open on (consumed in Task 4). */
const val EXTRA_OPEN_SLOT_ID = "open_slot_id"

private val OpenSlotParam = ActionParameters.Key<Int>(EXTRA_OPEN_SLOT_ID)
```

Replace `provideGlance` body (state read + indexed card):

```kotlin
override suspend fun provideGlance(context: Context, id: GlanceId) {
    val store = (context.applicationContext as App).container.scheduleSnapshotStore
    val snapshot = store.load()
    val prefs = getAppWidgetState(context, PreferencesGlanceStateDefinition, id)
    var cycleSize = 0
    val card: WidgetCard? = snapshot?.let {
        val (weekday, minute) = deviceNowWeekdayMinute()
        cycleSize = buildCycleList(it.slots.slots, weekday, minute).size
        val index = effectiveIndex(
            prefs[SelIndexKey] ?: 0, prefs[SelAtKey] ?: 0L,
            System.currentTimeMillis(), cycleSize,
        )
        buildWidgetCard(it.slots, weekday, minute, index)
    }
    val cover: Bitmap? = card?.coverUrl?.let { loadCoverBitmap(context, it) }
    provideContent { WidgetBody(card, cover, cycleSize) }
}
```

Replace `WidgetBody` with the Column layout — card content top-aligned (weighted), button row pinned at the bottom. The deep-link action (card tap AND middle button) carries the slot id when known:

```kotlin
@Composable
private fun WidgetBody(card: WidgetCard?, cover: Bitmap?, cycleSize: Int) {
    val openPicks = if (card?.deepLinkSlotId != null) {
        actionStartActivity<MainActivity>(
            actionParametersOf(OpenTabParam to "picks", OpenSlotParam to card.deepLinkSlotId),
        )
    } else {
        actionStartActivity<MainActivity>(actionParametersOf(OpenTabParam to "picks"))
    }
    // Scale the 3:4 cover to the height left above the button row.
    val coverHeight = (LocalSize.current.height - 68.dp).coerceIn(96.dp, 168.dp)
    val coverWidth = coverHeight * 3 / 4
    val tall = coverHeight >= 128.dp
    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .background(ImageProvider(R.drawable.widget_bg))
            .padding(12.dp),
    ) {
        Row(
            modifier = GlanceModifier.fillMaxWidth().defaultWeight().clickable(openPicks),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (cover != null) {
                Image(
                    provider = ImageProvider(cover),
                    contentDescription = card?.title ?: "Pick",
                    modifier = GlanceModifier.width(coverWidth).height(coverHeight),
                )
                Spacer(GlanceModifier.width(12.dp))
            }
            Column {
                Text(
                    text = card?.title ?: "No picks scheduled",
                    style = TextStyle(fontSize = if (tall) 18.sp else 16.sp, color = TitleColor),
                )
                if (card != null && card.slotLabel.isNotEmpty()) {
                    Text(
                        text = card.slotLabel,
                        style = TextStyle(fontSize = if (tall) 14.sp else 13.sp, color = SlotColor),
                    )
                }
                Text(
                    text = card?.hint ?: "Set windows on the web",
                    style = TextStyle(fontSize = if (tall) 14.sp else 13.sp, color = BodyColor),
                )
                if (card?.goal != null) {
                    Text(
                        text = "Goal: ${card.goal}",
                        style = TextStyle(fontSize = if (tall) 13.sp else 12.sp, color = BodyColor),
                    )
                }
            }
        }
        if (cycleSize > 1 || card?.deepLinkSlotId != null) {
            Row(
                modifier = GlanceModifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (cycleSize > 1) {
                    Image(
                        provider = ImageProvider(R.drawable.ic_widget_prev),
                        contentDescription = "Previous pick",
                        modifier = GlanceModifier.size(36.dp).clickable(
                            actionRunCallback<CycleAction>(actionParametersOf(DirectionParam to -1)),
                        ),
                    )
                }
                Box(modifier = GlanceModifier.defaultWeight()) {}
                if (card?.deepLinkSlotId != null) {
                    Image(
                        provider = ImageProvider(R.drawable.ic_widget_checklist),
                        contentDescription = "Open in Picks",
                        modifier = GlanceModifier.size(36.dp).clickable(openPicks),
                    )
                }
                Box(modifier = GlanceModifier.defaultWeight()) {}
                if (cycleSize > 1) {
                    Image(
                        provider = ImageProvider(R.drawable.ic_widget_next),
                        contentDescription = "Next pick",
                        modifier = GlanceModifier.size(36.dp).clickable(
                            actionRunCallback<CycleAction>(actionParametersOf(DirectionParam to +1)),
                        ),
                    )
                }
            }
        }
    }
}
```

(Buttons hide themselves when there is nothing to cycle / nowhere to deep-link — the empty state stays a plain card. The whole-card `clickable` moves from the root to the content Row so button taps don't double-fire.)

- [ ] **Step 4: Compile + full unit suite**

Run: `cd "C:\Users\Jeff\Documents\Projects\Game Tracker\android"; .\gradlew.bat assembleDebug :app:testDebugUnitTest --console=plain 2>&1 | Select-Object -Last 10`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

```powershell
cd "C:\Users\Jeff\Documents\Projects\Game Tracker"
git add android/app/src/main/java/com/backlogquest/companion/widget/ android/app/src/main/res/drawable/
git commit -m "feat(android): widget prev/checklist/next button row with snapshot cycling"
git push
```

---

### Task 3: RefreshWorker — 15-min tick, UPDATE policy, selection reset

**Files:**
- Modify: `android/app/src/main/java/com/backlogquest/companion/widget/RefreshWorker.kt`
- Test: `android/app/src/test/java/com/backlogquest/companion/widget/RefreshSchedulingTest.kt` (verify only, no changes expected)

**Interfaces:**
- Consumes: `SelIndexKey`, `SelAtKey` from Task 2; `PicksWidget` from Task 2.
- Produces: `WIDGET_TICK_MINUTES = 15L` (constant referenced nowhere else — verify with grep before assuming).

- [ ] **Step 1: Apply the changes**

In `RefreshWorker.kt`, change the tick constant:

```kotlin
const val WIDGET_TICK_MINUTES: Long = 15L                  // re-evaluate the primary every ~15 min
```

Add imports:

```kotlin
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.state.updateAppWidgetState
```

In `doWork()`, clear stale selections before the final re-render — insert between the existing `try/catch` block and the `updateAll` call:

```kotlin
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
```

In `enqueuePicksWidgetRefresh`, switch the policy so existing installs actually pick up the new 15-min interval:

```kotlin
fun enqueuePicksWidgetRefresh(context: Context) {
    val request = PeriodicWorkRequestBuilder<RefreshWorker>(WIDGET_TICK_MINUTES, TimeUnit.MINUTES).build()
    WorkManager.getInstance(context).enqueueUniquePeriodicWork(
        WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request,
    )
}
```

(`ExistingPeriodicWorkPolicy.UPDATE` needs WorkManager 2.8+; if the compile fails on the symbol, check `androidx.work:work-runtime-ktx` version in `android/app/build.gradle.kts` and bump to 2.9.0.)

- [ ] **Step 2: Run tests + compile**

Run: `cd "C:\Users\Jeff\Documents\Projects\Game Tracker\android"; .\gradlew.bat assembleDebug :app:testDebugUnitTest --console=plain 2>&1 | Select-Object -Last 10`
Expected: BUILD SUCCESSFUL (`RefreshSchedulingTest` only covers `shouldFetch`, unaffected).

- [ ] **Step 3: Commit**

```powershell
cd "C:\Users\Jeff\Documents\Projects\Game Tracker"
git add android/app/src/main/java/com/backlogquest/companion/widget/RefreshWorker.kt
git commit -m "feat(android): 15-min widget tick (UPDATE policy) + selection reset each tick"
git push
```

---

### Task 4: Deep link — open Picks pager on the shown slot

**Files:**
- Modify: `android/app/src/main/java/com/backlogquest/companion/MainActivity.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/ui/Nav.kt`
- Modify: `android/app/src/main/java/com/backlogquest/companion/ui/picks/PicksScreen.kt`
- Test: `android/app/src/test/java/com/backlogquest/companion/ui/SlotPageIndexTest.kt` (create)

**Interfaces:**
- Consumes: `EXTRA_OPEN_SLOT_ID` from Task 2 (`com.backlogquest.companion.widget`).
- Produces: `fun slotPageIndex(slots: List<Slot>, slotId: Int?): Int` (top-level in `PicksScreen.kt`); `AppNav(initialTab: String?, initialSlotId: Int?)`; `PicksScreen(onOpenGame, initialSlotId: Int?)`.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/backlogquest/companion/ui/SlotPageIndexTest.kt`:

```kotlin
package com.backlogquest.companion.ui

import com.backlogquest.companion.data.Slot
import com.backlogquest.companion.ui.picks.slotPageIndex
import org.junit.Assert.assertEquals
import org.junit.Test

class SlotPageIndexTest {
    private fun slot(id: Int) = Slot(id = id, label = "S$id", goal = null, sortOrder = 0,
        currentGame = null, windows = emptyList())

    @Test fun findsSlotIndexById() {
        assertEquals(2, slotPageIndex(listOf(slot(5), slot(9), slot(7)), slotId = 7))
    }

    @Test fun nullSlotId_defaultsToFirstPage() {
        assertEquals(0, slotPageIndex(listOf(slot(5), slot(9)), slotId = null))
    }

    @Test fun unknownSlotId_fallsBackToFirstPage() {
        assertEquals(0, slotPageIndex(listOf(slot(5), slot(9)), slotId = 42))
    }

    @Test fun emptySlots_returnsZero() {
        assertEquals(0, slotPageIndex(emptyList(), slotId = 3))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "C:\Users\Jeff\Documents\Projects\Game Tracker\android"; .\gradlew.bat :app:testDebugUnitTest --tests "com.backlogquest.companion.ui.SlotPageIndexTest" --console=plain 2>&1 | Select-Object -Last 15`
Expected: compilation FAILURE — `slotPageIndex` unresolved.

- [ ] **Step 3: Implement the chain**

(a) `PicksScreen.kt` — add the pure function (top-level, above `PicksScreen`) and thread the param:

```kotlin
/** Pager page for a deep-linked slot id; first page when null/unknown. */
fun slotPageIndex(slots: List<Slot>, slotId: Int?): Int {
    if (slotId == null) return 0
    val index = slots.indexOfFirst { it.id == slotId }
    return if (index >= 0) index else 0
}
```

Signature change:

```kotlin
@Composable
fun PicksScreen(onOpenGame: (Int) -> Unit, initialSlotId: Int? = null) {
```

Pass through on the Success branch:

```kotlin
is UiState.Success -> PicksContent(s.data, vm, onOpenGame, initialSlotId)
```

`PicksContent` signature + pager init:

```kotlin
private fun PicksContent(data: SlotsResponse, vm: PicksViewModel, onOpenGame: (Int) -> Unit, initialSlotId: Int? = null) {
```

```kotlin
    val pager = rememberPagerState(
        initialPage = slotPageIndex(data.slots, initialSlotId),
        pageCount = { data.slots.size },
    )
```

(b) `Nav.kt`:

```kotlin
fun AppNav(initialTab: String? = null, initialSlotId: Int? = null) {
```

```kotlin
            composable("picks") {
                com.backlogquest.companion.ui.picks.PicksScreen(
                    onOpenGame = { id -> nav.navigate("detail/$id") },
                    initialSlotId = initialSlotId,
                )
            }
```

(c) `MainActivity.kt`:

```kotlin
import com.backlogquest.companion.widget.EXTRA_OPEN_SLOT_ID
```

```kotlin
        val openTab = intent?.getStringExtra(EXTRA_OPEN_TAB)
        val openSlotId = intent?.getIntExtra(EXTRA_OPEN_SLOT_ID, -1)?.takeIf { it >= 0 }
        setContent {
            BacklogQuestTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    AppNav(initialTab = openTab, initialSlotId = openSlotId)
                }
            }
        }
```

Known parity limitation (accepted in spec): like `EXTRA_OPEN_TAB` today, a re-tap while the activity is already alive may not re-read extras (no `onNewIntent` override). Do not fix in this task.

- [ ] **Step 4: Run tests**

Run: `.\gradlew.bat :app:testDebugUnitTest --console=plain 2>&1 | Select-Object -Last 10`
Expected: BUILD SUCCESSFUL, all tests incl. `SlotPageIndexTest` pass.

- [ ] **Step 5: Commit**

```powershell
cd "C:\Users\Jeff\Documents\Projects\Game Tracker"
git add android/app/src/main/java/com/backlogquest/companion/MainActivity.kt android/app/src/main/java/com/backlogquest/companion/ui/Nav.kt android/app/src/main/java/com/backlogquest/companion/ui/picks/PicksScreen.kt android/app/src/test/java/com/backlogquest/companion/ui/SlotPageIndexTest.kt
git commit -m "feat(android): widget deep link opens Picks pager on the shown slot"
git push
```

---

### Task 5: Build, install, on-device smoke

**Files:** none (verification only). Device `R5GL11FYRGE` is plugged in; adb lives at `$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe`; screen is held awake via `svc power stayon true`.

- [ ] **Step 1: Build + install**

```powershell
cd "C:\Users\Jeff\Documents\Projects\Game Tracker\android"; .\gradlew.bat assembleDebug --console=plain 2>&1 | Select-Object -Last 5
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r "C:\Users\Jeff\Documents\Projects\Game Tracker\android\app\build\outputs\apk\debug\app-debug.apk"
```

Expected: `Success`.

- [ ] **Step 2: Owner smoke checklist (relay to owner)**

1. Widget shows card at top, three buttons at the bottom, no dead band.
2. `▶` advances to the next slate game (slot label + hint update); `◀` goes back; wraps at both ends.
3. Checklist button (and card tap) opens the app on the Picks tab **paged to the shown slot**.
4. After ~5+ minutes idle, the widget is back on the schedule's best pick.

- [ ] **Step 3: Turn the screen hold off once the owner confirms**

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" shell svc power stayon false
```

---

## Self-Review (done at planning time)

- **Spec coverage:** top-aligned card + button row (Task 2), cycle list ordering/wrap (Task 1+2), 5-min TTL + tick reset + 15-min tick + UPDATE policy (Tasks 1+3), deep link incl. fallback-to-page-0 (Task 4), tests for cycle builder/wrap/TTL/page-index (Tasks 1+4), on-device smoke (Task 5). Responsive cover scaling preserved with button-row allowance (Task 2). No gaps found.
- **Placeholder scan:** none.
- **Type consistency:** `buildCycleList(List<Slot>, Int, Int): List<Slot>`, `effectiveIndex(Int, Long, Long, Int): Int`, `wrapIndex(Int, Int): Int`, keys `SelIndexKey`/`SelAtKey`, `EXTRA_OPEN_SLOT_ID`, `slotPageIndex(List<Slot>, Int?): Int` — names match across Tasks 1–4.
