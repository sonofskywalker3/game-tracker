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
import threading
from datetime import datetime
from pathlib import Path

import models

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
