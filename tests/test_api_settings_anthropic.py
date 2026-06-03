import app as app_module
import config


def _client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_get_settings_masks_key(monkeypatch):
    monkeypatch.setattr(app_module, "load_config",
                        lambda: {"anthropic_api_key": "sk-secret", "decider_model": "claude-sonnet-4-6"})
    data = _client().get("/api/settings").get_json()
    assert data["anthropic_api_key"] == "••••••••"
    assert data["decider_model"] == "claude-sonnet-4-6"
    assert data["has_anthropic_key"] is True


def test_put_settings_saves_key_and_model(monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, "save_config", lambda u: saved.update(u))
    monkeypatch.setattr(app_module, "load_config", lambda: config.DEFAULT_CONFIG.copy())
    _client().put("/api/settings", json={"anthropic_api_key": "sk-new",
                                         "decider_model": "claude-sonnet-4-6"})
    assert saved["anthropic_api_key"] == "sk-new"
    assert saved["decider_model"] == "claude-sonnet-4-6"


def test_put_settings_ignores_masked_key(monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, "save_config", lambda u: saved.update(u))
    monkeypatch.setattr(app_module, "load_config", lambda: config.DEFAULT_CONFIG.copy())
    _client().put("/api/settings", json={"anthropic_api_key": "••••••••"})
    assert "anthropic_api_key" not in saved
