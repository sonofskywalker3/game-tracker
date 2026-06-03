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

# Games whose DLC catalogue comes from a vendor store (not IGDB) are skipped by
# enrich_missing -- IGDB is only the fallback catalogue for games without one.
VENDOR_CATALOGUE_SOURCES = ("steam",)


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


# Nested ids are requested explicitly — IGDB omits them unless named, otherwise
# every stored DLC row's igdb_id would be NULL.
_DLC_FIELDS = ("name, slug, cover.url, dlcs.id, dlcs.name, expansions.id, "
               "expansions.name, standalone_expansions.id, standalone_expansions.name")


def _igdb_query(query: str, client_id: str, access_token: str,
                *, _retried: bool = False) -> list[dict]:
    """POST an apicalypse query to IGDB /games; retry once on 429, then give up."""
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {access_token}",
               "Content-Type": "text/plain"}
    response = requests.post(f"{IGDB_API_URL}/games", headers=headers, data=query)
    if response.status_code == 429 and not _retried:
        time.sleep(1)
        return _igdb_query(query, client_id, access_token, _retried=True)
    response.raise_for_status()
    return response.json()


def fetch_game_by_id(igdb_id: int, client_id: str, token: str) -> dict | None:
    res = _igdb_query(f"fields {_DLC_FIELDS}; where id = {int(igdb_id)};", client_id, token)
    return res[0] if res else None


def fetch_game_by_slug(slug: str, client_id: str, token: str) -> dict | None:
    safe = slug.replace('"', "")
    res = _igdb_query(f'fields {_DLC_FIELDS}; where slug = "{safe}";', client_id, token)
    return res[0] if res else None



def enrich_game(conn: sqlite3.Connection, game_id: int, client_id: str, token: str,
                *, slug: str | None = None) -> dict:
    """Resolve a game on IGDB (by slug, stored id, or title), store igdb_id +
    cover, and merge its DLC. Returns {matched, cover_set, added, existing}.

    Cover is overwritten when pinning by slug; on auto-resolution it is only set
    when the game has no cover (never clobbers a user/IGDB cover).
    """
    row = conn.execute(
        "SELECT title, igdb_id, cover_url, collection_name FROM games WHERE id = ?",
        (game_id,)).fetchone()
    if not row:
        return {"matched": False, "cover_set": False, "added": 0, "existing": 0}
    if slug:
        game = fetch_game_by_slug(slug, client_id, token)
    elif row["igdb_id"]:
        game = fetch_game_by_id(row["igdb_id"], client_id, token)
    else:
        import igdb_match
        plat_short = [r[0] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (game_id,))]
        identity = igdb_match.resolve_identity(
            row["title"], igdb_match.platform_ids_for(plat_short),
            row["collection_name"], client_id, token)
        game = (fetch_game_by_id(identity["igdb_id"], client_id, token)
                if identity and identity.get("igdb_id") else None)
    if not game:
        return {"matched": False, "cover_set": False, "added": 0, "existing": 0}
    conn.execute("UPDATE games SET igdb_id = ? WHERE id = ?", (game.get("id"), game_id))
    cover = format_cover_url((game.get("cover") or {}).get("url"))
    cover_set = False
    if cover and (slug or not row["cover_url"]):
        conn.execute("UPDATE games SET cover_url = ? WHERE id = ?", (cover, game_id))
        cover_set = True
    counts = merge_dlc(conn, game_id, parse_dlc_payload(game))
    return {"matched": True, "cover_set": cover_set, **counts}


def enrich_missing(conn: sqlite3.Connection, *, client_id: str, token: str) -> dict:
    """Enrich every never-enriched game (games.igdb_id IS NULL). Commits per game;
    a per-game network error is logged and skipped (never aborts the run)."""
    placeholders = ",".join("?" * len(VENDOR_CATALOGUE_SOURCES))
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM games WHERE igdb_id IS NULL AND COALESCE(igdb_locked, 0) = 0 "
        f"AND id NOT IN "
        f"(SELECT game_id FROM game_external_ids WHERE source IN ({placeholders}))",
        VENDOR_CATALOGUE_SOURCES)]
    totals = {"games": 0, "matched": 0, "added": 0, "errors": 0}
    for gid in ids:
        try:
            rep = enrich_game(conn, gid, client_id, token)
            conn.commit()
        except requests.RequestException as exc:
            conn.rollback()
            totals["errors"] += 1
            logger.warning("DLC enrich failed for game %s: %s", gid, exc)
            continue
        totals["games"] += 1
        totals["matched"] += int(rep["matched"])
        totals["added"] += rep["added"]
    return totals
