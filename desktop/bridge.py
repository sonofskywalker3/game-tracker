"""JS<->Python API for the pywebview window. Thin: all logic lives in
config/runner/csv_export/sync/versioncheck; this only wires and queues."""
from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path

from desktop import config as cfg
from desktop.csv_export import write_csv
from desktop.runner import ScrapeRunner
from desktop.sync import sync_payloads
from desktop.versioncheck import APP_VERSION, check_for_update

logger = logging.getLogger(__name__)

VENDOR_CHOICES: tuple[str, ...] = ("playstation", "xbox", "nintendo", "steam")

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
        self.window = None          # set by main.py (needed for the save dialog)

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
        self._config = cfg.AppConfig(server_url=server_url.strip() or cfg.DEFAULT_SERVER_URL,
                                     token=token.strip())
        cfg.save_config(self._config, self._data_dir)
        return self.get_state()

    # -- scraping ---------------------------------------------------------------
    def start_scrape(self, vendors: list[str]) -> None:
        chosen = [v for v in VENDOR_CHOICES if v in vendors]
        self._runner = self._runner_factory(chosen, self._on_event)
        self._thread = threading.Thread(target=self._runner.run, daemon=True)
        self._thread.start()

    def _on_event(self, event: dict) -> None:
        if event["type"] == "finished":
            self._payload_paths = event["results"]
        self._events.put(event)

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
        paths = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="backlogquest_library.csv",
            file_types=("CSV file (*.csv)",))
        if not paths:
            return None
        out = Path(paths if isinstance(paths, str) else paths[0])
        rows = write_csv(self._payloads(), out)
        logger.info("exported %d rows to %s", rows, out)
        return str(out)

    def sync(self) -> list[dict]:
        results = sync_payloads(self._payloads(), self._config.server_url, self._config.token)
        return [{"source": r.source, "ok": r.ok, "summary": r.summary,
                 "retryable": r.retryable} for r in results]
