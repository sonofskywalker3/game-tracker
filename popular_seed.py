"""SP-B: seed game_traits.default.json from IGDB's most-rated games.

Classification (session_length) is done by an external controller-run Claude
Code Workflow; this module only (a) fetches the candidate game list from IGDB,
(b) drops games already in the catalog, and (c) merges approved verdicts back
into game_traits.default.json with a minimal diff. It never touches games.db.
"""
from __future__ import annotations

import argparse
import json
import logging
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


_VALID_SESSION_LENGTHS = frozenset({"short", "long"})


def merge_classifications(verdicts: list[dict], *,
                          catalog_path: Path = GAME_TRAITS_DEFAULT_PATH) -> tuple[int, int]:
    """Add ``{session_length}`` entries for new normalized_titles. Never overwrite
    an existing entry; skip anything not in {short, long} (unknown/abstentions)
    and rows missing a normalized_title. Write sorted + 2-space indent + preserved
    trailing newline (minimal diff). Returns ``(added, skipped)``."""
    raw = catalog_path.read_text(encoding="utf-8")
    catalog = json.loads(raw)
    added = skipped = 0
    for v in verdicts:
        nt = v.get("normalized_title")
        sl = v.get("session_length")
        if not nt or sl not in _VALID_SESSION_LENGTHS or nt in catalog:
            skipped += 1
            continue
        catalog[nt] = {"session_length": sl}
        added += 1
    out = json.dumps(catalog, sort_keys=True, indent=2, ensure_ascii=False)
    if raw.endswith("\n"):
        out += "\n"
    catalog_path.write_text(out, encoding="utf-8", newline="\n")
    return added, skipped


logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SP-B popular-games session_length seed")
    parser.add_argument("--fetch", type=int, metavar="N",
                        help="fetch top-N popular games from IGDB to --out (JSON)")
    parser.add_argument("--out", type=str, help="output path for --fetch")
    parser.add_argument("--merge", type=str, metavar="VERDICTS_JSON",
                        help="merge approved {normalized_title, session_length} verdicts")
    args = parser.parse_args(argv)

    if args.fetch:
        import config
        cid, sec = config.get_twitch_credentials()
        token = igdb_dlc.get_access_token(cid, sec)
        games = select_unseeded(fetch_top_games(args.fetch, client_id=cid, token=token))
        out_path = Path(args.out or "popular_seed_input.json")
        out_path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"fetched {len(games)} unseeded games -> {out_path}")
        return

    if args.merge:
        verdicts = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        added, skipped = merge_classifications(verdicts, catalog_path=GAME_TRAITS_DEFAULT_PATH)
        print(f"merged: added {added}, skipped {skipped}")
        return

    parser.error("nothing to do: pass --fetch N or --merge PATH")


if __name__ == "__main__":
    main()
