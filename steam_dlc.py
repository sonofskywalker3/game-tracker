"""Steam DLC catalogue + ownership (vendor store = source of truth).

For each owned Steam game, fetch its full DLC catalogue from the keyless storefront
`appdetails` endpoint, reconcile-or-create each DLC as a dlc row (recording the
Steam appid in dlc_external_ids), and mark owned exactly those whose appid the user
owns -- a pure appid set-intersection (no name heuristics). appdetails responses are
cached on disk. Pure parsers are unit-tested; the network fetch is isolated/cached
and injected into enrich_and_mark for offline tests. See
docs/superpowers/specs/2026-05-25-dlc-steam-vendor-design.md.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests

import models
from igdb_dlc import store_genres

logger = logging.getLogger(__name__)

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
CACHE_DIR = Path(__file__).parent / ".steam_cache"
REQUEST_DELAY_S = 1.5  # keep under ~200 appdetails / 5 min per IP

# Steam store-genre description -> our canonical genre tag (slot_signals vocabulary).
# Only gameplay genres are kept; Steam's content/meta categories (Free to Play,
# Early Access, Violent, Gore, etc.) are intentionally skipped.
STEAM_GENRE_TO_TAG = {
    "Action": "Action",
    "Adventure": "Adventure",
    "RPG": "RPG",
    "Role-Playing": "RPG",
    "Strategy": "Strategy",
    "Simulation": "Simulation",
    "Racing": "Racing",
    "Sports": "Sports",
    "Indie": "Indie",
    "Casual": "Casual",
    "Massively Multiplayer": "Multiplayer",
}


def parse_genres(data: dict) -> list[str]:
    """Canonical gameplay genre tags from a Steam appdetails `data` object
    (mapped via STEAM_GENRE_TO_TAG, deduped, order kept; meta categories skipped)."""
    out: list[str] = []
    for g in (data or {}).get("genres") or []:
        tag = STEAM_GENRE_TO_TAG.get(g.get("description"))
        if tag and tag not in out:
            out.append(tag)
    return out

_STEAM_APP_URL = re.compile(
    r"https?://store\.steampowered\.com/app/(\d+)",
    re.IGNORECASE,
)


def appid_from_steam_url(url: str | None) -> int | None:
    """Extract the appid from a store.steampowered.com/app/<appid> URL, else None.

    Mirrors igdb_dlc.slug_from_igdb_url: case-insensitive, ignores trailing path
    segments and query strings, strips surrounding whitespace.
    """
    if not url:
        return None
    match = _STEAM_APP_URL.search(url.strip())
    return int(match.group(1)) if match else None


def parse_catalogue(data: dict) -> list[int]:
    """The DLC appids listed for a base game (data.dlc), as ints."""
    return [int(x) for x in (data or {}).get("dlc") or []]


def parse_appdetails_name(data: dict) -> str:
    return ((data or {}).get("name") or "").strip()


def parse_type(data: dict) -> str:
    return ((data or {}).get("type") or "").strip()


def fetch_appdetails(appid: int, *, cache_dir: Path = CACHE_DIR,
                     session=requests, delay_s: float = REQUEST_DELAY_S) -> dict | None:
    """Return the appdetails `data` object for an appid (cached on disk), or None.

    A cache hit skips the network entirely. On a miss, GET appdetails, cache the
    `data` object (an empty object for a not-found/failed app), throttle, and
    return it (None when there is no data).
    """
    cache_dir = Path(cache_dir)
    cache_file = cache_dir / f"{appid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8")) or None
    resp = session.get(APPDETAILS_URL, params={"appids": appid, "l": "english"}, timeout=30)
    resp.raise_for_status()
    entry = (resp.json() or {}).get(str(appid)) or {}
    data = entry.get("data") if entry.get("success") else None
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data or {}, ensure_ascii=False), encoding="utf-8")
    if delay_s:
        time.sleep(delay_s)
    return data


@dataclass
class SteamReport:
    """Outcome of a Steam catalogue+ownership pass."""
    games: int = 0
    catalogue_added: int = 0
    owned_marked: int = 0
    errors: int = 0
    marked_items: list[int] = field(default_factory=list)  # dlc_ids flipped owned


def _record_ext_id(conn: sqlite3.Connection, dlc_id: int, ext: str, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO dlc_external_ids (dlc_id, source, external_id, source_title) "
        "VALUES (?, 'steam', ?, ?)", (dlc_id, ext, name))


def _reconcile_or_create(conn: sqlite3.Connection, game_id: int, name: str,
                         dlc_appid: int) -> tuple[int | None, bool]:
    """Return (dlc_id, created). Reconcile by Steam appid -> by normalized-name
    equality (recording the appid) -> else create a steam-sourced row (owned=0)."""
    ext = str(dlc_appid)
    row = conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='steam' AND external_id=?",
        (ext,)).fetchone()
    if row:
        return row[0], False
    target = models.normalize_title(name)
    for r in conn.execute("SELECT id, name FROM dlc WHERE game_id=?", (game_id,)):
        if models.normalize_title(r["name"]) == target:
            _record_ext_id(conn, r["id"], ext, name)
            return r["id"], False
    try:
        cur = conn.execute(
            "INSERT INTO dlc (game_id, name, kind, owned, source) "
            "VALUES (?, ?, 'dlc', 0, 'steam')", (game_id, name))
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT id FROM dlc WHERE game_id=? AND name=?", (game_id, name)).fetchone()
        if existing is None:           # impossible in single-writer scrape; fail safe
            logger.error("steam dlc insert conflict but row not found: game=%s name=%r",
                         game_id, name)
            return None, False
        _record_ext_id(conn, existing[0], ext, name)
        return existing[0], False
    new_id = cur.lastrowid
    _record_ext_id(conn, new_id, ext, name)
    return new_id, True


def _mark_owned(conn: sqlite3.Connection, report: SteamReport, dlc_id: int) -> None:
    """Flip a dlc row owned (0 -> 1 only)."""
    owned = conn.execute("SELECT owned FROM dlc WHERE id=?", (dlc_id,)).fetchone()[0]
    if owned:
        return
    conn.execute("UPDATE dlc SET owned=1 WHERE id=?", (dlc_id,))
    report.owned_marked += 1
    report.marked_items.append(dlc_id)


def enrich_and_mark(conn: sqlite3.Connection, owned_app_ids: set[int], *,
                    fetch=None,
                    progress: Callable[[int, int | None, int | None], None] | None = None) -> SteamReport:
    """Populate each owned Steam game's DLC catalogue and mark owned by appid.

    For every game with a `steam` external id: fetch its catalogue, reconcile-or-
    create each catalogue DLC as a dlc row, and set owned=1 for those whose appid is
    in owned_app_ids. 0 -> 1 only, idempotent. A per-app fetch error is logged and
    skipped. `fetch(appid) -> data|None` is injected for offline tests.
    """
    if fetch is None:
        fetch = fetch_appdetails
    report = SteamReport()
    steam_games = conn.execute(
        "SELECT g.id AS game_id, gx.external_id AS appid "
        "FROM games g JOIN game_external_ids gx ON gx.game_id = g.id "
        "WHERE gx.source = 'steam'").fetchall()
    total = len(steam_games)
    done = 0
    for grow in steam_games:
        game_id = grow["game_id"]
        try:
            game_appid = int(grow["appid"])
        except (TypeError, ValueError):
            done += 1
            if progress:
                progress(done, total, report.catalogue_added)
            continue
        try:
            data = fetch(game_appid)
        except requests.RequestException as exc:
            report.errors += 1
            logger.warning("steam appdetails failed for game %s (app %s): %s",
                           game_id, game_appid, exc)
            done += 1
            if progress:
                progress(done, total, report.catalogue_added)
            continue
        report.games += 1
        store_genres(conn, game_id, parse_genres(data))   # tag genres from Steam's own data
        for dlc_appid in parse_catalogue(data):
            try:
                dlc_data = fetch(dlc_appid)
            except requests.RequestException as exc:
                report.errors += 1
                logger.warning("steam appdetails failed for dlc %s: %s", dlc_appid, exc)
                continue
            name = parse_appdetails_name(dlc_data)
            if not name:
                continue
            dlc_id, created = _reconcile_or_create(conn, game_id, name, dlc_appid)
            if dlc_id is None:          # unreachable name-conflict race; skip safely
                report.errors += 1
                continue
            if created:
                report.catalogue_added += 1
            if dlc_appid in owned_app_ids:
                _mark_owned(conn, report, dlc_id)
        conn.commit()                  # commit per game (mirrors igdb_dlc.enrich_missing)
        done += 1
        if progress:
            progress(done, total, report.catalogue_added)
    return report
