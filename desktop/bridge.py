"""JS<->Python API for the pywebview window. Thin: all logic lives in
config/runner/csv_export/sync/versioncheck; this only wires and queues."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

from desktop import config as cfg
from desktop import selfupdate
from desktop.csv_export import write_vendor_csvs
from desktop.runner import ScrapeRunner
from desktop.sync import sync_payloads
from desktop.versioncheck import APP_VERSION, check_for_update

logger = logging.getLogger(__name__)

VENDOR_CHOICES: tuple[str, ...] = ("playstation", "xbox", "nintendo", "steam")

# The installer takes over via Restart Manager once launched, but pywebview's
# window.destroy() happens milliseconds later on this same code path — too
# fast for the JS poll loop (500ms interval) to ever paint update_installing.
# This linger gives the UI one guaranteed poll to show it before we tear down.
_INSTALLING_LINGER_S = 1.5

RunnerFactory = Callable[[list[str], Callable[[dict], None]], ScrapeRunner]


class Api:
    def __init__(self, data_dir: Path | None = None, exe_dir: Path | None = None,
                 runner_factory: RunnerFactory | None = None) -> None:
        self._data_dir = data_dir or cfg.appdata_dir()
        self._exe_dir = exe_dir or Path(__file__).resolve().parent
        self._runner_factory = runner_factory or self._default_runner
        self._config = cfg.load_config(self._exe_dir, self._data_dir)
        self._events: queue.Queue[dict] = queue.Queue()
        self._runner: ScrapeRunner | None = None
        self._thread: threading.Thread | None = None
        self._payload_paths: dict[str, str] = {}
        self._sync_thread: threading.Thread | None = None
        self._update_thread: threading.Thread | None = None
        # Set by main.py (needed for the folder dialog). MUST stay underscore-
        # private: pywebview serializes the js_api object's public attributes,
        # and a Window here sends it into window.native.AccessibilityObject.
        # Bounds.Empty.Empty... infinite recursion the moment any accessibility
        # client (OBS capture, PowerToys, Narrator) probes the window — wedging
        # the UI thread. See r0x0r/pywebview#1815.
        self._window = None

    def _default_runner(self, vendors: list[str], sink: Callable[[dict], None]) -> ScrapeRunner:
        return ScrapeRunner(vendors, self._data_dir / "scraped",
                            self._data_dir / "pw-profile", sink)

    # -- settings ---------------------------------------------------------------
    def get_state(self) -> dict:
        return {"server_url": self._config.server_url,
                "has_token": bool(self._config.token),
                "vendors": list(VENDOR_CHOICES),
                "version": APP_VERSION,
                "update": check_for_update(self._config.server_url)}

    def save_settings(self, server_url: str, token: str) -> dict:
        # The token field never round-trips the real token back into the UI
        # (it's always blank), so a blank submission means "unchanged", not
        # "clear it" -- keep the existing token in that case. This means v1
        # has no way to intentionally clear a saved token.
        new_token = token.strip() or self._config.token
        self._config = cfg.AppConfig(server_url=server_url.strip() or cfg.DEFAULT_SERVER_URL,
                                     token=new_token)
        cfg.save_config(self._config, self._data_dir)
        return self.get_state()

    # -- scraping ---------------------------------------------------------------
    def start_scrape(self, vendors: list[str]) -> None:
        # Re-entrancy guard: the GUI also disables the Start button while a
        # scrape is running, but double-clicks (or a slow disable) could
        # still reach here before the button updates, so guard here too.
        if self._thread is not None and self._thread.is_alive():
            return
        chosen = [v for v in VENDOR_CHOICES if v in vendors]
        self._runner = self._runner_factory(chosen, self._on_event)
        self._thread = threading.Thread(target=self._runner.run, daemon=True)
        self._thread.start()

    def _on_event(self, event: dict) -> None:
        if event["type"] == "finished":
            self._payload_paths = event["results"]
            self._events.put(event)
            # Sync is automatic whenever a token is configured; runs on its own
            # thread (server-side import can take minutes) and reports back
            # through the same event queue the UI already polls.
            if self._config.token and self._payload_paths:
                self._events.put({"type": "syncing"})
                self._sync_thread = threading.Thread(target=self._auto_sync, daemon=True)
                self._sync_thread.start()
            return
        self._events.put(event)

    def _auto_sync(self) -> None:
        self._events.put({"type": "synced", "results": self.sync()})

    def continue_login(self) -> None:
        if self._runner:
            self._runner.continue_login()

    def skip_vendor(self) -> None:
        if self._runner:
            self._runner.skip_vendor()

    def poll(self) -> dict:
        events = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        captured = self._runner.captured_count() if self._runner else 0
        return {"events": events, "captured": captured}

    # -- results ----------------------------------------------------------------
    def _payloads(self) -> list[dict]:
        return [json.loads(Path(p).read_text(encoding="utf-8"))
                for p in self._payload_paths.values()]

    def export_csv(self) -> str | None:
        import webview
        paths = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not paths:
            return None
        folder = Path(paths if isinstance(paths, str) else paths[0])
        counts = write_vendor_csvs(self._payloads(), folder)
        logger.info("exported %s to %s", counts, folder)
        return str(folder)

    def sync(self) -> list[dict]:
        results = sync_payloads(self._payloads(), self._config.server_url, self._config.token)
        return [{"source": r.source, "ok": r.ok, "summary": r.summary,
                 "retryable": r.retryable} for r in results]

    # -- self-update --------------------------------------------------------------
    def start_update(self) -> None:
        # Same double-click guard rationale as start_scrape.
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        # A scrape holds an open Playwright/browser session; racing an update
        # (which tears the window down) against it would destroy the scrape.
        if self._thread is not None and self._thread.is_alive():
            self._events.put({"type": "update_failed",
                              "error": "a scrape is running — update after it finishes"})
            return
        self._update_thread = threading.Thread(target=self._do_update, daemon=True)
        self._update_thread.start()

    def _do_update(self) -> None:
        try:
            version = check_for_update(self._config.server_url) or APP_VERSION
            path = selfupdate.download_installer(
                self._config.server_url, version,
                progress=lambda done, total: self._events.put(
                    {"type": "update_progress", "done": done, "total": total}))
            self._events.put({"type": "update_installing"})
            selfupdate.launch_installer(path)
            time.sleep(_INSTALLING_LINGER_S)   # let the JS poll loop paint "Installing"
            if self._window is not None:
                self._window.destroy()   # installer takes over and relaunches us
        except Exception as exc:   # any failure must leave the running app usable
            logger.exception("self-update failed")
            self._events.put({"type": "update_failed", "error": str(exc)})
