import json

import models


def test_load_game_traits_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "game_traits.default.json"
    default.write_text(json.dumps({"celeste": {"session_length": "short"}}), encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", default)
    assert models.load_game_traits() == {"celeste": {"session_length": "short"}}


def test_load_game_traits_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "game_traits.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "game_traits.json"
    per_user.write_text(json.dumps({"celeste": {"series_role": "mainline"}}), encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", per_user)
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", tmp_path / "game_traits.default.json")
    assert models.load_game_traits() == {"celeste": {"series_role": "mainline"}}


def test_load_game_traits_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_game_traits() == {}


def test_load_game_traits_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "game_traits.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", bad)
    assert models.load_game_traits() == {}
