"""UPC/barcode resolution for the mobile companion.

Resolution chain (see resolve()): local barcode_registry -> UPCitemdb free API
-> IGDB title match. External lookups degrade gracefully (never raise) so the
scan flow never 500s.
"""
import logging
import re
import sqlite3
from collections.abc import Callable

import requests

import dedup
import identity
import igdb_match
import import_scraped
import models

log = logging.getLogger(__name__)

# Free UPCitemdb trial endpoint: ~100 lookups/day, no API key required.
UPCITEMDB_TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"
# Free UPCitemdb trial name-search: shares the ~100/day per-IP quota with /lookup.
UPCITEMDB_SEARCH_URL = "https://api.upcitemdb.com/prod/trial/search"
UPC_LOOKUP_TIMEOUT = 8  # seconds
MAX_CANDIDATES = 5

# Wikidata SPARQL: free, keyless, no rate limit. GTIN property P3962; video game Q7889.
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_GTIN_PROPERTY = "P3962"
WIKIDATA_VIDEO_GAME_CLASS = "Q7889"
# Wikidata stores GTIN-13/14 (zero-padded); a 12-digit UPC needs padded variants tried.
_GTIN_PAD_WIDTHS = (0, 1, 2)
_WIKIDATA_USER_AGENT = "GameTracker/1.0 (UPC enrichment; contact via app)"

# Last X-RateLimit-Remaining seen from a UPCitemdb call (shared trial quota), or None.
_last_rate_remaining: int | None = None

# ── Retail-title cleanup ──────────────────────────────────────────────────────
# UPCitemdb titles wrap the real game name in platform names, packaging, region,
# and genre boilerplate ("Mario Kart 8 Deluxe racing video game (Nintendo Switch)").
# IGDB's title search chokes on that noise and returns nothing, so we strip it
# before matching. Lists are extensible — add new boilerplate here, not in callers.
_RETAIL_NOISE_WORDS: tuple[str, ...] = (
    # Platforms (longest-first so "nintendo switch" wins over "switch")
    "nintendo switch 2", "nintendo switch", "switch",
    "playstation 5", "playstation 4", "playstation 3", "playstation",
    "ps5", "ps4", "ps3",
    "xbox series x|s", "xbox series x", "xbox series s", "xbox series",
    "xbox one", "xbox 360", "xbox",
    "nintendo wii u", "wii u", "nintendo wii", "wii",
    "nintendo 3ds", "3ds", "nintendo ds", "nintendo gamecube", "gamecube",
    "pc dvd", "windows pc", "pc",
    # Packaging / region / condition
    "standard edition", "u.s. version", "us version", "world edition",
    "north america", "region free", "brand new", "factory sealed", "sealed",
    "physical", "digital", "import", "ntsc", "pal",
)
# Standalone publisher/vendor words that UPC titles tack on ("... Nintendo 0454...").
# Stripped as whole words; extensible — add new publishers here.
_PUBLISHER_NOISE_WORDS: tuple[str, ...] = (
    "nintendo", "sony", "microsoft", "sega", "capcom", "square enix",
    "bandai namco", "ubisoft", "electronic arts", "activision", "konami", "atlus",
)
# Platform/packaging noise first (longest-first), then publishers.
_ALL_NOISE_WORDS: tuple[str, ...] = _RETAIL_NOISE_WORDS + _PUBLISHER_NOISE_WORDS
# Drops bracketed/parenthesised chunks: "(Nintendo Switch)", "[Physical]".
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
# Drops "video game" plus an optional preceding genre word ("racing video game").
_VIDEO_GAME_RE = re.compile(r"\b(?:\w+\s+)?video game\b", re.IGNORECASE)
# Noise words are stripped ONLY at the trailing edge (typical UPC DB shapes:
# "Game Title - Nintendo Switch", "Game Title Wii", trailing publisher), NEVER
# mid-title — names that CONTAIN a platform word ("Wii Sports", "Nintendo Switch
# Sports", "Wii Play") must survive intact. Bracketed platform chunks anywhere
# ("(Wii U)") are handled by _BRACKETS_RE above.
_TRAILING_NOISE_RE = re.compile(
    r"[\s\-–—:,]*\b(?:" + "|".join(re.escape(w) for w in _ALL_NOISE_WORDS)
    + r")\b\.?[\s\-–—:,]*$",
    re.IGNORECASE,
)
# Standalone catalog numbers / embedded UPCs (5+ digits). The floor preserves
# real title numbers like "1942" or "FIFA 23".
_CATALOG_NUM_RE = re.compile(r"\b\d{5,}\b")


def _strip_trailing_noise(t: str) -> str:
    """Peel noise words off the trailing edge, one at a time, stopping before the
    title would be emptied (a console UPC titled just "Nintendo Switch" stays)."""
    while True:
        stripped = _TRAILING_NOISE_RE.sub("", t)
        if stripped == t or not stripped.strip():
            return t
        t = stripped


# Condition/reseller/shorthand tokens SAFE to strip from the LEADING edge because
# no real game title starts with them ("NSW - Fantasian Neo Dimension - Nintendo
# Switch", "Refurbished Nintendo Super Mario Maker 2"). Deliberately excludes
# platform words (wii, switch, nintendo, ps5, xbox...) and "new" — those DO start
# real titles ("Wii Sports", "Nintendo Switch Sports", "New Super Mario Bros.")
# and stripping them would break those matches. Extensible — add new reseller/
# condition boilerplate here, never at call sites.
_LEADING_NOISE_WORDS: tuple[str, ...] = (
    "refurbished", "brand new", "factory sealed", "sealed", "used", "pre-owned",
    "preowned", "open box", "nib", "cib", "nsw", "ns",
)
# Mirrors _TRAILING_NOISE_RE but anchored at the start of the string.
_LEADING_NOISE_RE = re.compile(
    r"^[\s\-–—:,]*\b(?:" + "|".join(re.escape(w) for w in _LEADING_NOISE_WORDS)
    + r")\b\.?[\s\-–—:,]*",
    re.IGNORECASE,
)


def _strip_leading_noise(t: str) -> str:
    """Peel noise words off the leading edge, one at a time, stopping before the
    title would be emptied. Mirrors _strip_trailing_noise."""
    while True:
        stripped = _LEADING_NOISE_RE.sub("", t)
        if stripped == t or not stripped.strip():
            return t
        t = stripped


# Publisher tokens (from _PUBLISHER_NOISE_WORDS) matched ONLY at the leading edge,
# longest-first so "square enix" wins over any shorter partial alternative. Used by
# _strip_leading_publisher as a guarded, last-resort IGDB search fallback (see
# _scan_candidates) — never as part of clean_product_title's general cleanup,
# since a leading publisher can also be the start of a real title's own search hit.
_LEADING_PUBLISHER_RE = re.compile(
    r"^\b(?:" + "|".join(re.escape(w) for w in
                         sorted(_PUBLISHER_NOISE_WORDS, key=len, reverse=True))
    + r")\b\.?[\s\-–—:,]*",
    re.IGNORECASE,
)


def _strip_leading_publisher(title: str) -> str:
    """Remove a single leading publisher token ("Nintendo Super Mario Maker 2" ->
    "Super Mario Maker 2"), or return title unchanged when it doesn't start with
    one. Whole-word, case-insensitive, longest phrase first."""
    if not title:
        return title
    m = _LEADING_PUBLISHER_RE.match(title)
    if not m:
        return title
    rest = title[m.end():].strip(" -–—:")
    return rest or title


# Retail platform phrase (as it appears in UPC titles) -> app short_name. Longest
# phrases first so "nintendo switch" wins over "switch". Extensible.
RETAIL_PLATFORM_TO_SHORT: tuple[tuple[str, str], ...] = (
    ("nintendo switch 2", "Switch"), ("nintendo switch", "Switch"), ("switch", "Switch"),
    ("playstation 5", "PS5"), ("ps5", "PS5"),
    ("playstation 4", "PS4"), ("ps4", "PS4"),
    ("playstation 3", "PS3"), ("ps3", "PS3"),
    ("xbox series x|s", "Xbox"), ("xbox series x", "Xbox"), ("xbox series s", "Xbox"),
    ("xbox one", "Xbox"), ("xbox 360", "X360"), ("xbox", "Xbox"),
    ("wii u", "WiiU"), ("wii", "Wii"),
    ("nintendo 3ds", "3DS"), ("3ds", "3DS"), ("nintendo ds", "NDS"),
    ("gamecube", "GC"), ("nintendo 64", "N64"),
    ("pc", "PC"), ("windows", "PC"),
)


def parse_retail_platform(raw: str | None) -> str | None:
    """First platform named in a retail product title, mapped to an app short_name."""
    if not raw:
        return None
    low = raw.lower()
    for phrase, short in RETAIL_PLATFORM_TO_SHORT:
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            return short
    return None


# Some 3DS retail UPC titles append the platform as a bare "3D"/"3DS" suffix
# (e.g. "Theatrhythm Final Fantasy: Curtain Call 3D"), un-bracketed, which both
# hides the 3DS platform and poisons the IGDB title search. Matched ONLY as a
# trailing token so a mid-name "3D" (Super Mario 3D World) is left alone. A
# trailing "3D" can still be a real name (Ballz 3D, a Genesis game), so callers
# treat the 3DS read as a hint and fall back to the un-stripped title when the
# stripped search finds nothing.
_TRAILING_3DS_RE = re.compile(r"\s+3ds?\b\.?\s*$", re.IGNORECASE)


def split_trailing_platform(title: str | None) -> tuple[str | None, str]:
    """Split a bare trailing platform suffix the bracket-cleaner misses off a UPC
    title. Returns (short_name, title_without_suffix) — currently only the 3DS
    "3D"/"3DS" suffix — or (None, title) when there is none. Extend the lookup
    here (not at call sites) if another platform shows the same bare-suffix habit."""
    if not title:
        return None, title or ""
    m = _TRAILING_3DS_RE.search(title)
    if m:
        return "3DS", title[:m.start()].rstrip(" :-–—")
    return None, title


# Separator dashes only (space on both sides) — leaves intra-word hyphens like
# "Spider-Man" untouched.
_SEP_DASH_RE = re.compile(r"\s+[-–—]+\s+")


def clean_product_title(raw: str | None) -> str:
    """Strip retail/platform/packaging boilerplate from a UPC product title so it
    can be title-searched on IGDB. Preserves intra-word hyphens and inner colons.
    Platform words are removed only from brackets or the trailing edge, never
    mid-title ("Wii Sports" stays "Wii Sports").

    "Mario Kart 8 Deluxe racing video game (Nintendo Switch)" -> "Mario Kart 8 Deluxe"
    """
    if not raw:
        return ""
    t = _BRACKETS_RE.sub(" ", raw)
    t = _VIDEO_GAME_RE.sub(" ", t)
    t = _CATALOG_NUM_RE.sub(" ", t)    # numbers first, so a publisher becomes trailing
    t = _strip_trailing_noise(t)
    t = _strip_leading_noise(t)
    t = _SEP_DASH_RE.sub(" ", t)                 # collapse separator dashes
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t.strip(" -–—:").strip()    # trim stray leading/trailing seps


def lookup_product_title(upc: str, *, url: str = UPCITEMDB_TRIAL_URL,
                         timeout: int = UPC_LOOKUP_TIMEOUT) -> str | None:
    """Return the first product title for a UPC via UPCitemdb, or None.

    Network/parse failures are logged and degrade to None (never raise)."""
    try:
        resp = requests.get(url, params={"upc": upc}, timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get("items") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("UPC lookup failed for %s: %s", upc, exc)
        return None
    if not items:
        return None
    return (items[0].get("title") or "").strip() or None


def lookup_wikidata_gtin(upc: str, *, url: str = WIKIDATA_SPARQL_URL,
                         timeout: int = UPC_LOOKUP_TIMEOUT) -> str | None:
    """Return the English label of a video game whose GTIN equals the UPC, or None.

    Wikidata zero-pads GTINs, so the raw UPC plus 1-2 leading zeros are all tried.
    Free + keyless; degrades to None on any failure (never raises)."""
    variants = {("0" * w) + upc for w in _GTIN_PAD_WIDTHS}
    values = " ".join(f'"{v}"' for v in sorted(variants))
    query = (
        "SELECT ?item ?itemLabel WHERE { "
        f"VALUES ?gtin {{ {values} }} "
        f"?item wdt:{WIKIDATA_GTIN_PROPERTY} ?gtin . "
        f"?item wdt:P31/wdt:P279* wd:{WIKIDATA_VIDEO_GAME_CLASS} . "
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } LIMIT 1')
    try:
        resp = requests.get(url, params={"query": query, "format": "json"},
                            headers={"Accept": "application/sparql-results+json",
                                     "User-Agent": _WIKIDATA_USER_AGENT},
                            timeout=timeout)
        resp.raise_for_status()
        bindings = (resp.json().get("results") or {}).get("bindings") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("Wikidata GTIN lookup failed for %s: %s", upc, exc)
        return None
    if not bindings:
        return None
    label = ((bindings[0].get("itemLabel") or {}).get("value") or "").strip()
    return label or None


# Ordered product-title sources tried by resolve(); first non-empty hit wins.
# Append new free sources here — the chain is the extensibility seam.
PRODUCT_SOURCES: tuple[Callable[[str], str | None], ...] = (
    lookup_product_title, lookup_wikidata_gtin)


def _product_via_sources(upc: str) -> str | None:
    """Try each product source in order; return the first non-empty title, else None."""
    for source in PRODUCT_SOURCES:
        title = source(upc)
        if title:
            return title
    return None


def last_rate_remaining() -> int | None:
    """Last-seen UPCitemdb trial quota remaining (X-RateLimit-Remaining), or None."""
    return _last_rate_remaining


def search_products_by_name(query: str, *, url: str = UPCITEMDB_SEARCH_URL,
                            timeout: int = UPC_LOOKUP_TIMEOUT) -> list[dict] | None:
    """Return [{title, upc}, ...] for a UPCitemdb name-search, [] if the search
    succeeded with no products, or None if the call FAILED (network/HTTP/parse).
    Distinguishing None (failed) from [] (empty) lets the worker avoid recording a
    transient failure as a permanent no_match. Captures X-RateLimit-Remaining even
    on a 429 (header read before raise_for_status)."""
    global _last_rate_remaining
    try:
        resp = requests.get(url, params={"s": query}, timeout=timeout)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                _last_rate_remaining = int(remaining)
            except (TypeError, ValueError):
                _last_rate_remaining = None
        resp.raise_for_status()
        items = resp.json().get("items") or []
    except (requests.RequestException, ValueError) as exc:
        log.warning("UPC name-search failed for %r: %s", query, exc)
        return None
    out: list[dict] = []
    for it in items:
        upc = (it.get("upc") or "").strip()
        title = (it.get("title") or "").strip()
        if upc and title:
            out.append({"title": title, "upc": upc})
    return out


def registry_get(conn: sqlite3.Connection, upc: str) -> dict | None:
    """Return the cached mapping for a UPC, or None."""
    row = conn.execute(
        "SELECT upc, igdb_id, title, platform, game_id, cover_url "
        "FROM barcode_registry WHERE upc = ?",
        (upc,),
    ).fetchone()
    return dict(row) if row else None


def registry_put(conn: sqlite3.Connection, upc: str, *, igdb_id: int | None = None,
                 title: str | None = None, platform: str | None = None,
                 cover_url: str | None = None,
                 game_id: int | None = None) -> None:
    """Upsert a UPC -> game mapping (stamps confirmed_at).

    Every field is COALESCE-guarded on update: an incoming non-NULL value wins,
    an omitted (NULL) value preserves what a previous call confirmed — so a
    metadata-less re-put never degrades an existing registry row."""
    conn.execute(
        "INSERT INTO barcode_registry "
        "(upc, igdb_id, title, platform, cover_url, game_id, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(upc) DO UPDATE SET "
        "igdb_id=COALESCE(excluded.igdb_id, barcode_registry.igdb_id), "
        "title=COALESCE(excluded.title, barcode_registry.title), "
        "platform=COALESCE(excluded.platform, barcode_registry.platform), "
        "cover_url=COALESCE(excluded.cover_url, barcode_registry.cover_url), "
        "game_id=COALESCE(excluded.game_id, barcode_registry.game_id), "
        "confirmed_at=datetime('now')",
        (upc, igdb_id, title, platform, cover_url, game_id),
    )


def registry_upcs_for_game(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """All known UPC -> platform rows for a game (the per-platform UPC set)."""
    rows = conn.execute(
        "SELECT upc, platform FROM barcode_registry WHERE game_id = ? ORDER BY platform",
        (game_id,),
    ).fetchall()
    return [{"upc": r["upc"], "platform": r["platform"]} for r in rows]


def _owned_game_id(conn: sqlite3.Connection, title: str,
                   user_id: int = identity.OWNER_USER_ID) -> int | None:
    """id of an existing game owned by ``user_id`` whose stored match key equals
    the title's, else None.

    games.normalized_title stores normalize_title(clean_title(...)) — the
    import_scraped.match_key composition — so the same composed key is applied
    here; a bare normalize_title would miss titles clean_title changes (edition
    suffixes, leading region tags, ...). Scoped to ``user_id`` so ownership never
    crosses users (the shared barcode_registry cache is not scoped this way —
    only the derived owned_game_id is)."""
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM games WHERE user_id = ? AND normalized_title = ?",
        (user_id, import_scraped.match_key(title)),
    ).fetchone()
    return row["id"] if row else None


def owned_platforms_for(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    """Platforms the user owns this game on, with per-platform format + whether the
    platform has a digital market (drives the (Physical/Digital) display qualifier)."""
    rows = conn.execute(
        "SELECT p.short_name, gp.format, p.has_digital_market "
        "FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
        "WHERE gp.game_id = ? ORDER BY p.short_name",
        (game_id,),
    ).fetchall()
    return [{"short_name": r["short_name"], "format": r["format"],
             "has_digital_market": r["has_digital_market"]} for r in rows]


def _scan_candidates(search_title: str, fallback_title: str, platform_ids: set[int],
                     client_id: str, token: str) -> tuple[list[dict], bool]:
    """IGDB candidate ladder for a scan. Tries, in order, until one is non-empty:
      1. search_title restricted to the scanned platform (when one is known),
      2. search_title unrestricted (some valid IGDB entries have empty platform
         lists, so a platform-restricted search alone can drop the real game),
      3. fallback_title unrestricted — only when it differs from search_title
         (i.e. a trailing platform token was stripped) so a name that genuinely
         ends in a platform-like token (e.g. "Ballz 3D") still resolves.
      4. search_title with a leading publisher token stripped ("Nintendo Super
         Mario Maker 2" -> "Super Mario Maker 2") unrestricted — a LAST RESORT
         tried only when steps 1-3 all missed, so a title that already matches
         as-is ("Nintendo Switch Sports") never reaches this step.
    All steps drop fan/mod types. Returns (raw_candidates, used_platform_hint):
    used_platform_hint is False only when the match came from step 3, signalling
    the stripped-suffix platform guess was wrong."""
    if platform_ids:
        raw = igdb_match.candidates_for(search_title, platform_ids, None, client_id,
                                        token, drop_fan_types=True,
                                        restrict_to_platform=True)
        if raw:
            return raw, True
    raw = igdb_match.candidates_for(search_title, platform_ids, None, client_id, token,
                                    drop_fan_types=True, restrict_to_platform=False)
    if raw:
        return raw, True
    if fallback_title != search_title:
        raw = igdb_match.candidates_for(fallback_title, set(), None, client_id, token,
                                        drop_fan_types=True, restrict_to_platform=False)
        if raw:
            return raw, False
    publisher_stripped = _strip_leading_publisher(search_title)
    if publisher_stripped != search_title:
        raw = igdb_match.candidates_for(publisher_stripped, set(), None, client_id,
                                        token, drop_fan_types=True,
                                        restrict_to_platform=False)
        if raw:
            return raw, True
    return [], True


def _drop_edition_duplicates(raw: list[dict]) -> list[dict]:
    """Drop edition-SKU candidates (Deluxe / Limited / Digital Deluxe / Master /
    Collector's / ... Edition) when the plain base game is also a candidate, so a
    physical scan resolves to the base game instead of offering edition noise. An
    edition with no base sibling in the list is the only match and is kept. Reuses
    dedup's curated EDITION_QUALIFIERS so the two stay in sync."""
    keys = {models.normalize_title(c.get("name") or "") for c in raw}
    out = []
    for c in raw:
        key = models.normalize_title(c.get("name") or "")
        base = dedup.strip_edition_key(key)
        if base != key and base in keys:   # an edition whose base is also present
            continue
        out.append(c)
    return out


def resolve(conn: sqlite3.Connection, upc: str, *, client_id: str | None = None,
            token: str | None = None,
            user_id: int = identity.OWNER_USER_ID) -> dict:
    """Resolve a UPC to candidate games: cache -> UPC API -> IGDB match.

    ``barcode_registry`` (the cache) is a SHARED global UPC->identity mapping;
    ownership (owned_game_id/owned_platforms) is always derived fresh from
    ``user_id``'s own games, never from the registry's stored game_id, so two
    users scanning the same UPC each see only their own ownership.

    Returns {upc, source, scanned_platform, candidates[, product_title]}. Each candidate:
    {igdb_id, title, platform, cover_url, game_type, owned_game_id, owned_platforms}."""
    cached = registry_get(conn, upc)
    if cached:
        owned_id = _owned_game_id(conn, cached["title"] or "", user_id)
        return {"upc": upc, "source": "cache",
                "scanned_platform": cached["platform"], "candidates": [{
                    "igdb_id": cached["igdb_id"],
                    "title": cached["title"],
                    "platform": cached["platform"],
                    "cover_url": cached["cover_url"],
                    "game_type": None,
                    "owned_game_id": owned_id,
                    "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
                }]}

    product = _product_via_sources(upc)
    if not product:
        return {"upc": upc, "source": "none", "candidates": [], "scanned_platform": None}

    explicit_platform = parse_retail_platform(product)
    # Retail titles carry platform/packaging noise that defeats IGDB's title search;
    # match on (and prefill) the cleaned name. Fall back to raw if cleaning empties it.
    cleaned = clean_product_title(product) or product
    # A bare trailing platform suffix the bracket-cleaner can't see (3DS "...3D").
    hint_platform, stripped = split_trailing_platform(cleaned)
    scanned_platform = explicit_platform or hint_platform
    # Search the stripped name when the only platform read came from that suffix.
    search_title = stripped if (hint_platform and not explicit_platform) else cleaned
    platform_ids = igdb_match.platform_ids_for([scanned_platform]) if scanned_platform else set()

    # The platform reported with the result. When a match only comes from the
    # un-stripped fallback below, the trailing-token guess was wrong, so it is
    # cleared and each candidate keeps its own platform.
    result_platform = scanned_platform
    candidates: list[dict] = []
    if client_id and token:
        raw, used_hint = _scan_candidates(search_title, cleaned, platform_ids,
                                          client_id, token)
        raw = _drop_edition_duplicates(raw)
        if not used_hint:
            result_platform = None
        for c in raw[:MAX_CANDIDATES]:
            owned_id = _owned_game_id(conn, c.get("name") or "", user_id)
            shorts = igdb_match.short_names_for(c.get("platforms") or [])
            candidates.append({
                "igdb_id": c.get("igdb_id"),
                "title": c.get("name"),
                "platform": result_platform or (shorts[0] if shorts else None),
                "cover_url": c.get("cover_url"),
                "game_type": c.get("game_type"),
                "owned_game_id": owned_id,
                "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
            })

    if client_id and token:
        for cand in candidates:
            if cand.get("game_type") != igdb_match._BUNDLE_GAME_TYPE or not cand.get("igdb_id"):
                continue
            cons = []
            for k in igdb_match.bundle_constituents(cand["igdb_id"], client_id, token):
                owned_id = _owned_game_id(conn, k.get("name") or "", user_id)
                cons.append({
                    "title": k.get("name"),
                    "owned_game_id": owned_id,
                    "owned_platforms": owned_platforms_for(conn, owned_id) if owned_id else [],
                })
            cand["constituents"] = cons

    if not candidates:
        # Found a product name but no IGDB match: hand the cleaned title back so the
        # app can prefill manual search without the retail boilerplate.
        return {"upc": upc, "source": "upc_api", "candidates": [],
                "product_title": search_title, "scanned_platform": scanned_platform}
    return {"upc": upc, "source": "upc_api", "candidates": candidates,
            "scanned_platform": result_platform}
