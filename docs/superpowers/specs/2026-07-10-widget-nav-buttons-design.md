# Widget Nav Buttons — Design (2026-07-10)

Approved by owner in-session. Scope: Android Glance picks widget only (Project A).
The gamification system (Project B, "Earn → Juice → Spend") is a separate spec;
decisions already made for it are recorded at the bottom so they aren't lost.

## Goal

The widget is the **decision surface**: sit down, swipe through the slate,
pick tonight's game, then switch to the app to track. Today it shows one
schedule-chosen card with dead space below on tall placements. Rework:

- Card content pinned to the **top** of the frame (cover + title/slot/hint,
  keeping the existing responsive cover scaling from SizeMode.Exact).
- **Button row below**: `◀` prev — `☑` checklist — `▶` next, app-palette tint
  on the existing dark card drawable.

## Cycle behavior (prev/next)

- Cycle list built purely from the cached `SlotsResponse` snapshot (no network):
  slots **with a game**, active slots first in `orderActive` order (so index 0
  is exactly what `buildWidgetCard` shows today), then inactive slots ordered
  by `minutesUntilActive` (soonest first). Empty slots skipped. Wraps at ends.
- Implemented via the app's first Glance `ActionCallback` + per-widget state:
  each tap stores a selection (index + timestamp) and re-renders from cache.
- Card text (slot label, Active-until / Next-at hint) always reflects the slot
  the displayed game belongs to.

## Snap-back

Android widgets get no screen-wake event, so:

- A manual selection **expires after 5 minutes** (`WIDGET_SELECTION_TTL_MIN`):
  any later render falls back to the schedule's best pick (index 0).
- Every `RefreshWorker` tick resets the stored selection; tick interval drops
  **30 → 15 minutes** (`WIDGET_TICK_MINUTES`) — it reads a local cache, so the
  battery cost is negligible.
- Accepted limitation: within ~5 minutes of cycling, a re-glance may still show
  the manual choice. At decision time that is usually correct anyway.

## Deep link (middle button + whole card)

- Both fire `actionStartActivity<MainActivity>` with `open_tab=picks` **plus a
  new `open_slot_id` extra** carrying the displayed slot's id.
- `MainActivity` → `AppNav` → Picks screen: the existing `HorizontalPager`
  opens already paged to that slot's panel (goal + outcome buttons — and later,
  the battle screen).
- If the slot id is missing from the freshly loaded slate (deleted/emptied),
  fall back to the current behavior: Picks tab, default page.

## Testing

Pure-Kotlin units beside the existing widget tests (`WidgetContentTest` etc.):

- cycle-list builder (ordering, empty-slot skipping),
- index wrapping both directions,
- selection-expiry decision (fresh vs stale timestamp),
- deep-link slot-id fallback resolution in the Picks VM/nav layer.

Existing `buildWidgetCard` tests continue to pass (index 0 must equal its
current primary/upcoming choice).

## Recorded Project-B decisions (not in this spec's scope)

- Build order: **B1 Earn** (schema, XP/gold/level rules engine, quick-log
  chips +15m/+30m/+1h/other + achievement button, goal picker + check-off) →
  **B2 Juice** (enemy = game cover, S/M/L + black aura by goal tier, attack /
  killing-blow / level-up ceremony, next-goal prompt) → **B3 Spend** (HP decay
  + potions + revive + camp/rest days, level-up stat picks incl. CON = max HP,
  cosmetics + app-theme shop). HP decay stays off until potions exist.
- Server = referee (all math deterministic, table-driven, in Flask; web
  canonical), clients = theater (API returns battle-event envelope).
- Goals: one active goal per game, stored on the game (survives slot moves),
  visible/editable **only while slotted**. Goal picker offers preset types
  Beat-the-game (boss) / 100% (superboss, multiplier) / custom bite-sized
  (minion). Checking a boss/superboss goal doubles as the existing
  Complete/100% outcome. Drop = strategic retreat, no penalty.
- Off-picks (unslotted) play: time-logging only, reduced XP; minimal XP for
  100%-status games, ~50% for beaten-not-100%. Slotted-only activity feeds
  the streak/HP system.
