# tests/test_slot_signals.py
"""Pure signal-derivation lookups (no DB)."""
from slot_signals import (
    session_tolerant, latency_tolerant, effective_time_to_beat_minutes,
)


def test_short_session_genres_are_tolerant():
    assert session_tolerant({"Roguelike"}) is True
    assert session_tolerant({"Puzzle", "Indie"}) is True


def test_long_form_genres_are_not_tolerant():
    assert session_tolerant({"Open World"}) is False
    assert session_tolerant({"JRPG"}) is False


def test_unknown_genre_defaults_to_tolerant():
    # No evidence it's long-form -> don't exclude it from short slots.
    assert session_tolerant(set()) is True
    assert session_tolerant({"Totally Made Up Genre"}) is True


def test_latency_sensitive_genres_not_tolerant():
    assert latency_tolerant({"Fighting"}, None) is False
    assert latency_tolerant({"Shooter"}, None) is False


def test_latency_tolerant_genres():
    assert latency_tolerant({"Strategy"}, None) is True
    assert latency_tolerant({"JRPG"}, None) is True


def test_latency_override_wins():
    # override 1 = force tolerant, 0 = force not, regardless of tags
    assert latency_tolerant({"Fighting"}, 1) is True
    assert latency_tolerant({"Strategy"}, 0) is False


def test_effective_time_to_beat_prefers_override():
    row = {"time_to_beat_override_minutes": 600, "hltb_main_minutes": 1200}
    assert effective_time_to_beat_minutes(row) == 600


def test_effective_time_to_beat_falls_back_to_hltb_main():
    row = {"time_to_beat_override_minutes": None, "hltb_main_minutes": 1200}
    assert effective_time_to_beat_minutes(row) == 1200


def test_effective_time_to_beat_none_when_unknown():
    row = {"time_to_beat_override_minutes": None, "hltb_main_minutes": None}
    assert effective_time_to_beat_minutes(row) is None
