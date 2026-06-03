import config


def test_default_config_has_anthropic_keys():
    assert "anthropic_api_key" in config.DEFAULT_CONFIG
    assert config.DEFAULT_CONFIG["decider_model"] == "claude-sonnet-4-6"


def test_get_anthropic_config_defaults(monkeypatch):
    monkeypatch.setattr(config, "load_config",
                        lambda: {"anthropic_api_key": "", "decider_model": "claude-sonnet-4-6"})
    key, model = config.get_anthropic_config()
    assert key is None and model == "claude-sonnet-4-6"


def test_get_anthropic_config_returns_key(monkeypatch):
    monkeypatch.setattr(config, "load_config",
                        lambda: {"anthropic_api_key": " sk-test ", "decider_model": "m"})
    key, model = config.get_anthropic_config()
    assert key == "sk-test" and model == "m"
