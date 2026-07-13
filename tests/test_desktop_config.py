"""Sidecar backlogquest.json seeds the appdata config once; appdata wins after."""
import json
from pathlib import Path

from desktop.config import SIDECAR_NAME, AppConfig, load_config, save_config


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_defaults_when_nothing_exists(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "exe", tmp_path / "data")
    assert cfg == AppConfig(server_url="https://backlogquest.xyz", token="")


def test_sidecar_seeds_appdata_on_first_run(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    _write(exe / SIDECAR_NAME, {"server_url": "https://x.example", "token": "tok1"})
    cfg = load_config(exe, data)
    assert (cfg.server_url, cfg.token) == ("https://x.example", "tok1")
    saved = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert saved["token"] == "tok1"          # persisted


def test_appdata_wins_over_sidecar_after_first_run(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    _write(exe / SIDECAR_NAME, {"server_url": "https://x.example", "token": "old"})
    _write(data / "config.json", {"server_url": "https://y.example", "token": "edited"})
    cfg = load_config(exe, data)
    assert (cfg.server_url, cfg.token) == ("https://y.example", "edited")


def test_corrupt_files_fall_back_to_defaults(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    (data).mkdir()
    (data / "config.json").write_text("{not json", encoding="utf-8")
    cfg = load_config(exe, data)
    assert cfg == AppConfig()


def test_valid_json_wrong_shape_falls_back_to_defaults(tmp_path: Path) -> None:
    exe, data = tmp_path / "exe", tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text("42", encoding="utf-8")
    assert load_config(exe, data) == AppConfig()


def test_save_round_trips(tmp_path: Path) -> None:
    save_config(AppConfig(server_url="https://z.example", token="t"), tmp_path / "d")
    cfg = load_config(tmp_path / "exe", tmp_path / "d")
    assert (cfg.server_url, cfg.token) == ("https://z.example", "t")
