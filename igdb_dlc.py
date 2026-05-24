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
