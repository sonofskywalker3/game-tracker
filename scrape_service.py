"""Web-driven vendor library sync: scrape -> import -> enrich DLC -> mark ownership.

A single scrape runs at a time, driven by one daemon thread that owns the whole
Playwright lifecycle. The login step parks on a threading.Event set by the web UI
(the GUI counterpart to scrape_libraries._wait_for_user's terminal input). The
browser/collect seams are injectable so the suite runs offline; the live headed
scrape is verified manually. See
docs/superpowers/specs/2026-05-25-scrape-now-button-design.md.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import models
from scrape_libraries import SCRAPERS
from scrapers.base import capturing_browser, write_scrape

logger = logging.getLogger(__name__)

VENDORS = ("playstation", "xbox", "nintendo")
# Phases during which a scrape is considered "running" (start() is rejected).
_ACTIVE = frozenset({"launching", "awaiting_login", "scraping",
                     "importing", "enriching", "matching"})

_lock = threading.Lock()
_continue = threading.Event()
_cancel = threading.Event()
_state: dict = {}


def _reset(vendor: str | None = None) -> None:
    """Reset global state to idle and clear the handshake events."""
    _continue.clear()
    _cancel.clear()
    with _lock:
        _state.clear()
        _state.update(phase="idle", vendor=vendor, message="", error=None,
                      summary={}, started_at=None, finished_at=None)


_reset()  # initialize at import


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def status() -> dict:
    """A snapshot of the current scrape state (safe to call from any thread)."""
    with _lock:
        return dict(_state)


def _is_active() -> bool:
    with _lock:
        return _state.get("phase") in _ACTIVE


def backup_db() -> str | None:
    """Copy the live DB to games.db.bak-YYYYMMDD-HHMMSS; return the path (or None
    if the DB file does not exist yet)."""
    src = Path(models.DB_PATH)
    if not src.exists():
        return None
    dst = src.with_name(f"{src.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(src, dst)
    return str(dst)


def _run_pipeline(conn: sqlite3.Connection, vendor: str, games: list) -> dict:
    """Back up the DB, then import games, enrich DLC, and mark ownership.

    `games` is a list of ScrapedGame objects (or dicts). Updates the phase as it
    goes and returns a summary dict. Fuzzy matches use the safe non-interactive
    confirmer (auto-merges only spacing/punctuation variants).
    """
    import dlc_ownership
    import import_scraped

    rows = [g if isinstance(g, dict) else asdict(g) for g in games]
    games_only = [r for r in rows if r.get("kind", "game") == "game"]
    addons = [r for r in rows if r.get("kind") == "addon"]

    _set(phase="importing", message=f"importing {len(games_only)} {vendor} games...")
    backup_path = backup_db()
    stats = import_scraped.import_games(
        conn, games_only, vendor, confirm_fn=import_scraped._safe_auto_confirm)
    conn.commit()

    _set(phase="enriching", message="enriching DLC from IGDB...")
    enrich = import_scraped.run_dlc_enrichment(conn)

    _set(phase="matching", message="matching DLC ownership...")
    report = dlc_ownership.mark_ownership(conn, addons)
    conn.commit()

    return {
        "vendor": vendor,
        "scraped": len(rows),
        "new_games": stats.new_games,
        "platform_links": stats.platform_links_added,
        "dlc_added": (enrich or {}).get("added", 0),
        "enrich_skipped": enrich is None,
        "owned_marked": report.marked,
        "held": len(report.held),
        "unmatched": len(report.unmatched),
        "backup_path": backup_path,
    }


def start(vendor: str, *, browser_factory=None, collect=None) -> tuple[bool, str]:
    """Begin a scrape in a daemon thread. Rejects if a scrape is active or the
    vendor is unknown. browser_factory/collect are test seams."""
    if vendor not in VENDORS:
        return False, f"unknown vendor: {vendor}"
    if _is_active():
        return False, "a scrape is already running"
    _reset(vendor=vendor)
    thread = threading.Thread(target=_run, args=(vendor, browser_factory, collect),
                              daemon=True)
    thread.start()
    return True, "started"


def signal_continue() -> bool:
    """Tell the runner the user has logged in and the scrape may proceed."""
    _continue.set()
    return True


def cancel() -> bool:
    """Request cancellation; the runner stops at the login wait / next boundary."""
    _cancel.set()
    return True


def _run(vendor: str, browser_factory, collect) -> None:
    """Daemon-thread body: own the browser, wait for login, scrape, run pipeline.

    Cancellation is honored during the (potentially long) login wait. Any error
    sets phase=error and is surfaced to the UI; the browser is always closed.
    """
    factory = browser_factory or capturing_browser
    collect_fn = collect or SCRAPERS[vendor].collect
    mod = SCRAPERS[vendor]
    _set(phase="launching", message=f"opening {vendor} in a browser...",
         started_at=datetime.now().isoformat())
    try:
        with factory(headless=False) as (page, captured):
            page.goto(mod.VENDOR_URL)
            _set(phase="awaiting_login",
                 message="log in and open your library, then click Continue")
            while not _continue.is_set():
                if _cancel.is_set():
                    _set(phase="cancelled", message="cancelled",
                         finished_at=datetime.now().isoformat())
                    return
                page.wait_for_timeout(300)
            if _cancel.is_set():
                _set(phase="cancelled", message="cancelled",
                     finished_at=datetime.now().isoformat())
                return
            _set(phase="scraping", message=f"scraping your {vendor} library...")
            games = collect_fn(page, captured)
        write_scrape(vendor, games)
        conn = models.get_db()
        try:
            summary = _run_pipeline(conn, vendor, games)
        finally:
            conn.close()
        _set(phase="complete", message="done", summary=summary,
             finished_at=datetime.now().isoformat())
    except Exception as exc:  # never crash Flask; surface to the UI
        logger.exception("scrape failed")
        _set(phase="error", error=str(exc), message="scrape failed",
             finished_at=datetime.now().isoformat())
