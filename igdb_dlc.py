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
from collections.abc import Callable

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
# every stored DLC row's igdb_id would be NULL. genres.name feeds the genre tags
# used for slot/session classification (slot_signals).
_DLC_FIELDS = ("name, slug, cover.url, genres.name, dlcs.id, dlcs.name, expansions.id, "
               "expansions.name, standalone_expansions.id, standalone_expansions.name")

# IGDB genre name -> our canonical tag vocabulary (slot_signals matches these names
# exactly). Unmapped IGDB genres are stored under their own name. Extensible table.
IGDB_GENRE_TO_TAG: dict[str, str] = {
    "Point-and-click": "Adventure",
    "Fighting": "Fighting",
    "Shooter": "Shooter",
    "Music": "Rhythm",
    "Platform": "Platformer",
    "Puzzle": "Puzzle",
    "Racing": "Racing",
    "Real Time Strategy (RTS)": "Strategy",
    "Role-playing (RPG)": "RPG",
    "Simulator": "Simulation",
    "Sport": "Sports",
    "Strategy": "Strategy",
    "Turn-based strategy (TBS)": "Strategy",
    "Tactical": "Strategy",
    "Hack and slash/Beat 'em up": "Action",
    "Adventure": "Adventure",
    "Indie": "Indie",
    "Arcade": "Action",
    "Visual Novel": "Visual Novel",
    "Card & Board Game": "Strategy",
    "MOBA": "Strategy",
}
GENRE_BACKFILL_BATCH = 400


def parse_genres(game: dict) -> list[str]:
    """Canonical genre tag names from an IGDB payload (mapped, deduped, order kept)."""
    out: list[str] = []
    for g in (game or {}).get("genres") or []:
        name = g.get("name")
        if not name:
            continue
        tag = IGDB_GENRE_TO_TAG.get(name, name)
        if tag not in out:
            out.append(tag)
    return out


def store_genres(conn: sqlite3.Connection, game_id: int, names: list[str]) -> int:
    """Upsert each genre tag (category='genre') and link it to the game. Idempotent;
    returns the number of NEW game<->genre links created."""
    linked = 0
    for name in names:
        conn.execute("INSERT OR IGNORE INTO tags (name, category) VALUES (?, 'genre')", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
        cur = conn.execute(
            "INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)", (game_id, tag_id))
        linked += cur.rowcount
    return linked


def fetch_games_by_ids(igdb_ids: list[int], client_id: str, token: str) -> list[dict]:
    """Batch-fetch IGDB game payloads by id (one query for many ids)."""
    if not igdb_ids:
        return []
    ids = ",".join(str(int(i)) for i in igdb_ids)
    return _igdb_query(f"fields {_DLC_FIELDS}; where id = ({ids}); limit 500;", client_id, token)


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
    store_genres(conn, game_id, parse_genres(game))
    return {"matched": True, "cover_set": cover_set, **counts}


def backfill_genres(conn: sqlite3.Connection, *, client_id: str, token: str,
                    progress: Callable[[int, int | None, int | None], None] | None = None) -> int:
    """Fetch + store genres for already-enriched games (igdb_id set) that carry no
    genre tag yet. Batched IGDB lookups. Returns the count of games newly tagged.

    Lets a re-scrape backfill genres for games that were enriched before genre
    fetching existed, without re-resolving identity. Genre-less games (IGDB returns
    no genres) simply gain nothing this pass.
    """
    rows = conn.execute(
        "SELECT id, igdb_id FROM games WHERE igdb_id IS NOT NULL AND id NOT IN "
        "(SELECT gt.game_id FROM game_tags gt JOIN tags t ON t.id = gt.tag_id "
        " WHERE t.category = 'genre')").fetchall()
    by_igdb: dict[int, list[int]] = {}
    for r in rows:
        by_igdb.setdefault(r["igdb_id"], []).append(r["id"])
    igdb_ids = list(by_igdb)
    tagged = 0
    done = 0
    for i in range(0, len(igdb_ids), GENRE_BACKFILL_BATCH):
        chunk = igdb_ids[i:i + GENRE_BACKFILL_BATCH]
        try:
            payloads = fetch_games_by_ids(chunk, client_id, token)
        except requests.RequestException as exc:
            logger.warning("genre backfill batch failed: %s", exc)
            continue
        for payload in payloads:
            names = parse_genres(payload)
            if not names:
                continue
            for gid in by_igdb.get(payload.get("id"), []):
                if store_genres(conn, gid, names):
                    tagged += 1
        conn.commit()
        done += len(chunk)
        if progress:
            progress(done, len(igdb_ids), tagged)
    return tagged


def enrich_missing(conn: sqlite3.Connection, *, client_id: str, token: str,
                   progress: Callable[[int, int | None, int | None], None] | None = None) -> dict:
    """Enrich every never-enriched game (games.igdb_id IS NULL). Commits per game;
    a per-game network error is logged and skipped (never aborts the run)."""
    placeholders = ",".join("?" * len(VENDOR_CATALOGUE_SOURCES))
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM games WHERE igdb_id IS NULL AND COALESCE(igdb_locked, 0) = 0 "
        f"AND id NOT IN "
        f"(SELECT game_id FROM game_external_ids WHERE source IN ({placeholders}))",
        VENDOR_CATALOGUE_SOURCES)]
    totals = {"games": 0, "matched": 0, "added": 0, "errors": 0}
    done = 0
    for gid in ids:
        try:
            rep = enrich_game(conn, gid, client_id, token)
            conn.commit()
        except requests.RequestException as exc:
            conn.rollback()
            totals["errors"] += 1
            logger.warning("DLC enrich failed for game %s: %s", gid, exc)
            done += 1
            if progress:
                progress(done, len(ids), totals["added"])
            continue
        totals["games"] += 1
        totals["matched"] += int(rep["matched"])
        totals["added"] += rep["added"]
        done += 1
        if progress:
            progress(done, len(ids), totals["added"])
    return totals
