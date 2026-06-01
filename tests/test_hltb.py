"""HLTB search/parse with mocked HTTP — never hits the network."""
from unittest.mock import MagicMock, patch

import requests

import hltb


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload

    def raise_for_status():
        if status >= 400 and status != 403:
            raise requests.HTTPError(f"{status}")

    r.raise_for_status.side_effect = raise_for_status
    return r


def _session(get_returns, post_returns):
    s = MagicMock()
    s.get.side_effect = (
        get_returns if isinstance(get_returns, list) else [get_returns] * 10
    )
    s.post.side_effect = (
        post_returns if isinstance(post_returns, list) else [post_returns] * 10
    )
    s.headers = {}
    return s


INIT = {"token": "t", "hpKey": "k", "hpVal": "v"}
SAMPLE = {
    "data": [
        {
            "game_id": 68151,
            "game_name": "Hades",
            "comp_main": 21600,
            "comp_plus": 79200,
            "comp_100": 327600,
        },
        {
            "game_id": 99999,
            "game_name": "Hades II",
            "comp_main": 0,
            "comp_plus": 0,
            "comp_100": 0,
        },
    ]
}


def test_parse_picks_best_title_match():
    with patch("hltb.requests.Session", return_value=_session(_resp(INIT), _resp(SAMPLE))):
        result = hltb.fetch_durations("Hades")
    assert result is not None
    assert result["hltb_id"] == "68151"
    assert result["hltb_main_minutes"] == 360       # 21600s / 60
    assert result["hltb_main_extra_minutes"] == 1320  # 79200s / 60
    assert result["hltb_completionist_minutes"] == 5460  # 327600s / 60


def test_no_match_returns_none():
    empty = {"data": []}
    with patch("hltb.requests.Session", return_value=_session(_resp(INIT), _resp(empty))):
        assert hltb.fetch_durations("Nonexistent Game 4791") is None


def test_zero_durations_become_none():
    payload = {"data": [{"game_id": 1, "game_name": "Z",
                          "comp_main": 0, "comp_plus": 0, "comp_100": 0}]}
    with patch("hltb.requests.Session", return_value=_session(_resp(INIT), _resp(payload))):
        result = hltb.fetch_durations("Z")
    assert result["hltb_id"] == "1"
    assert result["hltb_main_minutes"] is None  # 0 seconds = "unknown", not "0 hours"


def test_network_error_on_init_degrades_to_none():
    s = MagicMock()
    s.get.side_effect = requests.RequestException("init boom")
    s.headers = {}
    with patch("hltb.requests.Session", return_value=s):
        assert hltb.fetch_durations("Hades") is None


def test_network_error_on_search_degrades_to_none():
    s = MagicMock()
    s.get.return_value = _resp(INIT)
    s.post.side_effect = requests.RequestException("search boom")
    s.headers = {}
    with patch("hltb.requests.Session", return_value=s):
        assert hltb.fetch_durations("Hades") is None


def test_null_data_field_degrades_to_none():
    with patch(
        "hltb.requests.Session",
        return_value=_session(_resp(INIT), _resp({"data": None})),
    ):
        assert hltb.fetch_durations("Hades") is None


def test_403_triggers_reinit_and_retry():
    """First POST returns 403; second POST (after re-init) returns the real data."""
    get_responses = [_resp(INIT), _resp(INIT)]  # init called twice
    post_responses = [_resp({}, 403), _resp(SAMPLE, 200)]  # 403 then 200
    with patch(
        "hltb.requests.Session",
        return_value=_session(get_responses, post_responses),
    ):
        result = hltb.fetch_durations("Hades")
    assert result is not None
    assert result["hltb_id"] == "68151"
    assert result["hltb_main_minutes"] == 360
