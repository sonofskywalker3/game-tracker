"""
Configuration management for Game Tracker.
Stores API credentials and other settings in a JSON file.
"""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "twitch_client_id": "",
    "twitch_client_secret": "",
}


def load_config():
    """Load configuration from file."""
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
        except json.JSONDecodeError:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save configuration to file."""
    current = load_config()
    current.update(config)
    CONFIG_PATH.write_text(json.dumps(current, indent=2))
    return current


def get_twitch_credentials():
    """Get Twitch API credentials if configured."""
    config = load_config()
    client_id = config.get("twitch_client_id", "").strip()
    client_secret = config.get("twitch_client_secret", "").strip()

    if client_id and client_secret:
        return client_id, client_secret
    return None, None
