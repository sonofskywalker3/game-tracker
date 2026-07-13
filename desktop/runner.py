"""Sequential vendor scrape driver for the GUI: login-wait -> collect -> write.

Runs on a worker thread (Playwright sync API must stay off the GUI thread).
The GUI's Continue/Skip buttons set threading.Events consumed once per vendor.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_PUMP_MS = 300

EventSink = Callable[[dict], None]


class ScrapeRunner:
    def __init__(self, vendors: list[str], out_dir: Path, profile_dir: Path,
                 sink: EventSink, modules: dict | None = None,
                 browser_factory=None, write=None) -> None:
        # Late imports keep this module import-safe for tests without Playwright.
        if modules is None:
            from scrape_libraries import SCRAPERS
            modules = SCRAPERS
        if browser_factory is None:
            from scrapers.base import capturing_browser
            browser_factory = capturing_browser
        if write is None:
            from scrapers.base import write_scrape
            write = write_scrape
        self._vendors, self._modules = vendors, modules
        self._out_dir, self._profile_dir = out_dir, profile_dir
        self._sink, self._browser_factory, self._write = sink, browser_factory, write
        self._continue = threading.Event()
        self._skip = threading.Event()
        self._captured: list = []
        self._auto_continue = False

    # -- thread-safe controls (called from the GUI thread) --------------------
    def continue_login(self) -> None:
        self._continue.set()

    def skip_vendor(self) -> None:
        self._skip.set()

    def captured_count(self) -> int:
        return len(self._captured)

    # -- worker-thread body ----------------------------------------------------
    def _wait_for_continue(self, page) -> bool:
        """Pump the browser until Continue (True) or Skip (False).

        Each event is consumed (cleared) by the vendor that acts on it, so a
        Continue queued for a later vendor survives an earlier vendor's Skip.
        """
        while True:
            if self._skip.is_set():
                self._skip.clear()
                return False
            if self._auto_continue:
                return True
            if self._continue.is_set():
                self._continue.clear()
                return True
            page.wait_for_timeout(_PUMP_MS)

    def _scrape_one(self, vendor: str) -> Path | None:
        mod = self._modules[vendor]
        self._sink({"type": "login", "vendor": vendor, "label": mod.SOURCE})
        with self._browser_factory(headless=False, profile_dir=self._profile_dir) as (page, captured):
            self._captured = captured
            page.goto(mod.VENDOR_URL)
            if not self._wait_for_continue(page):
                self._sink({"type": "skipped", "vendor": vendor, "note": "skipped"})
                return None
            self._sink({"type": "collecting", "vendor": vendor})
            games = mod.collect(page, captured)
        path = self._write(vendor, games, self._out_dir)
        self._sink({"type": "done", "vendor": vendor, "count": len(games)})
        return path

    def run(self) -> dict[str, Path]:
        results: dict[str, Path] = {}
        for vendor in self._vendors:
            try:
                path = self._scrape_one(vendor)
            except Exception as exc:   # vendor page changed, browser closed, ...
                logger.exception("vendor %s failed", vendor)
                self._sink({"type": "skipped", "vendor": vendor, "note": str(exc)})
                continue
            if path is not None:
                results[vendor] = path
        self._sink({"type": "finished", "results": {v: str(p) for v, p in results.items()}})
        return results
