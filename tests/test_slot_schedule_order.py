"""slot_schedule.restrictiveness_score + order_active (pure, no DB)."""
import math

import slot_schedule as ss

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def w(days, start, end):
    return {"days": days, "start_min": start, "end_min": end}


def test_zero_windows_scores_infinite():
    assert ss.restrictiveness_score([]) == math.inf


def test_score_is_weekly_active_minutes():
    # Mon+Wed 12:00-13:00 = 2 days * 60 min = 120
    assert ss.restrictiveness_score([w((1 << MON) | (1 << WED), 720, 780)]) == 120.0
    # crossing Sat 22:00->01:00 on 1 day = (1440-1320)+60 = 180
    assert ss.restrictiveness_score([w(1 << SAT, 1320, 60)]) == 180.0


def test_order_active_filters_and_sorts_most_restrictive_first():
    narrow = {"id": 1, "sort_order": 5, "windows": [w(1 << THU, 1200, 1380)]}   # Thu 20:00-23:00
    anytime = {"id": 2, "sort_order": 0, "windows": []}                        # anytime
    evening = {"id": 3, "sort_order": 1, "windows": [w(0b1111111, 1200, 1380)]}  # daily 20-23
    # Now: Thursday 21:00 (minute 1260) — all three are active
    out = ss.order_active([anytime, evening, narrow], THU, 1260)
    assert [s["id"] for s in out] == [1, 3, 2]   # narrow < evening < anytime
    assert [s["restrictiveness_rank"] for s in out] == [0, 1, 2]


def test_order_active_excludes_inactive():
    lunch = {"id": 1, "sort_order": 0, "windows": [w(1 << MON, 720, 780)]}
    out = ss.order_active([lunch], MON, 60)   # 01:00, outside the window
    assert out == []


def test_order_active_tie_breaks_on_sort_order():
    a = {"id": 1, "sort_order": 9, "windows": [w(1 << MON, 0, 60)]}
    b = {"id": 2, "sort_order": 2, "windows": [w(1 << MON, 600, 660)]}  # same score (60 min)
    out = ss.order_active([a, b], MON, 30)  # only 'a' active at 00:30
    assert [s["id"] for s in out] == [1]
    out2 = ss.order_active([a, b], MON, 30)
    # sanity: ranks reset each call
    assert out2[0]["restrictiveness_rank"] == 0
