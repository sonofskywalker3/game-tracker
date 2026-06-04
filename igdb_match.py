"""Bundle-aware IGDB identity resolution.

Resolves a game to its correct IGDB entry (igdb_id + cover) by, in order:
  1. bundle-first — if the game came from a bundle (collection_name set), resolve
     the bundle on IGDB and pull its canonical constituents via the reverse
     `where bundles = (id)` lookup, then match by normalized_title;
  2. fallback — a platform-aware, mobile-penalized title-search scorer.

All IGDB access goes through igdb_dlc._igdb_query (monkeypatched in tests).
"""
from __future__ import annotations

from collections.abc import Iterable

import igdb_dlc
from models import normalize_title

IOS_ID = 39
ANDROID_ID = 34
MOBILE_PLATFORM_IDS = frozenset({IOS_ID, ANDROID_ID})

# app short_name -> IGDB platform id(s). Extensible; unknown names contribute no
# overlap (safe). Mobile is handled separately via MOBILE_PLATFORM_IDS.
IGDB_PLATFORM_IDS: dict[str, frozenset[int]] = {
    "Switch": frozenset({130}),
    "PS5": frozenset({167}),
    "PS4": frozenset({48}),
    "Xbox": frozenset({49, 169}),   # Xbox One + Series X|S
    "Steam": frozenset({6}),         # PC (Windows)
    "PC": frozenset({6}),
}

_TITLE_EXACT = 100
_TITLE_CONTAINS = 40
_PLATFORM_OVERLAP = 50
_MOBILE_PENALTY = -80
_HAS_COVER = 10
_BUNDLE_GAME_TYPE = 3


def platform_ids_for(short_names: Iterable[str] | None) -> set[int]:
    """Map app platform short_names to the set of IGDB platform ids."""
    out: set[int] = set()
    for sn in short_names or ():
        out |= set(IGDB_PLATFORM_IDS.get(sn, ()))
    return out


def _title_score(cand_name: str, search: str) -> int | None:
    a, b = normalize_title(cand_name), normalize_title(search)
    if not a or not b:
        return None
    if a == b:
        return _TITLE_EXACT
    if b in a or a in b:
        return _TITLE_CONTAINS
    return None


def score_candidates(candidates: list[dict], *, game_platform_ids: set[int],
                     title: str | None = None) -> list[dict]:
    """Return candidates ranked best-first. Drops non-title matches when `title`
    is given. Each returned candidate carries `_score` and `_mobile_only`."""
    ranked = []
    for c in candidates:
        plats = set(c.get("platforms") or [])
        mobile_only = bool(plats) and plats <= MOBILE_PLATFORM_IDS
        score = 0
        if title is not None:
            ts = _title_score(c.get("name", ""), title)
            if ts is None:
                continue
            score += ts
        if plats & game_platform_ids:
            score += _PLATFORM_OVERLAP
        if mobile_only:
            score += _MOBILE_PENALTY
        if c.get("cover"):
            score += _HAS_COVER
        out = dict(c)
        out["_score"] = score
        out["_mobile_only"] = mobile_only
        ranked.append(out)
    # -(first_release_date) makes the EARLIEST/original release win ties,
    # so the canonical retro entry beats modern re-releases.
    ranked.sort(key=lambda c: (c["_score"], c.get("total_rating_count") or 0,
                               -(c.get("first_release_date") or 0)), reverse=True)
    return ranked


def _escape(title: str) -> str:
    return title.replace('"', "").replace("\n", " ").strip()


def fetch_candidates(title: str, client_id: str, token: str,
                     limit: int = 10) -> list[dict]:
    """Title-search IGDB, returning candidates WITH platform + ranking signals."""
    # game_type is surfaced for the Phase-4 disambiguation modal display,
    # not used by the scorer.
    query = (
        f'search "{_escape(title)}"; '
        "fields name, cover.url, platforms, first_release_date, "
        "total_rating_count, game_type; "
        f"limit {int(limit)};"
    )
    return igdb_dlc._igdb_query(query, client_id, token) or []


def cover_url_of(candidate: dict) -> str | None:
    """Return a normalized https t_cover_big URL for a candidate, or None."""
    cover = candidate.get("cover") or {}
    url = cover.get("url")
    if not url:
        return None
    url = url.replace("t_thumb", "t_cover_big")
    return url if url.startswith("http") else "https:" + url


def _cover_stem(url: str | None) -> str | None:
    """The IGDB image id from a cover URL, ignoring size token + extension, so
    cosmetic differences (.webp vs .jpg, t_thumb vs t_cover_big) collapse to the
    same value. '.../t_cover_big/co1zyu.jpg' -> 'co1zyu'. None if no usable id."""
    if not url:
        return None
    last = url.rsplit("/", 1)[-1]      # 'co1zyu.jpg'
    stem = last.rsplit(".", 1)[0]      # 'co1zyu'
    return stem or None


def resolve_bundle(name: str, game_platform_ids: set[int],
                   client_id: str, token: str) -> int | None:
    """Find the IGDB bundle (game_type==3) for `name`, preferring one whose
    platforms overlap the owned bundle's platforms, then exact-title match."""
    query = (
        f'search "{_escape(name)}"; '
        "fields name, game_type, platforms; limit 10;"
    )
    results = igdb_dlc._igdb_query(query, client_id, token) or []
    bundles = [r for r in results if r.get("game_type") == _BUNDLE_GAME_TYPE]
    if not bundles:
        return None
    target = normalize_title(name)

    def rank(r: dict) -> tuple:
        plats = set(r.get("platforms") or [])
        return (bool(plats & game_platform_ids),
                normalize_title(r.get("name", "")) == target)

    best = max(bundles, key=rank)
    return best.get("id")


def bundle_constituents(bundle_id: int, client_id: str, token: str) -> list[dict]:
    """Reverse lookup: the games whose `bundles` array contains bundle_id.
    Returns [{igdb_id, name, normalized_title, cover_url, platforms}]."""
    query = (
        "fields name, cover.url, platforms, first_release_date; "
        f"where bundles = ({int(bundle_id)}); limit 50;"
    )
    rows = igdb_dlc._igdb_query(query, client_id, token) or []
    out = []
    for r in rows:
        out.append({
            "igdb_id": r.get("id"),
            "name": r.get("name"),
            "normalized_title": normalize_title(r.get("name", "")),
            "cover_url": cover_url_of(r),
            "platforms": r.get("platforms") or [],
        })
    return out


def _as_identity(igdb_id: int | None, name: str | None, cover_url: str | None, source: str) -> dict:
    return {"igdb_id": igdb_id, "name": name, "cover_url": cover_url,
            "source": source}


def candidates_for(title: str, game_platform_ids: set[int],
                   collection_name: str | None, client_id: str, token: str
                   ) -> list[dict]:
    """Ranked identity candidates, bundle-derived first then scored search.
    Each: {igdb_id, name, cover_url, platforms, source, score?}."""
    out: list[dict] = []
    seen: set[int] = set()
    target = normalize_title(title)
    if collection_name:
        bid = resolve_bundle(collection_name, game_platform_ids, client_id, token)
        if bid:
            for c in bundle_constituents(bid, client_id, token):
                if normalize_title(c["name"] or "") == target and c["igdb_id"] not in seen:
                    seen.add(c["igdb_id"])
                    out.append({**c, "source": "bundle"})
    # Bundle constituents carry the IGDB id under "igdb_id"; raw search results
    # carry it under "id" — same value, different dict shape — so the dedup is intentional.
    for c in score_candidates(fetch_candidates(title, client_id, token),
                              game_platform_ids=game_platform_ids, title=title):
        if c.get("id") in seen:
            continue
        out.append({"igdb_id": c.get("id"), "name": c.get("name"),
                    "cover_url": cover_url_of(c), "platforms": c.get("platforms") or [],
                    "source": "search", "score": c["_score"]})
    return out


def resolve_identity(title: str, game_platform_ids: set[int],
                     collection_name: str | None, client_id: str, token: str
                     ) -> dict | None:
    """Best identity for a game (bundle-first). Returns an identity dict or None."""
    cands = candidates_for(title, game_platform_ids, collection_name, client_id, token)
    if not cands:
        return None
    best = cands[0]
    return _as_identity(best["igdb_id"], best["name"], best.get("cover_url"),
                        best["source"])


def audit_igdb_matches(conn, *, client_id: str, token: str) -> list[int]:
    """Flag (needs_igdb_review=1) every non-locked game whose resolved best
    identity's cover differs from the current cover. Never mutates cover/igdb_id;
    games whose current cover already matches the resolved one are not flagged.
    Returns the list of flagged game ids."""
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name FROM games "
        "WHERE COALESCE(igdb_locked, 0) = 0 ORDER BY title").fetchall()
    flagged: list[int] = []
    for r in rows:
        plat_short = [x[0] for x in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (r["id"],))]
        identity = resolve_identity(r["title"], platform_ids_for(plat_short),
                                    r["collection_name"], client_id, token)
        if not identity or not identity.get("cover_url"):
            continue
        if identity["cover_url"] != r["cover_url"]:
            conn.execute("UPDATE games SET needs_igdb_review = 1 WHERE id = ?", (r["id"],))
            flagged.append(r["id"])
    conn.commit()
    return flagged
