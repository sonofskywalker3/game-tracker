"""
Configuration management for Game Tracker.
Stores API credentials and other settings in a JSON file.
"""
import json
import os
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "twitch_client_id": "",
    "twitch_client_secret": "",
    "steam_api_key": "",
    "steam_id": "",
    "anthropic_api_key": "",
    "decider_model": "claude-sonnet-4-6",
}

# Models offered in the decider-chat model dropdown — single source of truth.
# Add a row here when a new Claude model ships. (model_id, human label)
DECIDER_MODELS = (
    ("claude-opus-4-8", "Claude Opus 4.8 — most capable"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 — balanced (default)"),
    ("claude-haiku-4-5", "Claude Haiku 4.5 — fastest"),
)


def load_config():
    """Load configuration from file."""
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
        except json.JSONDecodeError:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save configuration to file atomically (tempfile + replace), so a crash
    mid-write can never leave a truncated config.json behind."""
    current = load_config()
    current.update(config)
    fd, tmp_path = tempfile.mkstemp(
        dir=CONFIG_PATH.parent, prefix=CONFIG_PATH.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(current, indent=2))
        os.replace(tmp_path, CONFIG_PATH)
    except OSError:
        os.unlink(tmp_path)
        raise
    return current


def get_twitch_credentials():
    """Get Twitch API credentials if configured."""
    config = load_config()
    client_id = config.get("twitch_client_id", "").strip()
    client_secret = config.get("twitch_client_secret", "").strip()

    if client_id and client_secret:
        return client_id, client_secret
    return None, None


def get_steam_credentials():
    """Get Steam Web API key + SteamID64 if configured."""
    config = load_config()
    api_key = config.get("steam_api_key", "").strip()
    steam_id = config.get("steam_id", "").strip()

    if api_key and steam_id:
        return api_key, steam_id
    return None, None


def get_anthropic_config() -> tuple[str | None, str]:
    """Return (api_key_or_None, model) for the decider chat."""
    config = load_config()
    key = config.get("anthropic_api_key", "").strip()
    model = config.get("decider_model", "").strip() or DEFAULT_CONFIG["decider_model"]
    return (key or None), model
