"""Pure schedule matcher for slots — active-now + restrictiveness ordering.

A window is a dict with int keys: days (7-bit mask, bit 0 = Monday .. bit 6 =
Sunday), start_min, end_min (minutes since local midnight, 0..1439). end_min >
start_min is a normal window; end_min < start_min crosses midnight; end_min ==
start_min is degenerate (never active). A slot with zero windows is 'anytime'.

This module is the canonical reference for the Kotlin matcher in Plan C — keep
the two in lockstep.
"""

DAY_MINUTES = 1440
WEEK_MINUTES = 7 * DAY_MINUTES


def _day_set(days: int, weekday: int) -> bool:
    """True if the given weekday's bit is set in the mask."""
    return bool(days & (1 << weekday))


def window_covers(window: dict, weekday: int, minute: int) -> bool:
    """True if (weekday, minute) falls inside this window. Handles midnight-cross."""
    days = window["days"]
    start = window["start_min"]
    end = window["end_min"]
    if end > start:                       # normal, same-day window
        return _day_set(days, weekday) and start <= minute < end
    if end < start:                       # crosses midnight
        on_start_day = _day_set(days, weekday) and minute >= start
        prev_day = (weekday - 1) % 7      # the morning portion belongs to day-after a set day
        on_next_day = _day_set(days, prev_day) and minute < end
        return on_start_day or on_next_day
    return False                          # degenerate (end == start)


def slot_active_at(windows: list[dict], weekday: int, minute: int) -> bool:
    """A slot is active if it has no windows (anytime) or any window covers now."""
    if not windows:
        return True
    return any(window_covers(w, weekday, minute) for w in windows)


def _window_length(start_min: int, end_min: int) -> int:
    """Length of a window in minutes (handles midnight-cross; degenerate -> 0)."""
    if end_min > start_min:
        return end_min - start_min
    if end_min < start_min:
        return (DAY_MINUTES - start_min) + end_min
    return 0


def restrictiveness_score(windows: list[dict]) -> float:
    """Total active minutes per week. Smaller = more restrictive. Zero windows
    ('anytime') scores infinity so it always sorts last. Overlapping windows are
    summed (an acceptable approximation for ordering)."""
    if not windows:
        return float("inf")
    total = 0
    for w in windows:
        total += bin(w["days"]).count("1") * _window_length(w["start_min"], w["end_min"])
    return float(total)


def order_active(slots: list[dict], weekday: int, minute: int) -> list[dict]:
    """Return only the active slots, most-restrictive-first (score asc, then
    sort_order, then id). Mutates each returned slot to add 'restrictiveness_rank'
    (0-based). Each slot dict must have 'id', 'sort_order', 'windows'."""
    active = [s for s in slots if slot_active_at(s["windows"], weekday, minute)]
    active.sort(key=lambda s: (restrictiveness_score(s["windows"]),
                               s.get("sort_order", 0), s["id"]))
    for rank, s in enumerate(active):
        s["restrictiveness_rank"] = rank
    return active
