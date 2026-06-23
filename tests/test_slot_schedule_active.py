"""slot_schedule.window_covers + slot_active_at (pure, no DB)."""
import slot_schedule as ss

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
ALL_DAYS = 0b1111111


def w(days, start, end):
    return {"days": days, "start_min": start, "end_min": end}


def test_normal_window_covers_inside_only():
    win = w(1 << TUE, 720, 780)  # Tue 12:00-13:00
    assert ss.window_covers(win, TUE, 740) is True
    assert ss.window_covers(win, TUE, 780) is False   # end is exclusive
    assert ss.window_covers(win, TUE, 719) is False
    assert ss.window_covers(win, WED, 740) is False    # wrong day


def test_midnight_crossing_window_covers_both_sides():
    win = w(1 << SAT, 1320, 60)  # Sat 22:00 -> Sun 01:00
    assert ss.window_covers(win, SAT, 1380) is True    # Sat 23:00
    assert ss.window_covers(win, SUN, 30) is True       # Sun 00:30 (next day)
    assert ss.window_covers(win, SUN, 90) is False      # Sun 01:30 (past end)
    assert ss.window_covers(win, SAT, 1200) is False    # Sat 20:00 (before start)
    assert ss.window_covers(win, FRI, 30) is False      # Fri not the day-after a set day


def test_degenerate_window_never_covers():
    assert ss.window_covers(w(ALL_DAYS, 600, 600), MON, 600) is False


def test_zero_windows_is_always_active():
    assert ss.slot_active_at([], MON, 0) is True
    assert ss.slot_active_at([], SUN, 1439) is True


def test_slot_active_if_any_window_matches():
    windows = [w(1 << MON, 0, 60), w(1 << WED, 720, 780)]
    assert ss.slot_active_at(windows, WED, 740) is True
    assert ss.slot_active_at(windows, TUE, 740) is False
