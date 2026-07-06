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
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import models
from scrape_libraries import SCRAPERS
from scrapers.base import capturing_browser, write_scrape

logger = logging.getLogger(__name__)

VENDORS = ("playstation", "xbox", "nintendo", "steam")
# Vendor -> platform label for parent games created from a vendor catalogue.
_PLATFORM_BY_VENDOR = {"xbox": "Xbox", "nintendo": "Switch", "playstation": "PS4"}
# Navigation budget. Vendor account pages are heavy SPAs that can take well over
# the Playwright 30s default to fire "load"; we wait only for "domcontentloaded"
# (the auth cookies/DOM are present by then -- the scrapers replay the data APIs
# themselves) and allow a generous timeout so a slow login page never aborts.
GOTO_TIMEOUT_MS = 60_000
GOTO_WAIT_UNTIL = "domcontentloaded"
# Phases during which a scrape is considered "running" (start() is rejected).
_ACTIVE = frozenset({"launching", "awaiting_login", "scraping",
                     "importing", "enriching", "matching"})

_lock = threading.Lock()
_continue = threading.Event()
_cancel = threading.Event()
_state: dict = {}


def _reset_state_locked(vendor: str | None = None, phase: str = "idle") -> None:
    """Reinitialize _state in place. Caller must hold _lock."""
    _state.clear()
    _state.update(phase=phase, vendor=vendor, message="", error=None,
                  summary={}, started_at=None, finished_at=None)


def _reset(vendor: str | None = None) -> None:
    """Reset global state to idle and clear the handshake events."""
    _continue.clear()
    _cancel.clear()
    with _lock:
        _reset_state_locked(vendor)


_reset()  # initialize at import


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def _progress(phase: str, label: str,
              found_word: str | None = None) -> Callable[[int, int | None, int | None], None]:
    """Return a callback(done, total=None, found=None) that updates the live status
    message the modal polls, so long loops show climbing counts."""
    def cb(done: int, total: int | None = None, found: int | None = None) -> None:
        msg = f"{label} {done}/{total}" if total else f"{label} {done}"
        if found is not None and found_word:
            msg += f" ({found} {found_word})"
        _set(phase=phase, message=msg)
    return cb


_SCRAPE_UNITS = {"playstation": "games", "steam": "games",
                 "nintendo": "orders", "xbox": "pages"}


def _scrape_progress(vendor: str) -> Callable[[int], None]:
    """Return a callback(done) that ticks the base-scrape status message with the
    vendor's natural unit, so a long library fetch shows it's still working."""
    unit = _SCRAPE_UNITS.get(vendor, "items")

    def cb(done: int) -> None:
        _set(phase="scraping",
             message=f"scraping your {vendor} library — {done} {unit} so far…")
    return cb


def _force_foreground_windows(title: str) -> None:
    """Find the Chromium window matching `title` and flash + foreground it (Windows).

    The taskbar flash is the reliable, OS-sanctioned attention signal; the
    foreground-force (AttachThreadInput trick) works on many setups but Windows
    intentionally makes it unreliable. Only acts on a window whose title contains
    the page title, to avoid grabbing an unrelated Chrome window.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    needle = title[:12].lower()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "Chrome_WidgetWin_1":
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if needle and needle in (buf.value or "").lower():
            found.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    if not found:
        return
    hwnd = found[0]

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD)]

    for fn, args in (
        (user32.ShowWindow, [wintypes.HWND, ctypes.c_int]),
        (user32.BringWindowToTop, [wintypes.HWND]),
        (user32.SetForegroundWindow, [wintypes.HWND]),
        (user32.FlashWindowEx, [ctypes.POINTER(FLASHWINFO)]),
        (user32.GetWindowThreadProcessId, [wintypes.HWND, wintypes.LPVOID]),
        (user32.SetWindowPos, [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]),
    ):
        fn.argtypes = args

    flashw_all, flashw_timernofg = 0x3, 0xC
    fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                    flashw_all | flashw_timernofg, 5, 0)
    user32.FlashWindowEx(ctypes.byref(fi))

    sw_restore = 9
    user32.ShowWindow(hwnd, sw_restore)
    # Z-order raise that survives Windows' focus-steal block: briefly mark the
    # window TOPMOST (jumps above all normal windows) then drop the flag, so it
    # ends up on top without permanently staying always-on-top. This fixes the
    # "login window opened behind my other windows" case even when the
    # foreground-force below is denied.
    hwnd_topmost, hwnd_notopmost = -1, -2
    swp_raise = 0x2 | 0x1 | 0x40  # SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, swp_raise)
    user32.SetWindowPos(hwnd, hwnd_notopmost, 0, 0, 0, 0, swp_raise)
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    our_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(our_tid, fg_tid, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(our_tid, fg_tid, False)


def _surface_login_window(page) -> None:
    """Best-effort: bring the login browser to the foreground so it's not missed.

    bring_to_front() activates the tab; on Windows we additionally flash the
    taskbar and attempt a foreground-force. Never raises; off Windows (or with no
    real page, e.g. tests) it just activates the tab.
    """
    try:
        page.bring_to_front()
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        title = (page.title() or "").strip()
    except Exception:
        return  # no real page (e.g. test fake) -> skip OS-level surfacing
    if not title:
        return
    try:
        _force_foreground_windows(title)
    except Exception:
        pass


def status() -> dict:
    """A snapshot of the current scrape state (safe to call from any thread)."""
    with _lock:
        return dict(_state)


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
                  visited_pids: list[str] | None = None,
                  parent_map: dict | None = None) -> dict:
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

    link = None  # set only when a vendor catalogue resolver runs (non-Steam path)
    if vendor == "steam":
        _set(phase="matching", message="fetching Steam DLC catalogue...")
        owned_app_ids = {int(r["external_id"]) for r in addons if r.get("external_id")}
        sr = steam_dlc.enrich_and_mark(conn, owned_app_ids,
                                       progress=_progress("matching", "fetching Steam DLC", "added"))
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
        import addon_parent
        platform = _PLATFORM_BY_VENDOR.get(vendor, vendor.title())
        if parent_map is not None:
            # Nintendo: parent links discovered live during the per-game DLC pass
            # (see _run); feed them through the same resolve_and_link machinery.
            def resolver(ids):
                return {i: parent_map.get(i) for i in ids}
        else:
            resolver = addon_parent.RESOLVERS.get(vendor)
        if resolver and addons:
            link = addon_parent.resolve_and_link(conn, vendor, platform, addons, resolver)
            conn.commit()
            remaining = link.unresolved
        else:
            remaining = addons
        report = dlc_ownership.mark_ownership(conn, remaining)
        conn.commit()
        owned_marked, created = report.marked, report.created
        marked_dlc_ids = [m.dlc_id for m in report.marked_items]
        if link is not None:
            owned_marked += link.linked
            marked_dlc_ids += [m.dlc_id for m in link.linked_items]
        dlc_added = (enrich or {}).get("added", 0)
        enrich_skipped = enrich is None
        review = [{"title": m.addon_title, "reason": m.reason} for m in report.review]

    # Collections sync (SP-A Stage 1): shared, vendor-agnostic polish so scraped
    # games get IGDB collection memberships without the manual backfill button.
    # Same batched code path as Settings > backfill; best-effort — a resolve
    # failure must never sink a finished scrape.
    collections_synced = None
    try:
        import config
        import igdb_dlc
        import igdb_resolve
        cid, secret = config.get_twitch_credentials()
        if cid:
            _set(phase="enriching", message="syncing collections...")
            token = igdb_dlc.get_access_token(cid, secret)
            creport = igdb_resolve.backfill_collections(
                conn, cid, token,
                progress=_progress("enriching", "syncing collections"))
            collections_synced = creport["games"]
    except Exception as exc:
        logger.warning("collections sync failed (%s); scrape result kept", exc)

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

    # SP-C nudge: games (library-wide) still lacking a session length — the
    # completion modal offers the opt-in AI classification when > 0.
    session_unclassified = conn.execute(
        "SELECT COUNT(*) FROM games WHERE session_length IS NULL").fetchone()[0]

    return {
        "vendor": vendor,
        "scraped": len(rows),
        "session_unclassified": session_unclassified,
        # + parent games auto-created from the vendor catalogue
        "new_games": stats.new_games + (link.created_parents if link else 0),
        "platform_links": stats.platform_links_added,
        "dlc_added": dlc_added,
        "enrich_skipped": enrich_skipped,
        "owned_marked": owned_marked,
        "created": created,
        "collections_synced": collections_synced,
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
    # Guard + state transition under ONE lock acquisition: the phase flips to
    # "launching" before the lock is released, so a near-simultaneous second
    # start() can never also pass the active check and spawn a duplicate runner.
    with _lock:
        if _state.get("phase") in _ACTIVE:
            return False, "a scrape is already running"
        _reset_state_locked(vendor, phase="launching")
    _continue.clear()
    _cancel.clear()
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
    """Daemon-thread body: log in (visible), then scrape headless, run pipeline.

    The browser is VISIBLE for the login step (surfaced to the foreground); once
    the user clicks Continue it is closed and a fresh context is opened on the same
    persistent profile (.pw-profile carries the auth) for the long page-pulling work
    (library + per-game store pages). That context is HEADLESS/off-screen by default,
    but a vendor whose account site blocks headless (COLLECT_HEADLESS=False, e.g.
    xbox) reopens a real headful window instead. Cancellation is honored during the
    login wait. Any error sets phase=error and is surfaced to the UI; browsers are
    always closed.
    """
    mod = SCRAPERS[vendor]
    factory = browser_factory or capturing_browser
    collect_fn = collect or mod.collect
    visited_pids: list[str] = []
    parent_map: dict | None = None
    _set(phase="launching", message=f"opening {vendor} in a browser...",
         started_at=datetime.now().isoformat())
    try:
        # Phase 1 — visible browser, for login only. Closed before the long
        # page-pulling so that work runs headless and off-screen.
        with factory(headless=False) as (page, _captured):
            page.set_default_navigation_timeout(GOTO_TIMEOUT_MS)
            page.goto(mod.VENDOR_URL, wait_until=GOTO_WAIT_UNTIL, timeout=GOTO_TIMEOUT_MS)
            _surface_login_window(page)  # flash taskbar + raise to foreground (Windows)
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
        # Login window closed; the saved session carries the auth into a fresh
        # context for the rest of the run. Headless (off-screen) by default, but a
        # vendor whose account site blocks headless (e.g. xbox) collects in a real
        # headful window instead -- COLLECT_HEADLESS=False opts out.
        collect_headless = getattr(mod, "COLLECT_HEADLESS", True)
        _set(phase="scraping", message=f"scraping your {vendor} library...")
        with factory(headless=collect_headless) as (page, captured):
            page.set_default_navigation_timeout(GOTO_TIMEOUT_MS)
            page.goto(mod.VENDOR_URL, wait_until=GOTO_WAIT_UNTIL, timeout=GOTO_TIMEOUT_MS)
            games = collect_fn(page, captured, progress=_scrape_progress(vendor))
            if vendor == "playstation":
                addon_fn = collect_addons or mod.collect_addons
                targets = _psn_addon_targets(games)
                _set(phase="scraping",
                     message=f"checking add-ons for {len(targets)} games...")
                try:
                    owned_addons, visited_pids, addon_parents = addon_fn(
                        page, targets, captured,
                        progress=_progress("scraping", "checking add-ons", "owned"),
                        should_cancel=_cancel.is_set)
                except Exception as exc:  # never sink the scrape; keep the base library
                    logger.warning("playstation: add-on pass failed (%s); "
                                   "importing base library only", exc)
                else:
                    games = list(games) + owned_addons
                    # Parent-down: link each add-on to the GAME whose page surfaced it
                    # (holds across PS4/PS5), via the same resolver path Nintendo uses.
                    from addon_parent import ParentRef
                    parent_map = {aid: ParentRef(product_id=gid)
                                  for aid, gid in addon_parents.items()}
            elif vendor == "nintendo":
                from scrapers import nintendo_catalog
                # Each owned add-on's own eShop page names its required base game
                # ("requires this game to play"); read that to link the add-on,
                # preferring a base the user already owns for cross-series packs.
                owned_game_nsuids = {g.external_id for g in games
                                     if getattr(g, "kind", "game") == "game" and g.external_id}
                addon_items = [(g.external_id, getattr(g, "url_key", None))
                               for g in games
                               if getattr(g, "kind", "game") == "addon" and g.external_id]
                _set(phase="scraping",
                     message=f"reading DLC parents for {len(addon_items)} add-ons...")
                try:
                    parent_map = nintendo_catalog.collect_addon_parents(
                        page, captured, addon_items,
                        owned_game_nsuids=owned_game_nsuids,
                        progress=_progress("scraping", "reading DLC", "linked"),
                        should_cancel=_cancel.is_set)
                except Exception as exc:  # never sink the scrape; fall back to name match
                    logger.warning("nintendo: DLC pass failed (%s); using name fallback", exc)
                    parent_map = None
        write_scrape(vendor, games)
        conn = models.get_db()
        try:
            summary = _run_pipeline(conn, vendor, games, visited_pids=visited_pids,
                                    parent_map=parent_map)
        finally:
            conn.close()
        _set(phase="complete", message="done", summary=summary,
             finished_at=datetime.now().isoformat())
    except Exception as exc:  # never crash Flask; surface to the UI
        logger.exception("scrape failed")
        _set(phase="error", error=str(exc), message="scrape failed",
             finished_at=datetime.now().isoformat())
