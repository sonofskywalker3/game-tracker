"""_launch_context honors an explicit profile_dir (desktop-app seam)."""
from pathlib import Path

from scrapers.base import PROFILE_DIR, _launch_context


class _FakeChromium:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def launch_persistent_context(self, **kwargs) -> object:
        self.calls.append(kwargs)
        return object()


class _FakeP:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


def test_launch_context_uses_custom_profile_dir(tmp_path: Path) -> None:
    p = _FakeP()
    _launch_context(p, headless=True, profile_dir=tmp_path / "prof")
    assert p.chromium.calls[0]["user_data_dir"] == str(tmp_path / "prof")


def test_launch_context_defaults_to_module_profile_dir() -> None:
    p = _FakeP()
    _launch_context(p, headless=True)
    assert p.chromium.calls[0]["user_data_dir"] == str(PROFILE_DIR)
