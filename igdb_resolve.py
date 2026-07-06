"""IGDB-canonical resolution for the collections layer (SP-A Stage 1).

Fetches every collection a game belongs to (multi-membership — FF7 Remake is in
"Final Fantasy", "Compilation of FF7", AND "FF7 Remake") plus the game's
ORIGINAL release date (own date, else version_parent / parent_game — remasters
sort at the original), and syncs them into the local collections tables.

Also exposes the bundle-constituent reverse lookup (game_type 3 bundles carry
no constituent field; the reliable path is `where bundles = (<bundle_id>)`).
"""
import logging
import sqlite3

import requests

logger = logging.getLogger(__name__)

IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
IGDB_TIMEOUT_SECONDS = 30
# IGDB caps `limit` at 500; batch id lookups accordingly.
IGDB_BATCH_SIZE = 500

# IGDB game_type id for a bundle (see docs/superpowers/specs/
# 2026-07-06-spa-igdb-resolution-phase0-findings.md for the full enum).
GAME_TYPE_BUNDLE = 3


def _headers(client_id: str, token: str) -> dict:
    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/plain",
    }


def _original_release_ts(row: dict) -> int | None:
    """Earliest known release for a game row: min of its own date and its
    version_parent / parent_game dates (a remaster belongs at the original)."""
    dates = [row.get("first_release_date"),
             (row.get("version_parent") or {}).get("first_release_date"),
             (row.get("parent_game") or {}).get("first_release_date")]
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def fetch_game_collections(igdb_ids, client_id: str, token: str) -> dict:
    """Batched lookup: {igdb_id: {"collections": [{id,name,slug}],
    "original_release_ts": int|None}} for every id IGDB returned. Empty input
    returns {} without a network call."""
    ids = sorted({i for i in igdb_ids if i})
    out: dict = {}
    for start in range(0, len(ids), IGDB_BATCH_SIZE):
        batch = ids[start:start + IGDB_BATCH_SIZE]
        query = (
            "fields id, first_release_date, version_parent.first_release_date, "
            "parent_game.first_release_date, collections.name, collections.slug; "
            f"where id = ({','.join(str(i) for i in batch)}); limit {len(batch)};")
        response = requests.post(IGDB_GAMES_URL, headers=_headers(client_id, token),
                                 data=query, timeout=IGDB_TIMEOUT_SECONDS)
        response.raise_for_status()
        for row in response.json():
            out[row["id"]] = {
                "collections": [
                    {"id": c["id"], "name": c.get("name") or "",
                     "slug": c.get("slug")}
                    for c in row.get("collections", []) if c.get("id")
                ],
                "original_release_ts": _original_release_ts(row),
            }
    return out


def sync_game_collections(conn: sqlite3.Connection, game_id: int, info: dict) -> None:
    """Write one game's IGDB collection info: upsert collection rows (IGDB may
    rename), REPLACE the game's memberships (never accumulate stale rows), and
    set original_release_ts. Commits."""
    for c in info.get("collections", []):
        conn.execute(
            "INSERT INTO collections (id, name, slug) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, slug = excluded.slug",
            (c["id"], c["name"], c.get("slug")))
    conn.execute("DELETE FROM game_collections WHERE game_id = ?", (game_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO game_collections (game_id, collection_id) VALUES (?, ?)",
        [(game_id, c["id"]) for c in info.get("collections", [])])
    if info.get("original_release_ts") is not None:
        conn.execute("UPDATE games SET original_release_ts = ? WHERE id = ?",
                     (info["original_release_ts"], game_id))
    conn.commit()


def backfill_collections(conn: sqlite3.Connection, client_id: str, token: str,
                         progress=None) -> dict:
    """Resolve collections for EVERY game with a pinned igdb_id (a whole library
    is only a handful of batched calls). Returns
    {"games": resolved, "collections": distinct, "memberships": total}."""
    rows = conn.execute(
        "SELECT id, igdb_id FROM games WHERE igdb_id IS NOT NULL").fetchall()
    by_igdb = {r["igdb_id"]: r["id"] for r in rows}
    fetched = fetch_game_collections(list(by_igdb), client_id, token)
    games = memberships = 0
    for igdb_id, info in fetched.items():
        game_id = by_igdb.get(igdb_id)
        if game_id is None:
            continue
        sync_game_collections(conn, game_id, info)
        games += 1
        memberships += len(info["collections"])
        if progress:
            progress(games, len(by_igdb))
    n_collections = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
    return {"games": games, "collections": n_collections, "memberships": memberships}


def resolve_bundle_constituents(bundle_igdb_id: int, client_id: str, token: str) -> list[dict]:
    """Constituent games of an IGDB bundle via the reverse lookup — the bundle
    itself carries no constituent field. Returns [{id, name, game_type}]."""
    query = ("fields id, name, game_type; "
             f"where bundles = ({int(bundle_igdb_id)}); limit 50;")
    response = requests.post(IGDB_GAMES_URL, headers=_headers(client_id, token),
                             data=query, timeout=IGDB_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()
