# Schedule-Aware Picks — Plan C (Android) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Android companion app a home-screen Glance widget that shows the single most-restrictive currently-active slot's assigned game ("what to play right now"), and make the in-app Picks view schedule-aware — both driven by a pure-Kotlin matcher mirroring the backend's `slot_schedule.py`, kept correct offline off the phone clock.

**Architecture:** A pure-Kotlin matcher (`schedule/SlotSchedule.kt`, no Android deps) mirrors the Python `slot_schedule.py` predicates and ordering, unit-tested with cases mirrored 1:1 from the Python tests. The app caches the enriched `/api/slots` snapshot (slots + windows + assignments) to DataStore via a periodic WorkManager worker; both the Glance widget and the in-app Picks view re-evaluate the active/primary slot locally off the device clock, so they stay correct minute-to-minute and functional off-network. Editing stays web-only — Android is a reader.

**Tech Stack:** Kotlin 2.0.21, AGP 8.7.3, Jetpack Compose (BOM 2024.12.01), Jetpack Glance (`androidx.glance:glance-appwidget`), WorkManager (`androidx.work:work-runtime-ktx`), Retrofit + kotlinx.serialization, Coil 2.7.0 (bitmap loading for the widget), DataStore. JVM unit tests via JUnit4 + kotlinx-coroutines-test.

## Global Constraints

- **Build dir:** all gradle commands run from `android/`. Build/test gate: `./gradlew.bat testDebugUnitTest assembleDebug` (Windows wrapper). Both must pass for every task.
- **App identity:** applicationId `com.gametracker.companion`; namespace `com.gametracker.companion`; compileSdk 35, targetSdk 35, minSdk 26; JVM target 17.
- **Matcher is canonical-mirrored:** `schedule/SlotSchedule.kt` must replicate `slot_schedule.py` semantics exactly. Mirror the Python test cases — especially the midnight-cross `(weekday - 1) % 7` morning-portion split and the `inf`/anytime ordering-last rule. Replicate the ranking **output** (active slots in priority order; rank = list index), NOT the Python `order_active` input mutation.
- **Window shape (from the enriched `/api/slots`, Plan A):** each slot carries `windows: [{id, days, start_min, end_min}, ...]` plus `active_now` (bool) and `restrictiveness_rank` (int|null). `days` is a 7-bit mask, bit 0 = Monday .. bit 6 = Sunday. `start_min`/`end_min` are minutes since local midnight (0..1439). `end_min > start_min` = same-day; `end_min < start_min` = crosses midnight; `end_min == start_min` = degenerate (never active). A slot with zero windows = "anytime". The Android client recomputes active/ordering locally off the device clock and **ignores** the server's `active_now`/`restrictiveness_rank` (they may be stale by the time the widget ticks).
- **Subagents:** pytest temp-DB + static review ONLY for backend; for Android, write + JVM-unit-test + static review ONLY. Never touch the live `games.db`, the running `:5000` server, the network, or the device. The controller runs the gradle gate, commits, pushes, and does on-device verification.
- **Git:** work directly on `main` + push (no branches/PRs unless asked). Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PQZfqye4BxsLcC3ZiWo6fG
  ```
- **On-device smoke is DEFERRED:** device SM-S948U (serial `R5GL11FYRGE`) is off adb. Tasks 7–8 (Glance widget UI, WorkManager worker, deep-link) are verified by `assembleDebug` compile + static review now; on-device widget smoke happens when the owner reconnects the phone (`cd android && ./gradlew.bat installDebug` + `adb -s R5GL11FYRGE shell am start -n com.gametracker.companion/.MainActivity`, then add the widget to the home screen).

---

## File Structure

**New files:**
- `android/app/src/main/java/com/gametracker/companion/schedule/SlotSchedule.kt` — pure matcher: `ScheduleWindow` (@Serializable DTO + matcher input), `ScheduleSlot` interface, predicates (`windowCovers`, `slotActiveAt`, `restrictivenessScore`), ordering/selection (`orderActive`, `scheduleAwareOrder`, `minutesUntilActive`, `nextUpcoming`). No Android imports.
- `android/app/src/test/java/com/gametracker/companion/schedule/SlotScheduleTest.kt` — JVM tests mirroring `tests/test_slot_schedule.py`.
- `android/app/src/test/java/com/gametracker/companion/data/ScheduleDtoTest.kt` — JSON round-trip test for the enriched `/api/slots` (windows parse, unknown server fields ignored).
- `android/app/src/main/java/com/gametracker/companion/data/ScheduleSnapshotStore.kt` — snapshot persistence interface + DataStore impl + pure JSON (de)serialize helpers.
- `android/app/src/test/java/com/gametracker/companion/data/ScheduleSnapshotTest.kt` — snapshot JSON round-trip test.
- `android/app/src/main/java/com/gametracker/companion/widget/WidgetContent.kt` — pure widget-card model: `WidgetCard` + `buildWidgetCard(...)` (primary / next-upcoming / empty) + `formatMinute(...)`.
- `android/app/src/test/java/com/gametracker/companion/widget/WidgetContentTest.kt` — JVM tests for card selection + time formatting.
- `android/app/src/main/java/com/gametracker/companion/widget/PicksWidget.kt` — `GlanceAppWidget` (renders the card; cover bitmap via Coil → `ImageProvider`, text fallback; tap deep-link).
- `android/app/src/main/java/com/gametracker/companion/widget/PicksWidgetReceiver.kt` — `GlanceAppWidgetReceiver`.
- `android/app/src/main/java/com/gametracker/companion/widget/RefreshWorker.kt` — `CoroutineWorker` (fetch snapshot, save, update widget) + `shouldFetch(...)` pure staleness helper + scheduling helpers.
- `android/app/src/test/java/com/gametracker/companion/widget/RefreshSchedulingTest.kt` — JVM test for `shouldFetch`.
- `android/app/src/main/res/xml/picks_widget_info.xml` — `appwidget-provider` metadata.

**Modified files:**
- `android/app/src/main/java/com/gametracker/companion/data/Dtos.kt` — `Slot` implements `ScheduleSlot`, gains `windows`, `activeNow`, `restrictivenessRank`.
- `android/app/src/main/java/com/gametracker/companion/ui/picks/PicksViewModel.kt` — inject a clock; emit slots in schedule-aware order.
- `android/app/src/test/java/com/gametracker/companion/ui/PicksViewModelTest.kt` — schedule-aware ordering test.
- `android/gradle/libs.versions.toml` — add `glance` + `work` versions/libraries.
- `android/app/build.gradle.kts` — add glance + work dependencies.
- `android/app/src/main/AndroidManifest.xml` — register the widget receiver.
- `android/app/src/main/java/com/gametracker/companion/App.kt` — enqueue the periodic refresh worker on startup.
- `android/app/src/main/java/com/gametracker/companion/MainActivity.kt` + `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt` — handle the widget deep-link (open Picks tab).

---

### Task 1: Pure matcher — predicates (window_covers, slot_active_at, restrictiveness_score)

Mirrors `slot_schedule.py` lines 23–68 and `tests/test_slot_schedule.py`. Pure Kotlin, no Android imports. `ScheduleWindow` is `@Serializable` so the same type doubles as the `/api/slots` window DTO (Task 3).

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/schedule/SlotSchedule.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/schedule/SlotScheduleTest.kt`

**Interfaces:**
- Produces:
  - `data class ScheduleWindow(@SerialName("days") days: Int, @SerialName("start_min") startMin: Int, @SerialName("end_min") endMin: Int, val id: Int? = null)` — `@Serializable`.
  - `interface ScheduleSlot { val id: Int; val sortOrder: Int; val windows: List<ScheduleWindow> }`
  - `const val DAY_MINUTES = 1440`
  - `fun windowCovers(window: ScheduleWindow, weekday: Int, minute: Int): Boolean`
  - `fun slotActiveAt(windows: List<ScheduleWindow>, weekday: Int, minute: Int): Boolean`
  - `fun restrictivenessScore(windows: List<ScheduleWindow>): Double`

- [ ] **Step 1: Write the failing tests**

`android/app/src/test/java/com/gametracker/companion/schedule/SlotScheduleTest.kt`:
```kotlin
package com.gametracker.companion.schedule

import org.junit.Assert.*
import org.junit.Test

// weekday: 0=Mon..6=Sun ; minute: minutes since local midnight (0..1439)
class SlotScheduleTest {

    // ---- windowCovers: normal same-day window (end > start) ----
    @Test fun sameDayWindow_coversInside_excludesEnd() {
        // Mon only (bit0), 18:00 (1080) .. 23:00 (1380)
        val w = ScheduleWindow(days = 0b0000001, startMin = 1080, endMin = 1380)
        assertTrue(windowCovers(w, weekday = 0, minute = 1080))   // start inclusive
        assertTrue(windowCovers(w, weekday = 0, minute = 1200))
        assertFalse(windowCovers(w, weekday = 0, minute = 1380))  // end exclusive
        assertFalse(windowCovers(w, weekday = 0, minute = 1079))
        assertFalse(windowCovers(w, weekday = 1, minute = 1200))  // wrong day
    }

    // ---- windowCovers: crosses midnight (end < start) ----
    @Test fun midnightCross_coversStartDayEvening_andNextDayMorning() {
        // Mon set (bit0), 22:00 (1320) .. 02:00 (120) — wraps past midnight into Tue
        val w = ScheduleWindow(days = 0b0000001, startMin = 1320, endMin = 120)
        assertTrue(windowCovers(w, weekday = 0, minute = 1320))   // Mon 22:00 (start day, >= start)
        assertTrue(windowCovers(w, weekday = 0, minute = 1439))   // Mon 23:59
        assertTrue(windowCovers(w, weekday = 1, minute = 30))     // Tue 00:30 — morning belongs to (weekday-1)%7 == Mon
        assertTrue(windowCovers(w, weekday = 1, minute = 119))    // Tue 01:59
        assertFalse(windowCovers(w, weekday = 1, minute = 120))   // Tue 02:00 (end exclusive)
        assertFalse(windowCovers(w, weekday = 1, minute = 1320))  // Tue 22:00 — Tue not a start day
        assertFalse(windowCovers(w, weekday = 0, minute = 60))    // Mon 01:00 — Sun would own this, Sun not set
    }

    @Test fun midnightCross_wrapsWeekFromSundayToMonday() {
        // Sun set (bit6), 23:00 (1380) .. 01:00 (60). Mon 00:30 belongs to (Mon-1)%7 == Sun.
        val w = ScheduleWindow(days = 0b1000000, startMin = 1380, endMin = 60)
        assertTrue(windowCovers(w, weekday = 6, minute = 1400))   // Sun 23:20
        assertTrue(windowCovers(w, weekday = 0, minute = 30))     // Mon 00:30 via (0-1)%7==6==Sun
    }

    // ---- windowCovers: degenerate (end == start) never active ----
    @Test fun degenerateWindow_neverActive() {
        val w = ScheduleWindow(days = 0b1111111, startMin = 600, endMin = 600)
        assertFalse(windowCovers(w, weekday = 0, minute = 600))
        assertFalse(windowCovers(w, weekday = 3, minute = 600))
    }

    // ---- slotActiveAt ----
    @Test fun emptyWindows_isAnytimeActive() {
        assertTrue(slotActiveAt(emptyList(), weekday = 2, minute = 0))
        assertTrue(slotActiveAt(emptyList(), weekday = 5, minute = 1439))
    }

    @Test fun anyWindowCovering_makesSlotActive() {
        val morning = ScheduleWindow(days = 0b1111111, startMin = 360, endMin = 540)   // 06:00-09:00
        val evening = ScheduleWindow(days = 0b1111111, startMin = 1200, endMin = 1380) // 20:00-23:00
        assertTrue(slotActiveAt(listOf(morning, evening), weekday = 0, minute = 1260)) // in evening
        assertFalse(slotActiveAt(listOf(morning, evening), weekday = 0, minute = 720))  // noon, neither
    }

    // ---- restrictivenessScore: total active minutes/week; empty -> +inf ----
    @Test fun emptyWindows_scoreInfinity() {
        assertEquals(Double.POSITIVE_INFINITY, restrictivenessScore(emptyList()), 0.0)
    }

    @Test fun score_sumsDaysCountTimesLength_sameDay() {
        // Mon+Tue (2 days), 20:00..23:00 = 180 min -> 2*180 = 360
        val w = ScheduleWindow(days = 0b0000011, startMin = 1200, endMin = 1380)
        assertEquals(360.0, restrictivenessScore(listOf(w)), 0.0)
    }

    @Test fun score_midnightCrossLength_andSumsMultipleWindows() {
        // 1 day, 22:00 (1320)..02:00 (120) -> (1440-1320)+120 = 240 ; plus Mon+Tue 1h window = 2*60=120
        val cross = ScheduleWindow(days = 0b0000001, startMin = 1320, endMin = 120)
        val short = ScheduleWindow(days = 0b0000011, startMin = 600, endMin = 660)
        assertEquals(240.0 + 120.0, restrictivenessScore(listOf(cross, short)), 0.0)
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.schedule.SlotScheduleTest"`
Expected: compile failure / unresolved reference `ScheduleWindow`, `windowCovers`, etc.

- [ ] **Step 3: Write the minimal implementation**

`android/app/src/main/java/com/gametracker/companion/schedule/SlotSchedule.kt`:
```kotlin
package com.gametracker.companion.schedule

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Pure schedule matcher for slots — active-now + restrictiveness ordering.
 * Canonical mirror of the backend's slot_schedule.py; keep the two in lockstep.
 *
 * A window's `days` is a 7-bit mask (bit 0 = Monday .. bit 6 = Sunday). start_min/
 * end_min are minutes since local midnight (0..1439). end_min > start_min is a normal
 * same-day window; end_min < start_min crosses midnight; end_min == start_min is
 * degenerate (never active). A slot with zero windows is 'anytime'.
 */

const val DAY_MINUTES = 1440

@Serializable
data class ScheduleWindow(
    val days: Int,
    @SerialName("start_min") val startMin: Int,
    @SerialName("end_min") val endMin: Int,
    val id: Int? = null,
)

interface ScheduleSlot {
    val id: Int
    val sortOrder: Int
    val windows: List<ScheduleWindow>
}

private fun daySet(days: Int, weekday: Int): Boolean = (days and (1 shl weekday)) != 0

/** True if (weekday, minute) falls inside this window. Handles midnight-cross. */
fun windowCovers(window: ScheduleWindow, weekday: Int, minute: Int): Boolean {
    val days = window.days
    val start = window.startMin
    val end = window.endMin
    if (end > start) {                       // normal, same-day window
        return daySet(days, weekday) && minute in start until end
    }
    if (end < start) {                       // crosses midnight
        val onStartDay = daySet(days, weekday) && minute >= start
        val prevDay = ((weekday - 1) % 7 + 7) % 7   // morning portion belongs to day-after a set day
        val onNextDay = daySet(days, prevDay) && minute < end
        return onStartDay || onNextDay
    }
    return false                             // degenerate (end == start)
}

/** A slot is active if it has no windows (anytime) or any window covers now. */
fun slotActiveAt(windows: List<ScheduleWindow>, weekday: Int, minute: Int): Boolean {
    if (windows.isEmpty()) return true
    return windows.any { windowCovers(it, weekday, minute) }
}

private fun windowLength(startMin: Int, endMin: Int): Int = when {
    endMin > startMin -> endMin - startMin
    endMin < startMin -> (DAY_MINUTES - startMin) + endMin
    else -> 0
}

/**
 * Total active minutes per week. Smaller = more restrictive. Zero windows ('anytime')
 * scores +infinity so it always sorts last. Overlapping windows are summed (an
 * acceptable approximation for ordering).
 */
fun restrictivenessScore(windows: List<ScheduleWindow>): Double {
    if (windows.isEmpty()) return Double.POSITIVE_INFINITY
    var total = 0
    for (w in windows) {
        total += Integer.bitCount(w.days) * windowLength(w.startMin, w.endMin)
    }
    return total.toDouble()
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.schedule.SlotScheduleTest"`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/schedule/SlotSchedule.kt android/app/src/test/java/com/gametracker/companion/schedule/SlotScheduleTest.kt
git commit -m "feat(android): pure schedule matcher predicates (mirror slot_schedule.py)"
```

---

### Task 2: Pure matcher — ordering & selection (order_active, schedule-aware order, next-upcoming)

Mirrors `slot_schedule.py` `order_active` (lines 71–80) but **pure** (no input mutation — rank = list index). Adds the helpers the widget + app need: full schedule-aware ordering and next-upcoming lookahead.

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/schedule/SlotSchedule.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/schedule/SlotScheduleTest.kt`

**Interfaces:**
- Consumes: `ScheduleWindow`, `ScheduleSlot`, `slotActiveAt`, `restrictivenessScore`, `windowCovers`, `DAY_MINUTES` (Task 1).
- Produces:
  - `fun <T : ScheduleSlot> orderActive(slots: List<T>, weekday: Int, minute: Int): List<T>` — active slots only, most-restrictive-first (score asc, then sortOrder, then id). Rank = index. Pure.
  - `fun <T : ScheduleSlot> scheduleAwareOrder(slots: List<T>, weekday: Int, minute: Int): List<T>` — active slots (ranked) followed by inactive slots in their incoming order. Pure.
  - `fun minutesUntilActive(windows: List<ScheduleWindow>, weekday: Int, minute: Int): Int?` — 0 if active now (incl. anytime), else minutes to the next activation within the next 7 days, or null if never.
  - `data class Upcoming<T>(val slot: T, val minutesUntil: Int)`
  - `fun <T : ScheduleSlot> nextUpcoming(slots: List<T>, weekday: Int, minute: Int, hasGame: (T) -> Boolean): Upcoming<T>?` — among inactive slots passing `hasGame`, the soonest to activate (tie-break sortOrder, then id).

- [ ] **Step 1: Write the failing tests** (append to `SlotScheduleTest.kt`)

```kotlin
    // ---- test slot impl ----
    private data class TestSlot(
        override val id: Int,
        override val sortOrder: Int,
        override val windows: List<ScheduleWindow>,
    ) : ScheduleSlot

    private fun win(days: Int, start: Int, end: Int) = ScheduleWindow(days, start, end)
    private val allDays = 0b1111111

    // ---- orderActive: most-restrictive-first, anytime last, tie-break sortOrder then id ----
    @Test fun orderActive_filtersInactive_andSortsByScore_anytimeLast() {
        // now = Mon 21:00 (weekday 0, minute 1260)
        val tight = TestSlot(1, 0, listOf(win(0b0000001, 1200, 1380)))   // Mon 20-23, 1 day*180=180
        val wide  = TestSlot(2, 0, listOf(win(allDays, 1200, 1380)))     // every day 20-23, 7*180=1260
        val anytime = TestSlot(3, 0, emptyList())                        // inf -> last
        val inactive = TestSlot(4, 0, listOf(win(allDays, 360, 540)))    // 06-09, not active at 21:00
        val ordered = orderActive(listOf(anytime, wide, tight, inactive), weekday = 0, minute = 1260)
        assertEquals(listOf(1, 2, 3), ordered.map { it.id })             // tight, wide, anytime; inactive dropped
    }

    @Test fun orderActive_tieBreak_sortOrderThenId() {
        // two slots with identical scores -> sortOrder asc, then id asc
        val a = TestSlot(id = 9, sortOrder = 1, windows = listOf(win(allDays, 1200, 1380)))
        val b = TestSlot(id = 8, sortOrder = 0, windows = listOf(win(allDays, 1200, 1380)))
        val c = TestSlot(id = 7, sortOrder = 1, windows = listOf(win(allDays, 1200, 1380)))
        val ordered = orderActive(listOf(a, b, c), weekday = 0, minute = 1260)
        assertEquals(listOf(8, 7, 9), ordered.map { it.id })  // b(so0), then so1 by id: 7,9
    }

    @Test fun orderActive_midnightCross_activeInMorningPortion() {
        // window Mon 22:00..02:00 ; now Tue 00:30 -> active via (weekday-1)%7
        val late = TestSlot(1, 0, listOf(win(0b0000001, 1320, 120)))
        val ordered = orderActive(listOf(late), weekday = 1, minute = 30)
        assertEquals(listOf(1), ordered.map { it.id })
    }

    // ---- scheduleAwareOrder: active (ranked) then inactive (incoming order) ----
    @Test fun scheduleAwareOrder_activeFirstThenInactiveInOrder() {
        val activeWide = TestSlot(1, 0, listOf(win(allDays, 1200, 1380)))  // active
        val inactiveA  = TestSlot(2, 0, listOf(win(allDays, 360, 540)))    // inactive
        val activeTight= TestSlot(3, 0, listOf(win(0b0000001, 1200, 1380)))// active, more restrictive
        val inactiveB  = TestSlot(4, 0, listOf(win(allDays, 0, 60)))       // inactive
        val out = scheduleAwareOrder(listOf(activeWide, inactiveA, activeTight, inactiveB),
                                     weekday = 0, minute = 1260)
        assertEquals(listOf(3, 1, 2, 4), out.map { it.id })  // tight, wide, then inactive A,B in order
    }

    // ---- minutesUntilActive ----
    @Test fun minutesUntilActive_zeroWhenActiveOrAnytime() {
        assertEquals(0, minutesUntilActive(emptyList(), 0, 0))
        val active = listOf(win(allDays, 1200, 1380))
        assertEquals(0, minutesUntilActive(active, weekday = 0, minute = 1260))
    }

    @Test fun minutesUntilActive_laterToday() {
        // 20:00 window, now 18:00 -> 120 min
        assertEquals(120, minutesUntilActive(listOf(win(allDays, 1200, 1380)), weekday = 0, minute = 1080))
    }

    @Test fun minutesUntilActive_nextDay() {
        // Tue-only 09:00 window, now Mon 23:00 -> until Tue 09:00 = 60 (to midnight) + 540 = 600
        assertEquals(600, minutesUntilActive(listOf(win(0b0000010, 540, 600)), weekday = 0, minute = 1380))
    }

    @Test fun minutesUntilActive_nullWhenNever() {
        assertNull(minutesUntilActive(listOf(win(0b0000000, 540, 600)), weekday = 0, minute = 0)) // no days set
    }

    // ---- nextUpcoming ----
    @Test fun nextUpcoming_picksSoonestInactiveWithGame() {
        val soon = TestSlot(1, 0, listOf(win(allDays, 1320, 1380)))  // 22:00, now 21:00 -> 60 min
        val later= TestSlot(2, 0, listOf(win(allDays, 1380, 1410)))  // 23:00 -> 120 min
        val out = nextUpcoming(listOf(later, soon), weekday = 0, minute = 1260, hasGame = { true })
        assertEquals(1, out!!.slot.id)
        assertEquals(60, out.minutesUntil)
    }

    @Test fun nextUpcoming_skipsSlotsWithoutGame_andReturnsNullWhenNone() {
        val noGame = TestSlot(1, 0, listOf(win(allDays, 1320, 1380)))
        assertNull(nextUpcoming(listOf(noGame), weekday = 0, minute = 1260, hasGame = { false }))
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.schedule.SlotScheduleTest"`
Expected: unresolved references `orderActive`, `scheduleAwareOrder`, `minutesUntilActive`, `nextUpcoming`, `Upcoming`.

- [ ] **Step 3: Write the minimal implementation** (append to `SlotSchedule.kt`)

```kotlin
/**
 * Active slots only, most-restrictive-first (score asc, then sortOrder, then id).
 * Pure: rank is the returned list index (mirrors order_active's restrictiveness_rank
 * output WITHOUT mutating the inputs).
 */
fun <T : ScheduleSlot> orderActive(slots: List<T>, weekday: Int, minute: Int): List<T> =
    slots.filter { slotActiveAt(it.windows, weekday, minute) }
        .sortedWith(
            compareBy({ restrictivenessScore(it.windows) }, { it.sortOrder }, { it.id })
        )

/** Active slots (ranked) followed by the inactive slots in their incoming order. */
fun <T : ScheduleSlot> scheduleAwareOrder(slots: List<T>, weekday: Int, minute: Int): List<T> {
    val active = orderActive(slots, weekday, minute)
    val activeIds = active.mapTo(HashSet()) { it.id }
    val inactive = slots.filter { it.id !in activeIds }
    return active + inactive
}

/**
 * Minutes until the slot next becomes active, scanning the next 7 days minute-by-minute.
 * 0 if active now (incl. anytime). null if it never activates within a week.
 */
fun minutesUntilActive(windows: List<ScheduleWindow>, weekday: Int, minute: Int): Int? {
    if (slotActiveAt(windows, weekday, minute)) return 0
    for (delta in 1..(7 * DAY_MINUTES)) {
        val abs = weekday * DAY_MINUTES + minute + delta
        val wd = (abs / DAY_MINUTES) % 7
        val m = abs % DAY_MINUTES
        if (windows.any { windowCovers(it, wd, m) }) return delta
    }
    return null
}

data class Upcoming<T>(val slot: T, val minutesUntil: Int)

/**
 * Among inactive slots passing [hasGame], the soonest to activate (tie-break sortOrder,
 * then id). null if none qualify.
 */
fun <T : ScheduleSlot> nextUpcoming(
    slots: List<T>,
    weekday: Int,
    minute: Int,
    hasGame: (T) -> Boolean,
): Upcoming<T>? =
    slots.asSequence()
        .filter { hasGame(it) && !slotActiveAt(it.windows, weekday, minute) }
        .mapNotNull { s -> minutesUntilActive(s.windows, weekday, minute)?.let { Upcoming(s, it) } }
        .sortedWith(compareBy({ it.minutesUntil }, { it.slot.sortOrder }, { it.slot.id }))
        .firstOrNull()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.schedule.SlotScheduleTest"`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/schedule/SlotSchedule.kt android/app/src/test/java/com/gametracker/companion/schedule/SlotScheduleTest.kt
git commit -m "feat(android): schedule matcher ordering + next-upcoming selection"
```

---

### Task 3: Slot DTO — consume enriched /api/slots windows

`Slot` implements `ScheduleSlot` and gains `windows` (+ the server's `active_now`/`restrictiveness_rank`, parsed but unused on Android). `sortOrder` already maps `sort_order`.

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/data/Dtos.kt:75-83` (the `Slot` data class)
- Test: `android/app/src/test/java/com/gametracker/companion/data/ScheduleDtoTest.kt`

**Interfaces:**
- Consumes: `ScheduleWindow`, `ScheduleSlot` (Task 1).
- Produces: `Slot : ScheduleSlot` with `windows: List<ScheduleWindow>`, `activeNow: Boolean`, `restrictivenessRank: Int?`.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/data/ScheduleDtoTest.kt`:
```kotlin
package com.gametracker.companion.data

import com.gametracker.companion.schedule.ScheduleSlot
import org.junit.Assert.*
import org.junit.Test

class ScheduleDtoTest {
    private val json = appJson()

    @Test fun parsesSlotWindowsAndIgnoresUnknownServerFields() {
        // shape mirrors GET /api/slots (Plan A): windows[] + active_now + restrictiveness_rank
        val body = """
        {"slots":[
          {"id":5,"label":"Evening","goal":"Beat ch.1","sort_order":2,
           "current_game":{"id":42,"title":"Hades","cover_url":"http://x/h.png"},
           "candidates":[],
           "windows":[{"id":11,"days":127,"start_min":1200,"end_min":1380}],
           "active_now":true,"restrictiveness_rank":0}
        ],"recently_finished":[]}
        """.trimIndent()
        val resp = json.decodeFromString(SlotsResponse.serializer(), body)
        val slot = resp.slots.single()
        assertEquals(5, slot.id)
        assertEquals(2, slot.sortOrder)
        assertEquals(1, slot.windows.size)
        assertEquals(127, slot.windows[0].days)
        assertEquals(1200, slot.windows[0].startMin)
        assertEquals(1380, slot.windows[0].endMin)
        assertTrue(slot.activeNow)
        assertEquals(0, slot.restrictivenessRank)
        // implements the matcher interface
        val asSchedule: ScheduleSlot = slot
        assertEquals(5, asSchedule.id)
    }

    @Test fun defaultsWhenWindowsAbsent() {
        val body = """{"slots":[{"id":1,"label":"X"}],"recently_finished":[]}"""
        val resp = json.decodeFromString(SlotsResponse.serializer(), body)
        assertTrue(resp.slots.single().windows.isEmpty())
        assertFalse(resp.slots.single().activeNow)
        assertNull(resp.slots.single().restrictivenessRank)
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.ScheduleDtoTest"`
Expected: compile failure — `Slot` has no `windows`/`activeNow`/`restrictivenessRank`, does not implement `ScheduleSlot`.

- [ ] **Step 3: Modify the `Slot` data class**

Replace `Dtos.kt:75-83` with:
```kotlin
@Serializable
data class Slot(
    override val id: Int,
    val label: String,
    val goal: String? = null,
    @SerialName("sort_order") override val sortOrder: Int = 0,
    @SerialName("current_game") val currentGame: SlotCandidate? = null,
    val candidates: List<RankedCandidate> = emptyList(),
    override val windows: List<com.gametracker.companion.schedule.ScheduleWindow> = emptyList(),
    @SerialName("active_now") val activeNow: Boolean = false,
    @SerialName("restrictiveness_rank") val restrictivenessRank: Int? = null,
) : com.gametracker.companion.schedule.ScheduleSlot
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.ScheduleDtoTest"`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data/Dtos.kt android/app/src/test/java/com/gametracker/companion/data/ScheduleDtoTest.kt
git commit -m "feat(android): Slot DTO consumes schedule windows (implements ScheduleSlot)"
```

---

### Task 4: Schedule snapshot store (cache slots+windows+assignments to DataStore)

Persists the last fetched `SlotsResponse` (with a fetch timestamp) so the widget/app work offline. The JSON (de)serialize is pure + tested; the DataStore wrapper is thin (untested, matching the existing `SettingsStore` precedent).

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/data/ScheduleSnapshotStore.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/data/ScheduleSnapshotTest.kt`

**Interfaces:**
- Consumes: `appJson()` (Networking.kt), `SlotsResponse`, `Slot`, `ScheduleWindow`.
- Produces:
  - `data class ScheduleSnapshot(val slots: SlotsResponse, val savedAtMillis: Long)`
  - `fun encodeSnapshot(snapshot: ScheduleSnapshot, json: Json = appJson()): String`
  - `fun decodeSnapshot(raw: String, json: Json = appJson()): ScheduleSnapshot?` — returns null on malformed input (never throws).
  - `interface ScheduleSnapshotStore { suspend fun save(snapshot: ScheduleSnapshot); suspend fun load(): ScheduleSnapshot? }`
  - `class DataStoreScheduleSnapshotStore(context: Context) : ScheduleSnapshotStore`

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/data/ScheduleSnapshotTest.kt`:
```kotlin
package com.gametracker.companion.data

import com.gametracker.companion.schedule.ScheduleWindow
import org.junit.Assert.*
import org.junit.Test

class ScheduleSnapshotTest {
    @Test fun roundTripsSlotsAndTimestamp() {
        val resp = SlotsResponse(
            slots = listOf(
                Slot(
                    id = 1, label = "Evening", goal = "Beat ch.1", sortOrder = 0,
                    currentGame = SlotCandidate(42, "Hades", "http://x/h.png"),
                    windows = listOf(ScheduleWindow(days = 127, startMin = 1200, endMin = 1380, id = 9)),
                ),
            ),
        )
        val snap = ScheduleSnapshot(resp, savedAtMillis = 1_700_000_000_000L)
        val decoded = decodeSnapshot(encodeSnapshot(snap))
        assertNotNull(decoded)
        assertEquals(1_700_000_000_000L, decoded!!.savedAtMillis)
        val slot = decoded.slots.slots.single()
        assertEquals("Evening", slot.label)
        assertEquals(42, slot.currentGame?.id)
        assertEquals(1200, slot.windows.single().startMin)
    }

    @Test fun decodeReturnsNullOnGarbage() {
        assertNull(decodeSnapshot("not json {{{"))
        assertNull(decodeSnapshot(""))
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.ScheduleSnapshotTest"`
Expected: unresolved references `ScheduleSnapshot`, `encodeSnapshot`, `decodeSnapshot`.

- [ ] **Step 3: Write the implementation**

`android/app/src/main/java/com/gametracker/companion/data/ScheduleSnapshotStore.kt`:
```kotlin
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.data.ScheduleSnapshotTest"`
Expected: green.

- [ ] **Step 5: Wire the store into `AppContainer`**

In `android/app/src/main/java/com/gametracker/companion/AppContainer.kt`, add after the `repository` line:
```kotlin
    val scheduleSnapshotStore: ScheduleSnapshotStore = DataStoreScheduleSnapshotStore(appContext)
```
(and import `com.gametracker.companion.data.DataStoreScheduleSnapshotStore` + `com.gametracker.companion.data.ScheduleSnapshotStore`).

- [ ] **Step 6: Verify the module still compiles**

Run: `cd android && ./gradlew.bat compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/data/ScheduleSnapshotStore.kt android/app/src/test/java/com/gametracker/companion/data/ScheduleSnapshotTest.kt android/app/src/main/java/com/gametracker/companion/AppContainer.kt
git commit -m "feat(android): schedule snapshot store (DataStore cache + JSON round-trip)"
```

---

### Task 5: Pure widget-card computation (primary / next-upcoming / empty + time formatting)

The pure logic that drives the Glance widget: pick the headline card off the device clock. Primary = top active slot **with an assigned game** (active-but-empty slots are skipped for the headline). Else next-upcoming slot with a game. Else an empty-state message.

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/widget/WidgetContent.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/widget/WidgetContentTest.kt`

**Interfaces:**
- Consumes: `Slot`, `SlotsResponse` (data), `orderActive`, `nextUpcoming`, `windowCovers`, `DAY_MINUTES` (schedule).
- Produces:
  - `data class WidgetCard(val title: String, val slotLabel: String, val hint: String, val goal: String?, val coverUrl: String?, val deepLinkGameId: Int?)`
  - `fun formatMinute(minute: Int): String` — 12-hour, e.g. 1380 -> "11:00pm", 0 -> "12:00am".
  - `fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int): WidgetCard`

- [ ] **Step 1: Write the failing tests**

`android/app/src/test/java/com/gametracker/companion/widget/WidgetContentTest.kt`:
```kotlin
package com.gametracker.companion.widget

import com.gametracker.companion.data.Slot
import com.gametracker.companion.data.SlotCandidate
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.schedule.ScheduleWindow
import org.junit.Assert.*
import org.junit.Test

class WidgetContentTest {
    private fun win(days: Int, start: Int, end: Int) = ScheduleWindow(days, start, end)
    private val allDays = 0b1111111
    private fun slot(id: Int, label: String, game: SlotCandidate?, windows: List<ScheduleWindow>,
                     goal: String? = null, sortOrder: Int = 0) =
        Slot(id = id, label = label, goal = goal, sortOrder = sortOrder, currentGame = game, windows = windows)

    @Test fun formatMinute_twelveHour() {
        assertEquals("12:00am", formatMinute(0))
        assertEquals("9:00am", formatMinute(540))
        assertEquals("12:00pm", formatMinute(720))
        assertEquals("8:00pm", formatMinute(1200))
        assertEquals("11:00pm", formatMinute(1380))
        assertEquals("11:30pm", formatMinute(1410))
    }

    @Test fun primary_isTopActiveSlotWithGame_skipsActiveButEmpty() {
        // now Mon 21:00. tightEmpty is most-restrictive but has no game -> skipped.
        val tightEmpty = slot(1, "Lunch", game = null, windows = listOf(win(0b0000001, 1200, 1380)))
        val eveningGame = slot(2, "Evening",
            game = SlotCandidate(42, "Hades", "http://x/h.png"),
            windows = listOf(win(allDays, 1200, 1380)), goal = "Beat ch.1")
        val card = buildWidgetCard(SlotsResponse(slots = listOf(tightEmpty, eveningGame)),
                                   weekday = 0, minute = 1260)
        assertEquals("Hades", card.title)
        assertEquals("Evening", card.slotLabel)
        assertEquals(42, card.deepLinkGameId)
        assertEquals("http://x/h.png", card.coverUrl)
        assertEquals("Beat ch.1", card.goal)
        assertTrue(card.hint.contains("until 11:00pm"))  // active-window end hint
    }

    @Test fun fallback_nextUpcomingWhenNoneActiveWithGame() {
        // now Mon 18:00 (1080). evening slot has a game, activates 20:00.
        val evening = slot(2, "Evening", game = SlotCandidate(7, "Celeste"),
            windows = listOf(win(allDays, 1200, 1380)))
        val card = buildWidgetCard(SlotsResponse(slots = listOf(evening)), weekday = 0, minute = 1080)
        assertEquals("Celeste", card.title)
        assertTrue(card.hint.startsWith("Next:"))
        assertTrue(card.hint.contains("8:00pm"))
        assertEquals(7, card.deepLinkGameId)
    }

    @Test fun empty_whenNothingScheduledWithAGame() {
        val card = buildWidgetCard(SlotsResponse(slots = emptyList()), weekday = 0, minute = 1080)
        assertNull(card.deepLinkGameId)
        assertNull(card.coverUrl)
        assertTrue(card.title.contains("No picks"))
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.widget.WidgetContentTest"`
Expected: unresolved references `WidgetCard`, `formatMinute`, `buildWidgetCard`.

- [ ] **Step 3: Write the implementation**

`android/app/src/main/java/com/gametracker/companion/widget/WidgetContent.kt`:
```kotlin
package com.gametracker.companion.widget

import com.gametracker.companion.data.Slot
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.schedule.DAY_MINUTES
import com.gametracker.companion.schedule.nextUpcoming
import com.gametracker.companion.schedule.orderActive
import com.gametracker.companion.schedule.windowCovers

data class WidgetCard(
    val title: String,
    val slotLabel: String,
    val hint: String,
    val goal: String?,
    val coverUrl: String?,
    val deepLinkGameId: Int?,
)

/** 12-hour clock label, e.g. 0 -> "12:00am", 1380 -> "11:00pm". */
fun formatMinute(minute: Int): String {
    val m = ((minute % DAY_MINUTES) + DAY_MINUTES) % DAY_MINUTES
    val h24 = m / 60
    val min = m % 60
    val ampm = if (h24 < 12) "am" else "pm"
    val h12 = when (val h = h24 % 12) { 0 -> 12; else -> h }
    return "%d:%02d%s".format(h12, min, ampm)
}

private fun hasGame(slot: Slot): Boolean = slot.currentGame != null

/** The end-of-active-window hint for an active slot, e.g. "Active · until 11:00pm". */
private fun activeHint(slot: Slot, weekday: Int, minute: Int): String {
    val covering = slot.windows.firstOrNull { windowCovers(it, weekday, minute) }
    return if (covering != null) "Active · until ${formatMinute(covering.endMin)}" else "Active now"
}

/**
 * Headline card off the device clock: primary = top active slot with a game; else the
 * next-upcoming slot with a game; else an empty-state message.
 */
fun buildWidgetCard(snapshot: SlotsResponse, weekday: Int, minute: Int): WidgetCard {
    val slots = snapshot.slots
    val primary = orderActive(slots, weekday, minute).firstOrNull { hasGame(it) }
    if (primary != null) {
        val g = primary.currentGame!!
        return WidgetCard(
            title = g.title,
            slotLabel = primary.label,
            hint = activeHint(primary, weekday, minute),
            goal = primary.goal,
            coverUrl = g.coverUrl,
            deepLinkGameId = g.id,
        )
    }
    val upcoming = nextUpcoming(slots, weekday, minute, ::hasGame)
    if (upcoming != null) {
        val g = upcoming.slot.currentGame!!
        val startMinute = (minute + upcoming.minutesUntil) % DAY_MINUTES
        return WidgetCard(
            title = g.title,
            slotLabel = upcoming.slot.label,
            hint = "Next: ${upcoming.slot.label} at ${formatMinute(startMinute)}",
            goal = upcoming.slot.goal,
            coverUrl = g.coverUrl,
            deepLinkGameId = g.id,
        )
    }
    return WidgetCard(
        title = "No picks scheduled",
        slotLabel = "",
        hint = "Set windows on the web",
        goal = null,
        coverUrl = null,
        deepLinkGameId = null,
    )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.widget.WidgetContentTest"`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/widget/WidgetContent.kt android/app/src/test/java/com/gametracker/companion/widget/WidgetContentTest.kt
git commit -m "feat(android): pure widget-card computation (primary/next-upcoming/empty)"
```

---

### Task 6: Schedule-aware in-app Picks ordering

The in-app Picks view sorts currently-active slots most-restrictive-first (then inactive in their backend order), driven by the device clock. Done in `PicksViewModel` with an injectable clock so it's JVM-testable; the screen is unchanged (it already iterates `data.slots` in order). Manual reorder still works — it sets the backend `sort_order`, which is the tie-break within equal restrictiveness and the order of inactive slots.

**Files:**
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/picks/PicksViewModel.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/ui/PicksViewModelTest.kt`

**Interfaces:**
- Consumes: `scheduleAwareOrder` (schedule), `SlotsResponse`, `Repository`.
- Produces: `PicksViewModel(repository, nowProvider: () -> Pair<Int, Int> = ::deviceNowWeekdayMinute)` — `nowProvider` returns `(weekday 0=Mon..6=Sun, minute-of-day)`. Slots are emitted in schedule-aware order on `load`/`refresh`.

- [ ] **Step 1: Write the failing test** (append to `PicksViewModelTest.kt`)

```kotlin
    @Test fun load_ordersActiveSlotsMostRestrictiveFirst() = runTest {
        val win = com.gametracker.companion.schedule.ScheduleWindow(days = 0b1111111, startMin = 1200, endMin = 1380)
        val tight = com.gametracker.companion.schedule.ScheduleWindow(days = 0b0000001, startMin = 1200, endMin = 1380)
        // incoming order: wide(1), inactive(2 @ morning), tight(3)
        val wide = Slot(id = 1, label = "Wide", windows = listOf(win))
        val inactive = Slot(id = 2, label = "Morning",
            windows = listOf(com.gametracker.companion.schedule.ScheduleWindow(0b1111111, 360, 540)))
        val tightSlot = Slot(id = 3, label = "Tight", windows = listOf(tight))
        val repo = FakeRepo(slotsResp = SlotsResponse(slots = listOf(wide, inactive, tightSlot)))
        // Fixed clock: Mon 21:00 -> tight & wide active, morning inactive
        val vm = PicksViewModel(repo.asRepository(), nowProvider = { 0 to 1260 })
        vm.load(); advanceUntilIdle()
        val st = vm.state.value as UiState.Success
        assertEquals(listOf(3, 1, 2), st.data.slots.map { it.id })  // tight, wide, inactive
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.ui.PicksViewModelTest"`
Expected: `PicksViewModel` has no `nowProvider` parameter; slots not reordered.

- [ ] **Step 3: Modify `PicksViewModel`**

Replace the class header + the two state-setting helpers so every emission runs through `scheduleAwareOrder`. Full file:
```kotlin
package com.gametracker.companion.ui.picks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gametracker.companion.data.GameSummary
import com.gametracker.companion.data.Repository
import com.gametracker.companion.data.SlotsResponse
import com.gametracker.companion.schedule.scheduleAwareOrder
import com.gametracker.companion.ui.common.UiState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.util.Calendar

/** (weekday 0=Mon..6=Sun, minute-of-day) from the device clock. */
fun deviceNowWeekdayMinute(): Pair<Int, Int> {
    val c = Calendar.getInstance()
    // Calendar.DAY_OF_WEEK: Sunday=1..Saturday=7 -> convert to Mon=0..Sun=6
    val weekday = ((c.get(Calendar.DAY_OF_WEEK) + 5) % 7)
    val minute = c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE)
    return weekday to minute
}

class PicksViewModel(
    private val repository: Repository,
    private val nowProvider: () -> Pair<Int, Int> = ::deviceNowWeekdayMinute,
) : ViewModel() {

    private val _state = MutableStateFlow<UiState<SlotsResponse>>(UiState.Loading)
    val state: StateFlow<UiState<SlotsResponse>> = _state

    private val _picker = MutableStateFlow<List<GameSummary>>(emptyList())
    val picker: StateFlow<List<GameSummary>> = _picker

    /** Re-order the slots schedule-aware (active most-restrictive-first) off the clock. */
    private fun ordered(resp: SlotsResponse): SlotsResponse {
        val (weekday, minute) = nowProvider()
        return resp.copy(slots = scheduleAwareOrder(resp.slots, weekday, minute))
    }

    private fun emit(resp: SlotsResponse) {
        _state.value = if (resp.slots.isEmpty()) UiState.Empty else UiState.Success(ordered(resp))
    }

    fun load() = viewModelScope.launch {
        _state.value = UiState.Loading
        repository.slots().fold(
            onSuccess = { emit(it) },
            onFailure = { _state.value = UiState.Error(it.message ?: "Can't reach Game Tracker") },
        )
    }

    fun pin(slotId: Int, gameId: Int, goal: String?) = viewModelScope.launch {
        if (repository.pin(slotId, gameId, goal).isSuccess) refresh()
    }

    fun applyOutcome(slotId: Int, outcome: String) = viewModelScope.launch {
        if (repository.outcome(slotId, outcome).isSuccess) refresh()
    }

    fun editGoal(slotId: Int, goal: String?) = viewModelScope.launch {
        if (repository.setGoal(slotId, goal).isSuccess) refresh()
    }

    fun reorder(slotIds: List<Int>) = viewModelScope.launch {
        if (repository.reorderSlots(slotIds).isSuccess) refresh()
    }

    /** Reload after a mutation WITHOUT going through Loading. */
    private suspend fun refresh() {
        repository.slots().onSuccess { emit(it) }
    }

    fun searchLibrary(q: String) = viewModelScope.launch {
        _picker.value = if (q.length < 2) emptyList()
                        else repository.games(search = q).getOrDefault(emptyList())
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass** (the whole PicksViewModel suite — confirm no regression)

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.ui.PicksViewModelTest"`
Expected: all green (existing tests pass; the FakeRepo with no windows = anytime slots, which order stably).

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/ui/picks/PicksViewModel.kt android/app/src/test/java/com/gametracker/companion/ui/PicksViewModelTest.kt
git commit -m "feat(android): schedule-aware in-app Picks ordering (active most-restrictive-first)"
```

---

### Task 7: Glance widget — deps, receiver, UI, manifest

Adds the `androidx.glance:glance-appwidget` + `androidx.work:work-runtime-ktx` dependencies and builds the single-card widget: reads the cached snapshot, computes the card via `buildWidgetCard` off the device clock, renders cover (Coil bitmap → `ImageProvider`, text fallback), title/slot/hint/goal, and a tap action that deep-links into the Picks tab. **No JVM unit test** — verified by `assembleDebug` compile + static review; on-device smoke deferred.

**Files:**
- Modify: `android/gradle/libs.versions.toml`
- Modify: `android/app/build.gradle.kts`
- Create: `android/app/src/main/java/com/gametracker/companion/widget/PicksWidget.kt`
- Create: `android/app/src/main/java/com/gametracker/companion/widget/PicksWidgetReceiver.kt`
- Create: `android/app/src/main/res/xml/picks_widget_info.xml`
- Modify: `android/app/src/main/AndroidManifest.xml`

**Interfaces:**
- Consumes: `buildWidgetCard`, `WidgetCard` (Task 5), `DataStoreScheduleSnapshotStore` / `App.container.scheduleSnapshotStore` (Task 4), `deviceNowWeekdayMinute` (Task 6).
- Produces: `class PicksWidget : GlanceAppWidget`, `class PicksWidgetReceiver : GlanceAppWidgetReceiver`, the intent extra constant `EXTRA_OPEN_TAB`/value used by the deep-link (consumed in Task 8).

- [ ] **Step 1: Add versions + libraries to `libs.versions.toml`**

Under `[versions]` add:
```toml
glance = "1.1.1"
work = "2.10.0"
```
Under `[libraries]` add:
```toml
glance-appwidget = { module = "androidx.glance:glance-appwidget", version.ref = "glance" }
glance-material3 = { module = "androidx.glance:glance-material3", version.ref = "glance" }
work-runtime-ktx = { module = "androidx.work:work-runtime-ktx", version.ref = "work" }
```

- [ ] **Step 2: Add dependencies to `app/build.gradle.kts`**

In the `dependencies { ... }` block (with the other `implementation(...)` lines):
```kotlin
    implementation(libs.glance.appwidget)
    implementation(libs.glance.material3)
    implementation(libs.work.runtime.ktx)
```

- [ ] **Step 3: Create the widget metadata `res/xml/picks_widget_info.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<appwidget-provider xmlns:android="http://schemas.android.com/apk/res/android"
    android:minWidth="180dp"
    android:minHeight="110dp"
    android:targetCellWidth="3"
    android:targetCellHeight="2"
    android:resizeMode="horizontal|vertical"
    android:widgetCategory="home_screen"
    android:initialLayout="@layout/glance_default_loading_layout"
    android:updatePeriodMillis="0" />
```
(`@layout/glance_default_loading_layout` ships with the Glance library.)

- [ ] **Step 4: Create `PicksWidget.kt`**

```kotlin
package com.gametracker.companion.widget

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.provideContent
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import coil.ImageLoader
import coil.request.ImageRequest
import coil.request.SuccessResult
import com.gametracker.companion.App
import com.gametracker.companion.MainActivity

class PicksWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val store = (context.applicationContext as App).container.scheduleSnapshotStore
        val snapshot = store.load()
        val card = snapshot?.let {
            val (weekday, minute) = com.gametracker.companion.ui.picks.deviceNowWeekdayMinute()
            buildWidgetCard(it.slots, weekday, minute)
        }
        val cover = card?.coverUrl?.let { loadCoverBitmap(context, it) }
        provideContent { WidgetBody(card, cover) }
    }

    /** Best-effort cover load → bitmap for ImageProvider; null on any failure. */
    private suspend fun loadCoverBitmap(context: Context, url: String): android.graphics.Bitmap? {
        val loader = ImageLoader(context)
        val request = ImageRequest.Builder(context).data(url).allowHardware(false).build()
        val result = loader.execute(request)
        if (result !is SuccessResult) return null
        return (result.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap
    }
}

@Composable
private fun WidgetBody(card: WidgetCard?, cover: android.graphics.Bitmap?) {
    val openPicks = actionStartActivity(
        Intent(/* context set by Glance */).apply {
            // resolved below via the typed overload
        },
    )
    Row(
        modifier = GlanceModifier.fillMaxSize().padding(12.dp)
            .clickable(actionStartActivity<MainActivity>()),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (cover != null) {
            Image(
                provider = ImageProvider(cover),
                contentDescription = card?.title ?: "Pick",
                modifier = GlanceModifier.width(72.dp).height(96.dp),
            )
            Spacer(GlanceModifier.width(12.dp))
        }
        Column {
            Text(
                card?.title ?: "No picks scheduled",
                style = TextStyle(color = ColorProvider(Color.Black), fontSize = 16.dp.value.sp()),
            )
            if (card != null && card.slotLabel.isNotEmpty()) {
                Text(card.slotLabel, style = TextStyle(color = ColorProvider(Color.DarkGray)))
            }
            Text(card?.hint ?: "Set windows on the web",
                style = TextStyle(color = ColorProvider(Color.DarkGray)))
            card?.goal?.let { Text("Goal: $it", style = TextStyle(color = ColorProvider(Color.DarkGray))) }
        }
    }
}

// Glance text sizes are sp via androidx.glance.text; small helper to keep call sites tidy.
private fun Float.sp() = androidx.compose.ui.unit.TextUnit(this, androidx.compose.ui.unit.TextUnitType.Sp)
```

> **Implementer note:** Glance's exact API surface (e.g. `Image`/`ImageProvider(Bitmap)`, `TextStyle.fontSize`, `actionStartActivity<MainActivity>()`) is version-sensitive. The intent above is illustrative — make it **compile against the resolved `glance` version**: use `actionStartActivity<MainActivity>(actionParametersOf(...))` to attach the `EXTRA_OPEN_TAB` ("picks") used in Task 8, set Glance text sizes with `androidx.glance.text` `TextStyle(fontSize = 16.sp)` (import `androidx.compose.ui.unit.sp`), and drop the unused `openPicks` placeholder. The required behavior: render the card, tap → launch `MainActivity` with the open-Picks signal, text fallback when `cover == null`.

- [ ] **Step 5: Create `PicksWidgetReceiver.kt`**

```kotlin
package com.gametracker.companion.widget

import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver

class PicksWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = PicksWidget()
}
```

- [ ] **Step 6: Register the receiver in `AndroidManifest.xml`**

Inside `<application>` (after the `MainActivity` `<activity>` block):
```xml
        <receiver
            android:name=".widget.PicksWidgetReceiver"
            android:exported="false">
            <intent-filter>
                <action android:name="android.appwidget.action.APPWIDGET_UPDATE" />
            </intent-filter>
            <meta-data
                android:name="android.appwidget.provider"
                android:resource="@xml/picks_widget_info" />
        </receiver>
```

- [ ] **Step 7: Compile (the gate for this task)**

Run: `cd android && ./gradlew.bat testDebugUnitTest assembleDebug`
Expected: BUILD SUCCESSFUL (existing JVM tests still pass; the widget compiles). Resolve any Glance API mismatches against version `1.1.1` per the implementer note — the deliverable is a compiling widget that renders the card and deep-links on tap.

- [ ] **Step 8: Commit**

```bash
git add android/gradle/libs.versions.toml android/app/build.gradle.kts android/app/src/main/java/com/gametracker/companion/widget/PicksWidget.kt android/app/src/main/java/com/gametracker/companion/widget/PicksWidgetReceiver.kt android/app/src/main/res/xml/picks_widget_info.xml android/app/src/main/AndroidManifest.xml
git commit -m "feat(android): Glance single-card picks widget (cover + hint + deep-link)"
```

---

### Task 8: WorkManager refresh worker + scheduling + widget deep-link handling

A `CoroutineWorker` periodically (~30 min) updates the widget off the cached snapshot and refetches `/api/slots` from the network only when the cache is stale (> ~90 min), so the widget re-evaluates the primary every tick yet stays offline-resilient. Scheduling is enqueued on app startup. The widget tap opens the Picks tab.

**Files:**
- Create: `android/app/src/main/java/com/gametracker/companion/widget/RefreshWorker.kt`
- Test: `android/app/src/test/java/com/gametracker/companion/widget/RefreshSchedulingTest.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/App.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/MainActivity.kt`
- Modify: `android/app/src/main/java/com/gametracker/companion/ui/Nav.kt`

**Interfaces:**
- Consumes: `Repository.slots()`, `ScheduleSnapshotStore`, `App.container`, `PicksWidget` (to trigger update), `EXTRA_OPEN_TAB` (Task 7).
- Produces:
  - `fun shouldFetch(lastSavedMillis: Long?, nowMillis: Long, staleAfterMillis: Long = FETCH_STALE_MILLIS): Boolean` — pure staleness decision.
  - `const val FETCH_STALE_MILLIS` (~90 min), `const val WIDGET_TICK_MINUTES` (30L).
  - `class RefreshWorker(...) : CoroutineWorker`, `fun enqueuePicksWidgetRefresh(context: Context)`.

- [ ] **Step 1: Write the failing test**

`android/app/src/test/java/com/gametracker/companion/widget/RefreshSchedulingTest.kt`:
```kotlin
package com.gametracker.companion.widget

import org.junit.Assert.*
import org.junit.Test

class RefreshSchedulingTest {
    @Test fun shouldFetch_whenNeverFetched() {
        assertTrue(shouldFetch(lastSavedMillis = null, nowMillis = 1_000L))
    }

    @Test fun shouldFetch_whenStale() {
        val now = 100_000_000L
        assertTrue(shouldFetch(lastSavedMillis = now - (FETCH_STALE_MILLIS + 1), nowMillis = now))
    }

    @Test fun shouldNotFetch_whenFresh() {
        val now = 100_000_000L
        assertFalse(shouldFetch(lastSavedMillis = now - 1_000L, nowMillis = now))
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.widget.RefreshSchedulingTest"`
Expected: unresolved references `shouldFetch`, `FETCH_STALE_MILLIS`.

- [ ] **Step 3: Write `RefreshWorker.kt`**

```kotlin
package com.gametracker.companion.widget

import android.content.Context
import androidx.glance.appwidget.GlanceAppWidgetManager
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd android && ./gradlew.bat testDebugUnitTest --tests "com.gametracker.companion.widget.RefreshSchedulingTest"`
Expected: green.

- [ ] **Step 5: Enqueue on startup in `App.kt`**

```kotlin
package com.gametracker.companion

import android.app.Application
import com.gametracker.companion.widget.enqueuePicksWidgetRefresh

class App : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        enqueuePicksWidgetRefresh(this)
    }
}
```

- [ ] **Step 6: Deep-link — open the Picks tab on widget tap**

In `widget/PicksWidget.kt`, define the extra constant (top-level): `const val EXTRA_OPEN_TAB = "open_tab"` and have the tap launch `MainActivity` with `EXTRA_OPEN_TAB = "picks"` (via `actionParametersOf` + `actionStartActivity<MainActivity>(...)` or an explicit `Intent`). In `MainActivity.kt`, read the extra and pass the requested tab into `AppNav`:
```kotlin
package com.gametracker.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import com.gametracker.companion.ui.AppNav
import com.gametracker.companion.ui.theme.GameTrackerTheme
import com.gametracker.companion.widget.EXTRA_OPEN_TAB

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val openTab = intent?.getStringExtra(EXTRA_OPEN_TAB)
        setContent {
            GameTrackerTheme {
                Surface(color = MaterialTheme.colorScheme.background) { AppNav(initialTab = openTab) }
            }
        }
    }
}
```
In `ui/Nav.kt`, give `AppNav` the optional param and navigate once if provided:
```kotlin
@Composable
fun AppNav(initialTab: String? = null) {
    val nav = rememberNavController()
    androidx.compose.runtime.LaunchedEffect(initialTab) {
        if (initialTab != null) nav.navigate(initialTab) { launchSingleTop = true }
    }
    // ... existing Scaffold body unchanged ...
}
```
(`"picks"` is already the start destination, so a cold start lands there anyway; this handles the already-running case. Keep the rest of `AppNav` exactly as-is.)

- [ ] **Step 7: Gate — full build + tests**

Run: `cd android && ./gradlew.bat testDebugUnitTest assembleDebug`
Expected: BUILD SUCCESSFUL; all JVM tests pass.

- [ ] **Step 8: Commit**

```bash
git add android/app/src/main/java/com/gametracker/companion/widget/RefreshWorker.kt android/app/src/test/java/com/gametracker/companion/widget/RefreshSchedulingTest.kt android/app/src/main/java/com/gametracker/companion/App.kt android/app/src/main/java/com/gametracker/companion/MainActivity.kt android/app/src/main/java/com/gametracker/companion/ui/Nav.kt android/app/src/main/java/com/gametracker/companion/widget/PicksWidget.kt
git commit -m "feat(android): WorkManager widget refresh (~30m tick, stale-only network) + Picks deep-link"
```

---

### Task 9: Final gate + whole-branch review

**Files:** none (verification only).

- [ ] **Step 1: Full build + unit-test gate**

Run: `cd android && ./gradlew.bat testDebugUnitTest assembleDebug`
Expected: BUILD SUCCESSFUL; all JVM unit tests pass (SlotSchedule, ScheduleDto, ScheduleSnapshot, WidgetContent, PicksViewModel, RefreshScheduling, plus all pre-existing tests).

- [ ] **Step 2: Confirm the matcher mirrors the Python reference**

Compare `schedule/SlotSchedule.kt` against `slot_schedule.py` and `SlotScheduleTest.kt` against `tests/test_slot_schedule.py`. Confirm: midnight-cross `(weekday-1)%7` morning split, `end` exclusivity, degenerate `end==start`, anytime→active + `inf` score-last, tie-break (score, sortOrder, id). Note any case present in Python but absent in Kotlin and add it.

- [ ] **Step 3: Whole-branch review**

Dispatch a final whole-branch review (most capable model) over the full Plan C commit range (BASE = the commit before Task 1 .. HEAD). Verdict must be "ready to merge" with no Critical/Important before push. Fix any Critical/Important inline (new commit), re-run the gate.

- [ ] **Step 4: Push**

```bash
git push origin main
```

- [ ] **Step 5: Record deferred on-device smoke**

On-device widget smoke is DEFERRED until the device is back on adb. When reconnected:
```
cd android && ./gradlew.bat installDebug
adb -s R5GL11FYRGE shell am start -n com.gametracker.companion/.MainActivity
```
Then add the "Game Tracker Picks" widget to the home screen and verify: primary card shows the top active slot's game (cover or text fallback), hint reads "Active · until …", tap opens the Picks tab, and the card advances when the clock crosses a window boundary (or after the ~30-min tick). Update `.superpowers/sdd/progress.md` + memory `schedule-aware-picks-and-widget`.

---

## Self-Review

**Spec coverage** (against `2026-06-23-schedule-aware-picks-and-widget-design.md` §"Android — widget + lean reader" and §Plan C):
- Pure-Kotlin matcher mirroring `slot_schedule.py` → Tasks 1–2 (predicates + ordering/selection), with mirrored tests incl. midnight split + inf/anytime ordering. ✅
- Glance single-card widget (primary; cover bitmap via Coil→ImageProvider + text fallback; title/slot/hint/goal; tap→Picks deep-link) → Tasks 5 (pure card) + 7 (Glance UI) + 8 (deep-link). ✅
- WorkManager periodic refresh (~1–2h network) + ~30-min widget re-eval off the clock, offline-resilient → Task 8 (`shouldFetch` stale-only network at 90 min; 30-min tick re-renders). ✅
- Caches slots+windows+assignments to disk → Task 4 (DataStore snapshot store). ✅
- New deps glance-appwidget + work-runtime-ktx + manifest receiver → Task 7. ✅
- Enriched `/api/slots` (windows field) consumed by the Slot DTO → Task 3. ✅
- App Picks view schedule-aware (active most-restrictive-first) → Task 6. ✅
- No-active-slot fallback = next-upcoming pick; active-but-empty skipped for headline; "nothing scheduled" message → Task 5 (`buildWidgetCard`). ✅
- Timezone = device local time (Android) — `deviceNowWeekdayMinute` off `Calendar` → Task 6. ✅
- Editing stays web-only (Android reader) — no schedule/profile editors added. ✅

**Placeholder scan:** Task 7's `PicksWidget.kt` carries an explicit implementer note that the Glance API surface is version-sensitive and the intent wiring is illustrative — this is a deliberate, flagged adaptation point (Glance API specifics resolve against the actual `1.1.1` artifacts at build time), not an unscoped placeholder; the required behavior is stated precisely and the compile gate (Task 7 Step 7) enforces it.

**Type consistency:** `ScheduleWindow`/`ScheduleSlot` (Task 1) are consumed unchanged by Tasks 2/3/5/6. `Slot` gains `windows`/`activeNow`/`restrictivenessRank` (Task 3) and is used by Tasks 5/6. `buildWidgetCard`/`WidgetCard` (Task 5) consumed by Task 7. `ScheduleSnapshot`/`ScheduleSnapshotStore` (Task 4) consumed by Tasks 7/8. `deviceNowWeekdayMinute` (Task 6) consumed by Tasks 7/8. `shouldFetch`/`FETCH_STALE_MILLIS`/`WIDGET_TICK_MINUTES`/`EXTRA_OPEN_TAB` (Tasks 7/8) consistent across worker + deep-link. No naming drift found.
</content>
</invoke>
