"""HLTB search/parse with mocked HTTP — never hits the network."""
from unittest.mock import patch

import hltb


def _fake_response(payload, status=200):
    class R:
        status_code = status
        def json(self):
            return payload
        def raise_for_status(self):
            pass
    return R()


SAMPLE = {"data": [
    {"game_id": 68151, "game_name": "Hades",
     "comp_main": 21600, "comp_plus": 79200, "comp_100": 327600},
    {"game_id": 99999, "game_name": "Hades II",
     "comp_main": 0, "comp_plus": 0, "comp_100": 0},
]}


def test_parse_picks_best_title_match():
    with patch("hltb.requests.post", return_value=_fake_response(SAMPLE)):
        result = hltb.fetch_durations("Hades")
    assert result is not None
    assert result["hltb_id"] == "68151"
    assert result["hltb_main_minutes"] == 360          # 21600s / 60
    assert result["hltb_main_extra_minutes"] == 1320   # 79200s / 60
    assert result["hltb_completionist_minutes"] == 5460


def test_no_match_returns_none():
    with patch("hltb.requests.post", return_value=_fake_response({"data": []})):
        assert hltb.fetch_durations("Nonexistent Game 4791") is None


def test_zero_durations_become_none():
    payload = {"data": [{"game_id": 1, "game_name": "Z",
                         "comp_main": 0, "comp_plus": 0, "comp_100": 0}]}
    with patch("hltb.requests.post", return_value=_fake_response(payload)):
        result = hltb.fetch_durations("Z")
    assert result["hltb_id"] == "1"
    assert result["hltb_main_minutes"] is None  # 0 seconds = "unknown", not "0 hours"


def test_network_error_degrades_to_none():
    import requests
    with patch("hltb.requests.post", side_effect=requests.RequestException("boom")):
        assert hltb.fetch_durations("Hades") is None
