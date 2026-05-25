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
