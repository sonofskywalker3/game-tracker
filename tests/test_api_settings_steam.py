"""Steam credentials in the Settings API (masked read-back, mask-safe save),
plus atomic config writes."""
import json

import config as config_module


def _isolate_config(tmp_path, monkeypatch, initial=None):
    path = tmp_path / "config.json"
    if initial is not None:
        path.write_text(json.dumps(initial))
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    return path


def test_settings_get_masks_steam_key(client, temp_db, tmp_path, monkeypatch):
    _isolate_config(tmp_path, monkeypatch,
                    {"steam_api_key": "SECRETKEY", "steam_id": "76561198000000000"})
    data = client.get("/api/settings").get_json()
    assert data["steam_api_key"] == "••••••••"
    assert data["steam_id"] == "76561198000000000"   # SteamID64 is not a secret
    assert data["has_steam_credentials"] is True


def test_settings_get_empty_steam(client, temp_db, tmp_path, monkeypatch):
    _isolate_config(tmp_path, monkeypatch)
    data = client.get("/api/settings").get_json()
    assert data["steam_api_key"] == ""
    assert data["steam_id"] == ""
    assert data["has_steam_credentials"] is False


def test_settings_put_saves_steam_and_ignores_mask(client, temp_db, tmp_path, monkeypatch):
    path = _isolate_config(tmp_path, monkeypatch,
                           {"steam_api_key": "REALKEY", "steam_id": "111"})
    resp = client.put("/api/settings", json={
        "steam_api_key": "••••••••",    # untouched mask must not clobber
        "steam_id": "222",
    })
    assert resp.status_code == 200
    saved = json.loads(path.read_text())
    assert saved["steam_api_key"] == "REALKEY"
    assert saved["steam_id"] == "222"

    client.put("/api/settings", json={"steam_api_key": "NEWKEY"})
    saved = json.loads(path.read_text())
    assert saved["steam_api_key"] == "NEWKEY"


def test_save_config_is_atomic(tmp_path, monkeypatch):
    """save_config writes via tempfile+replace so a crash can't leave a
    half-written config.json."""
    path = _isolate_config(tmp_path, monkeypatch, {"steam_id": "1"})

    real_replace = config_module.os.replace
    calls = []

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(config_module.os, "replace", spy_replace)
    config_module.save_config({"steam_id": "2"})
    assert calls, "save_config must write via os.replace (atomic swap)"
    assert json.loads(path.read_text())["steam_id"] == "2"
