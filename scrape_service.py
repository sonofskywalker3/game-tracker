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

VENDORS = ("playstation", "xbox", "nintendo", "steam")
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


def _progress(phase: str, label: str, found_word: str | None = None):
    """Return a callback(done, total=None, found=None) that updates the live status
    message the modal polls, so long loops show climbing counts."""
    def cb(done: int, total: int | None = None, found: int | None = None) -> None:
        msg = f"{label} {done}/{total}" if total else f"{label} {done}"
        if found is not None and found_word:
            msg += f" ({found} {found_word})"
        _set(phase=phase, message=msg)
    return cb


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


def _psn_addon_targets(games: list) -> list[str]:
    """PS product ids to visit: scraped ids whose game is not yet add-on-synced.

    New games aren't in the DB yet (import runs later), so they're naturally
    included -> first run backfills all, later runs only hit unsynced/new games.
    """
    from scrapers import playstation
    scraped = list(dict.fromkeys(
        g.external_id for g in games
        if g.external_id and playstation.ADDON_PID_RE.fullmatch(g.external_id)))
    conn = models.get_db()
    try:
        synced = {r[0] for r in conn.execute(
            "SELECT ge.external_id FROM game_external_ids ge "
            "JOIN games g ON g.id = ge.game_id "
            "WHERE ge.source = 'playstation' AND g.psn_addons_synced_at IS NOT NULL")}
    finally:
        conn.close()
    return [pid for pid in scraped if pid not in synced]


def _run_pipeline(conn: sqlite3.Connection, vendor: str, games: list,
                  visited_pids: list[str] | None = None) -> dict:
    """Back up the DB, import games, then populate DLC + ownership per vendor.

    Steam uses the id-based deep-fetch (steam_dlc: catalogue + appid ownership);
    other vendors use IGDB enrichment + the title-based mark_ownership. Returns a
    summary dict (counts + DLC added this run, rows flipped owned this run, and
    add-ons needing review). Fuzzy matches use the safe non-interactive confirmer.
    """
    import dlc_ownership
    import import_scraped
    import steam_dlc

    rows = [g if isinstance(g, dict) else asdict(g) for g in games]
    games_only = [r for r in rows if r.get("kind", "game") == "game"]
    addons = [r for r in rows if r.get("kind") == "addon"]

    # Timestamp (DB clock, matching dlc.created_at) to find DLC added this run.
    run_started = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]

    _set(phase="importing", message=f"importing {len(games_only)} {vendor} games...")
    backup_path = backup_db()
    stats = import_scraped.import_games(
        conn, games_only, vendor, confirm_fn=import_scraped._safe_auto_confirm,
        progress=_progress("importing", "importing games", "new"))
    conn.commit()

    if vendor == "steam":
        _set(phase="matching", message="fetching Steam DLC catalogue...")
        owned_app_ids = {int(r["external_id"]) for r in addons if r.get("external_id")}
        sr = steam_dlc.enrich_and_mark(conn, owned_app_ids,
                                       progress=_progress("matching", "fetching Steam DLC", "DLC"))
        conn.commit()
        owned_marked, created, dlc_added = sr.owned_marked, sr.catalogue_added, sr.catalogue_added
        enrich_skipped = True
        marked_dlc_ids = list(sr.marked_items)
        review = []
    else:
        _set(phase="enriching", message="enriching DLC from IGDB...")
        enrich = import_scraped.run_dlc_enrichment(conn,
                                                   progress=_progress("enriching", "enriching DLC", "added"))
        _set(phase="matching", message="matching DLC ownership...")
        report = dlc_ownership.mark_ownership(conn, addons)
        conn.commit()
        owned_marked, created = report.marked, report.created
        dlc_added = (enrich or {}).get("added", 0)
        enrich_skipped = enrich is None
        marked_dlc_ids = [m.dlc_id for m in report.marked_items]
        review = [{"title": m.addon_title, "reason": m.reason} for m in report.review]

    if visited_pids:
        placeholders = ",".join("?" for _ in visited_pids)
        conn.execute(
            f"UPDATE games SET psn_addons_synced_at = CURRENT_TIMESTAMP "
            f"WHERE id IN (SELECT game_id FROM game_external_ids "
            f"WHERE source = 'playstation' AND external_id IN ({placeholders}))",
            visited_pids)
        conn.commit()

    added_dlc = [
        {"game": r["title"], "name": r["name"], "kind": r["kind"], "owned": bool(r["owned"])}
        for r in conn.execute(
            "SELECT g.title, d.name, d.kind, d.owned FROM dlc d JOIN games g ON g.id = d.game_id "
            "WHERE d.created_at >= ? ORDER BY g.title, d.name", (run_started,))
    ]
    added_games = [
        {"id": r["id"], "title": r["title"]}
        for r in conn.execute(
            "SELECT id, title FROM games WHERE created_at >= ? ORDER BY title", (run_started,))
    ]
    newly_owned = []
    for dlc_id in marked_dlc_ids:
        row = conn.execute(
            "SELECT g.title, d.name FROM dlc d JOIN games g ON g.id = d.game_id WHERE d.id = ?",
            (dlc_id,)).fetchone()
        if row:
            newly_owned.append({"game": row["title"], "name": row["name"]})

    return {
        "vendor": vendor,
        "scraped": len(rows),
        "new_games": stats.new_games,
        "platform_links": stats.platform_links_added,
        "dlc_added": dlc_added,
        "enrich_skipped": enrich_skipped,
        "owned_marked": owned_marked,
        "created": created,
        "backup_path": backup_path,
        "added_games": added_games,
        "added_dlc": added_dlc,
        "newly_owned": newly_owned,
        "review": review,
    }


def start(vendor: str, *, browser_factory=None, collect=None,
          collect_addons=None) -> tuple[bool, str]:
    """Begin a scrape in a daemon thread. Rejects if a scrape is active or the
    vendor is unknown. browser_factory/collect/collect_addons are test seams."""
    if vendor not in VENDORS:
        return False, f"unknown vendor: {vendor}"
    if _is_active():
        return False, "a scrape is already running"
    _reset(vendor=vendor)
    thread = threading.Thread(
        target=_run, args=(vendor, browser_factory, collect, collect_addons),
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


def _run(vendor: str, browser_factory, collect, collect_addons=None) -> None:
    """Daemon-thread body: own the browser, wait for login, scrape, run pipeline.

    Cancellation is honored during the (potentially long) login wait. Any error
    sets phase=error and is surfaced to the UI; the browser is always closed.
    """
    mod = SCRAPERS[vendor]
    factory = browser_factory or capturing_browser
    collect_fn = collect or mod.collect
    visited_pids: list[str] = []
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
            if vendor == "playstation":
                addon_fn = collect_addons or mod.collect_addons
                targets = _psn_addon_targets(games)
                _set(phase="scraping",
                     message=f"checking add-ons for {len(targets)} games...")
                owned_addons, visited_pids = addon_fn(
                    page, targets, captured,
                    progress=_progress("scraping", "checking add-ons", "owned"))
                games = list(games) + owned_addons
        write_scrape(vendor, games)
        conn = models.get_db()
        try:
            summary = _run_pipeline(conn, vendor, games, visited_pids=visited_pids)
        finally:
            conn.close()
        _set(phase="complete", message="done", summary=summary,
             finished_at=datetime.now().isoformat())
    except Exception as exc:  # never crash Flask; surface to the UI
        logger.exception("scrape failed")
        _set(phase="error", error=str(exc), message="scrape failed",
             finished_at=datetime.now().isoformat())
