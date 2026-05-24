"""IGDB-sourced DLC/expansion enrichment for games.

Pure helpers (parse/merge/slug/cover formatting) are unit-tested; the network
fetch (`_igdb_query` and the `fetch_game_by_*` wrappers) is isolated so tests
monkeypatch `_igdb_query`. Auth is reused from fetch_covers (no duplicated token
logic). DLC is a child of a game, never a games row.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time

import requests

from fetch_covers import IGDB_API_URL, get_access_token  # noqa: F401 (get_access_token re-exported for callers)

logger = logging.getLogger(__name__)

# IGDB relations that count as DLC, with the kind we store.
_DLC_RELATIONS = (("dlcs", "dlc"), ("expansions", "expansion"),
                  ("standalone_expansions", "expansion"))


def parse_dlc_payload(igdb_game: dict) -> list[dict]:
    """Flatten an IGDB game's dlcs/expansions into {name, igdb_id, kind} dicts.

    Blanks are dropped; names are de-duped within the payload (case-insensitive).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for key, kind in _DLC_RELATIONS:
        for item in igdb_game.get(key) or []:
            name = (item.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append({"name": name, "igdb_id": item.get("id"), "kind": kind})
    return out


_IGDB_GAME_URL = re.compile(r"https?://(?:www\.)?igdb\.com/games/([a-z0-9][a-z0-9\-]*)",
                            re.IGNORECASE)


def slug_from_igdb_url(url: str | None) -> str | None:
    """Extract the game slug from an igdb.com/games/<slug> URL, else None."""
    if not url:
        return None
    m = _IGDB_GAME_URL.search(url.strip())
    return m.group(1).lower() if m else None


def format_cover_url(raw: str | None) -> str | None:
    """Upgrade an IGDB cover URL to t_cover_big and ensure an https scheme."""
    if not raw:
        return None
    url = raw.replace("t_thumb", "t_cover_big")
    if not url.startswith("http"):
        url = "https:" + url
    return url


def merge_dlc(conn: sqlite3.Connection, game_id: int, parsed: list[dict]) -> dict:
    """Insert any parsed DLC not already present for the game (by name).

    Never updates existing rows, so `owned` and manual entries are preserved.
    Returns {"added", "existing"}.
    """
    added = existing = 0
    for d in parsed:
        present = conn.execute(
            "SELECT 1 FROM dlc WHERE game_id = ? AND name = ?", (game_id, d["name"])).fetchone()
        if present:
            existing += 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO dlc (game_id, name, igdb_id, kind, source) "
            "VALUES (?, ?, ?, ?, 'igdb')",
            (game_id, d["name"], d.get("igdb_id"), d.get("kind", "dlc")))
        added += 1
    return {"added": added, "existing": existing}
