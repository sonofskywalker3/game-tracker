# Task 8 Report: WorkManager Widget Refresh + Picks Deep-Link

## Status: DONE

## Commit
`97bc643` — `feat(android): WorkManager widget refresh (~30m tick, stale-only network) + Picks deep-link`

## Gate Results
- All 83 unit tests pass, 0 failures (3 new RefreshSchedulingTest tests included)
- assembleDebug: BUILD SUCCESSFUL

## TDD Flow
1. Wrote `RefreshSchedulingTest.kt` first — confirmed RED (unresolved `shouldFetch`, `FETCH_STALE_MILLIS`)
2. Wrote `RefreshWorker.kt` — confirmed GREEN (3 tests pass, focused run)
3. Wired App/MainActivity/Nav — full gate BUILD SUCCESSFUL

## Files Created
- `android/app/src/main/java/com/gametracker/companion/widget/RefreshWorker.kt`
  - `FETCH_STALE_MILLIS = 90 * 60 * 1000L`
  - `WIDGET_TICK_MINUTES = 30L`
  - `fun shouldFetch(lastSavedMillis, nowMillis, staleAfterMillis): Boolean` — pure, no Android deps
  - `class RefreshWorker : CoroutineWorker` — loads cache, fetches+saves only when stale, keeps cache on failure, always calls `PicksWidget().updateAll()`
  - `fun enqueuePicksWidgetRefresh(context)` — `PeriodicWorkRequestBuilder` + `enqueueUniquePeriodicWork(KEEP)`
- `android/app/src/test/java/com/gametracker/companion/widget/RefreshSchedulingTest.kt`
  - 3 tests: never-fetched→true, stale→true, fresh→false

## Files Modified
- `App.kt`: added `enqueuePicksWidgetRefresh(this)` at end of `onCreate`
- `MainActivity.kt`: reads `intent?.getStringExtra(EXTRA_OPEN_TAB)`, passes as `AppNav(initialTab = openTab)`
- `ui/Nav.kt`: added `initialTab: String? = null` param + `LaunchedEffect(initialTab)` block; Scaffold body unchanged

## Self-Review Checklist
- [x] `shouldFetch` logic correct: null → true; stale (>= threshold) → true; fresh → false
- [x] `doWork` keeps cache on fetch failure (onSuccess only saves on success path)
- [x] `doWork` always calls `PicksWidget().updateAll()` regardless of fetch result
- [x] `enqueueUniquePeriodicWork` uses `ExistingPeriodicWorkPolicy.KEEP` (idempotent)
- [x] `AppNav` Scaffold body unchanged; only param + LaunchedEffect added
- [x] `MainActivity` reads extra and passes to `AppNav`
- [x] `EXTRA_OPEN_TAB` imported from `PicksWidget.kt`, not re-declared
- [x] `PicksWidget.kt` not touched (tap logic pre-existing)

## Concerns
None. Clean TDD cycle, no API mismatches, no blocked items.

---

## Fix Wave (Review Findings)

### Fix 1 (Important): `doWork` always updates widget on exception
**File:** `android/app/src/main/java/com/gametracker/companion/widget/RefreshWorker.kt`

Wrapped the cache-load + conditional-fetch + save block in a `try { ... } catch (e: Exception) { ... }`. Previously, any exception from `store.load()`, `container.repository.slots()`, or `store.save()` would unwind the stack and skip `PicksWidget().updateAll()`. Now, on exception the catch silently keeps the existing cache, and control always falls through to `PicksWidget().updateAll(applicationContext)` + `return Result.success()`.

### Fix 2 (Minor): boundary test for `shouldFetch`'s `>=` operator
**File:** `android/app/src/test/java/com/gametracker/companion/widget/RefreshSchedulingTest.kt`

Added `shouldFetch_atExactStaleBoundary` test: asserts that `lastSavedMillis = now - FETCH_STALE_MILLIS` (exactly at threshold) returns `true` — documenting the `>=` semantics of the stale check.

### Build Result
- 84 unit tests pass, 0 failures (was 83 + 1 new boundary test)
- assembleDebug: BUILD SUCCESSFUL
