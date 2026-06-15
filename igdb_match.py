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
# overlap (safe). Mobile is handled separately via MOBILE_PLATFORM_IDS. Legacy ids
# confirmed against IGDB's live /v4/platforms (2026-06-15).
IGDB_PLATFORM_IDS: dict[str, frozenset[int]] = {
    # Modern
    "Switch": frozenset({130}),
    "PS5": frozenset({167}),
    "PS4": frozenset({48}),
    "Xbox": frozenset({49, 169}),   # Xbox One + Series X|S
    "Steam": frozenset({6}),         # PC (Windows)
    "PC": frozenset({6}),
    # Legacy (mirrors models.LEGACY_PLATFORM_SEED short_names)
    "3DS": frozenset({37}),
    "NDS": frozenset({20}),
    "GBA": frozenset({24}),
    "GBC": frozenset({22}),
    "GB": frozenset({33}),
    "WiiU": frozenset({41}),
    "Wii": frozenset({5}),
    "GC": frozenset({21}),
    "N64": frozenset({4}),
    "SNES": frozenset({19}),
    "NES": frozenset({18}),
    "PS3": frozenset({9}),
    "PS2": frozenset({8}),
    "PS1": frozenset({7}),
    "PSP": frozenset({38}),
    "Vita": frozenset({46}),
    "X360": frozenset({12}),
    "OGXbox": frozenset({11}),
    "Genesis": frozenset({29}),
    "Saturn": frozenset({32}),
    "Dreamcast": frozenset({23}),
}

# Ordered, human-readable platform labels for the Fix-match modal. Derived from the
# same IGDB ids as IGDB_PLATFORM_IDS / mobile so identical-looking covers can be told
# apart by which platforms each entry covers. Unknown ids contribute no label.
_PLATFORM_LABELS: tuple[tuple[str, frozenset[int]], ...] = (
    ("PS5", frozenset({167})),
    ("PS4", frozenset({48})),
    ("Switch", frozenset({130})),
    ("Xbox", frozenset({49, 169})),
    ("PC", frozenset({6})),
    ("iOS", frozenset({IOS_ID})),
    ("Android", frozenset({ANDROID_ID})),
)

_TITLE_EXACT = 100
_TITLE_CONTAINS = 40
_PLATFORM_OVERLAP = 50
_MOBILE_PENALTY = -80
_HAS_COVER = 10
_BUNDLE_GAME_TYPE = 3
_REVIEW_MARGIN = 1                                 # flag when best beats stored by >= this
_STRONG_MATCH = _TITLE_EXACT + _PLATFORM_OVERLAP   # bar for flagging an unmatched game


def platform_ids_for(short_names: Iterable[str] | None) -> set[int]:
    """Map app platform short_names to the set of IGDB platform ids."""
    out: set[int] = set()
    for sn in short_names or ():
        out |= set(IGDB_PLATFORM_IDS.get(sn, ()))
    return out


def short_names_for(igdb_platform_ids: Iterable[int] | None) -> list[str]:
    """Reverse of platform_ids_for: app short_names whose IGDB ids overlap the
    given ids, in IGDB_PLATFORM_IDS insertion order. Unknown ids are omitted."""
    ids = set(igdb_platform_ids or ())
    return [sn for sn, pset in IGDB_PLATFORM_IDS.items() if pset & ids]


def platform_labels(platform_ids: Iterable[int] | None) -> list[str]:
    """Readable platform names for a set of IGDB platform ids, in a stable order.
    Unknown ids are omitted (e.g. retro/handheld ids the tracker does not model)."""
    ids = set(platform_ids or ())
    return [name for name, pset in _PLATFORM_LABELS if pset & ids]


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


def fetch_entry(igdb_id: int, client_id: str, token: str) -> dict | None:
    """Fetch one IGDB entry by id with the fields the scorer needs. Returns the
    raw IGDB dict (name, cover.url, platforms, ...) or None if the id is gone."""
    query = (
        "fields name, cover.url, platforms, first_release_date, "
        "total_rating_count, game_type; "
        f"where id = {int(igdb_id)}; limit 1;"
    )
    rows = igdb_dlc._igdb_query(query, client_id, token) or []
    return rows[0] if rows else None


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


def modal_candidates(cands: list[dict], game_title: str,
                     current_cover: str | None = None) -> list[dict]:
    """Shape `candidates_for` output for the Fix-match modal.

    Keeps candidates that have a cover AND either come from the bundle or whose name
    normalises equal to the game's title (drops mod/hack junk like "… Alter"). If no
    candidate matches the title, falls back to all cover-bearing candidates so the
    modal is never empty when art exists. Drops any candidate whose cover matches the
    game's current cover (already shown as the Current tile) and de-duplicates by cover
    stem, keeping the first of each identical-art group (input is already best-first)."""
    target = normalize_title(game_title)
    current_stem = _cover_stem(current_cover)
    with_cover = [c for c in cands if c.get("cover_url")]
    matched = [c for c in with_cover
               if c.get("source") == "bundle"
               or normalize_title(c.get("name") or "") == target]
    pool = matched or with_cover
    out: list[dict] = []
    seen: set[str | None] = set()
    for c in pool:
        stem = _cover_stem(c.get("cover_url"))
        if current_stem is not None and stem == current_stem:
            continue
        if stem in seen:
            continue
        seen.add(stem)
        out.append(c)
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


def _score_entry(entry: dict | None, *, game_platform_ids: set[int],
                 title: str) -> dict | None:
    """Score one IGDB entry dict with score_candidates. Returns the scored dict
    (carrying _score / _mobile_only) or None if it is falsy or fails title match."""
    if not entry:
        return None
    scored = score_candidates([entry], game_platform_ids=game_platform_ids, title=title)
    return scored[0] if scored else None


def _flag_reason(best: dict, best_scored: dict, stored_entry: dict | None,
                 stored_scored: dict | None, game_platform_ids: set[int]) -> str:
    """Short human reason a game was flagged, by why the candidate won."""
    if best.get("source") == "bundle":
        return "bundle"
    if stored_scored is None:
        return "unmatched->match"
    if stored_scored.get("_mobile_only") and not best_scored.get("_mobile_only"):
        return "mobile->console"
    best_overlap = bool(set(best.get("platforms") or []) & game_platform_ids)
    stored_overlap = bool(set((stored_entry or {}).get("platforms") or []) & game_platform_ids)
    if best_overlap and not stored_overlap:
        return "better platform match"
    return "stronger match"


def audit_igdb_matches(conn, *, client_id: str, token: str) -> dict[str, list[int]]:
    """Reconcile every non-locked game against its best IGDB candidate.

    Bundle-source candidates are authoritative: apply id + cover + lock and clear
    review (self-heal). Search-source candidates flag for review on a positive
    quality score-delta; games with no scorable stored entry flag only on a strong
    candidate. Returns ``{"applied": [...], "flagged": [...]}`` of game ids."""
    rows = conn.execute(
        "SELECT id, title, cover_url, collection_name, igdb_id FROM games "
        "WHERE COALESCE(igdb_locked, 0) = 0 ORDER BY title").fetchall()
    flagged: list[int] = []
    applied: list[int] = []
    for r in rows:
        plat_short = [x[0] for x in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
            "ON p.id = gp.platform_id WHERE gp.game_id = ?", (r["id"],))]
        gpi = platform_ids_for(plat_short)
        cands = candidates_for(r["title"], gpi, r["collection_name"], client_id, token)
        if not cands:
            continue
        best = cands[0]
        best_cover = best.get("cover_url")
        if not best_cover:
            continue
        best_stem, stored_stem = _cover_stem(best_cover), _cover_stem(r["cover_url"])
        if best_stem and stored_stem and best_stem == stored_stem:
            continue

        best_min = {"name": best.get("name"), "platforms": best.get("platforms") or [],
                    "cover": {"url": best_cover}}
        best_scored = _score_entry(best_min, game_platform_ids=gpi, title=r["title"])
        if best_scored is None:
            continue

        stored_entry = fetch_entry(r["igdb_id"], client_id, token) if r["igdb_id"] else None
        stored_scored = _score_entry(stored_entry, game_platform_ids=gpi, title=r["title"])

        if best.get("source") == "bundle":
            # Bundle constituents are authoritative (exact-title match inside the
            # owned bundle). Apply + lock instead of flagging: the pipeline owns
            # this identity, so heal it in place and clear any prior review flag.
            conn.execute(
                "UPDATE games SET igdb_id = ?, cover_url = ?, igdb_locked = 1, "
                "needs_igdb_review = 0, igdb_review_reason = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (best["igdb_id"], best_cover, r["id"]))
            applied.append(r["id"])
            continue
        elif stored_scored is not None:
            should_flag = best_scored["_score"] - stored_scored["_score"] >= _REVIEW_MARGIN
        else:
            should_flag = (best_scored["_score"] >= _STRONG_MATCH
                           and not best_scored.get("_mobile_only"))

        if should_flag:
            reason = _flag_reason(best, best_scored, stored_entry, stored_scored, gpi)
            conn.execute(
                "UPDATE games SET needs_igdb_review = 1, igdb_review_reason = ? WHERE id = ?",
                (reason, r["id"]))
            flagged.append(r["id"])
    conn.commit()
    return {"applied": applied, "flagged": flagged}
