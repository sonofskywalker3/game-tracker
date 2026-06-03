"""SP-B: seed game_traits.default.json from IGDB's most-rated games.

Classification (session_length) is done by an external controller-run Claude
Code Workflow; this module only (a) fetches the candidate game list from IGDB,
(b) drops games already in the catalog, and (c) merges approved verdicts back
into game_traits.default.json with a minimal diff. It never touches games.db.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import igdb_dlc
from models import GAME_TRAITS_DEFAULT_PATH, normalize_title

_MAIN_GAME_FILTER = "game_type = 0"  # verified live in Phase 0 (category=0 returns []; game_type=0 = main_game)
_PAGE_SIZE = 500
_REQ_PAUSE_SECONDS = 0.25  # IGDB courtesy (4 req/s)


def fetch_top_games(n: int, *, client_id: str, token: str,
                    page_size: int = _PAGE_SIZE) -> list[dict]:
    """Return up to ``n`` most-rated IGDB main games, de-collided by
    normalized_title (the most-rated of a colliding pair wins, since results are
    sorted by rating count descending). Each item:
    ``{igdb_id, name, normalized_title, genres: list[str], summary: str|None, year: int|None}``.
    """
    out: list[dict] = []
    seen: set[str] = set()
    offset = 0
    while len(out) < n:
        query = (
            "fields name, genres.name, summary, first_release_date, total_rating_count; "
            f"where {_MAIN_GAME_FILTER} & total_rating_count != null; "
            f"sort total_rating_count desc; limit {page_size}; offset {offset};"
        )
        rows = igdb_dlc._igdb_query(query, client_id, token)
        if not rows:
            break
        for r in rows:
            name = r.get("name")
            if not name:
                continue
            nt = normalize_title(name)
            if nt in seen:
                continue
            seen.add(nt)
            ts = r.get("first_release_date")
            out.append({
                "igdb_id": r.get("id"),
                "name": name,
                "normalized_title": nt,
                "genres": [g["name"] for g in (r.get("genres") or []) if g.get("name")],
                "summary": r.get("summary"),
                "year": time.gmtime(ts).tm_year if ts else None,
            })
            if len(out) >= n:
                break
        offset += page_size
        time.sleep(_REQ_PAUSE_SECONDS)
    return out


def select_unseeded(games: list[dict], *,
                    catalog_path: Path = GAME_TRAITS_DEFAULT_PATH) -> list[dict]:
    """Drop games whose normalized_title already has a catalog entry, and
    intra-list normalized_title duplicates (first occurrence wins)."""
    existing = set(json.loads(catalog_path.read_text(encoding="utf-8")))
    out: list[dict] = []
    seen: set[str] = set()
    for g in games:
        nt = g["normalized_title"]
        if nt in existing or nt in seen:
            continue
        seen.add(nt)
        out.append(g)
    return out
