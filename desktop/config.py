"""App settings: a sidecar backlogquest.json (personalized download) seeds the
persisted %APPDATA% config on first run; user edits in the app win thereafter."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_NAME = "backlogquest.json"
_CONFIG_NAME = "config.json"
DEFAULT_SERVER_URL = "https://backlogquest.xyz"


@dataclass
class AppConfig:
    server_url: str = DEFAULT_SERVER_URL
    token: str = ""


def appdata_dir() -> Path:
    """Per-user data root (profile, scrapes, config, log)."""
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "BacklogQuest"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("unreadable config %s: %s", path, exc)
        return None


def _from_dict(data: dict) -> AppConfig:
    return AppConfig(
        server_url=str(data.get("server_url") or DEFAULT_SERVER_URL),
        token=str(data.get("token") or ""),
    )


def save_config(cfg: AppConfig, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _CONFIG_NAME).write_text(json.dumps(asdict(cfg)), encoding="utf-8")


def load_config(exe_dir: Path, data_dir: Path) -> AppConfig:
    """Persisted config wins; a sidecar next to the exe seeds it exactly once."""
    persisted = _read_json(data_dir / _CONFIG_NAME)
    if persisted is not None:
        return _from_dict(persisted)
    sidecar = _read_json(exe_dir / SIDECAR_NAME)
    if sidecar is not None:
        cfg = _from_dict(sidecar)
        save_config(cfg, data_dir)
        return cfg
    return AppConfig()
