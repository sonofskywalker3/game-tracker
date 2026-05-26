import json

import config


def test_get_steam_credentials_present(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"steam_api_key": "KEY", "steam_id": "76561190000000000"}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    assert config.get_steam_credentials() == ("KEY", "76561190000000000")


def test_get_steam_credentials_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nope.json")
    assert config.get_steam_credentials() == (None, None)


def test_get_steam_credentials_blank(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"steam_api_key": "  ", "steam_id": ""}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    assert config.get_steam_credentials() == (None, None)
