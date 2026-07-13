"""BacklogQuest Scraper entry point (dev: `uv run python -m desktop.main`)."""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys
from pathlib import Path

from desktop.bridge import Api
from desktop.config import appdata_dir

_BG = "#181A22"
_WEBVIEW2_HELP = ("The app needs Microsoft WebView2 (normally preinstalled on "
                  "Windows 10/11).\nInstall it from:\n"
                  "https://developer.microsoft.com/microsoft-edge/webview2/")


def _setup_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        data_dir / "scraper.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _exe_dir() -> Path:
    """Directory the .exe itself lives in (where a user drops backlogquest.json)."""
    if getattr(sys, "frozen", False):          # PyInstaller onedir
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_dir() -> Path:
    """Bundled read-only resources (ui/). PyInstaller 6.x onedir builds unpack
    datas into a `_internal` subfolder (sys._MEIPASS), not next to the exe."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _exe_dir()))
    return Path(__file__).resolve().parent


def main() -> None:
    data_dir = appdata_dir()
    _setup_logging(data_dir)
    import webview
    api = Api(data_dir=data_dir, exe_dir=_exe_dir())
    ui = _resource_dir() / "ui" / "index.html"
    # Private attr on purpose — a public one gets serialized into the JS bridge
    # and recurses through AccessibilityObject (see bridge.py / pywebview#1815).
    api._window = webview.create_window(
        "BacklogQuest Scraper", url=str(ui), js_api=api,
        width=560, height=680, background_color=_BG,
        text_select=True)   # pywebview defaults to no text selection anywhere
    try:
        webview.start()
    except Exception:
        logging.getLogger(__name__).exception("webview failed to start")
        ctypes.windll.user32.MessageBoxW(None, _WEBVIEW2_HELP, "BacklogQuest Scraper", 0x10)


if __name__ == "__main__":
    main()
