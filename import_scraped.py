"""Load scraped vendor library JSON into games.db with rename-proof identity.

Match cascade per scraped game: exact (source, external_id) -> exact normalized
title -> fuzzy title (needs confirmation) -> new game. Never overwrites existing
user curation (status / rating / notes); only adds platform links and
external ids. Idempotent; supports --dry-run.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import bundles
import dlc_ownership
import models

logger = logging.getLogger(__name__)

FUZZY_MATCH_THRESHOLD = 0.85
DEFAULT_STATUS = "backlog"

# Per-user "not a game" exclusions (gitignored). The committed NON_GAME_APPS /
# NON_GAME_PATTERN below are the shared seed; this file is the manual layer.
EXCLUDED_GAMES_PATH = Path(__file__).parent / "excluded_games.json"

# Non-game library entries (PSN lists apps + add-ons alongside games). DLC is
# skipped for now; a future feature will attach it to its parent game.
NON_GAME_APPS = frozenset({
    "netflix", "hulu", "crunchyroll", "amazon prime video", "youtube",
    "spotify", "twitch", "disney plus", "disney", "max", "hbo max", "peacock",
    "funimation", "pluto tv", "apple tv", "plex", "wwe network", "media player",
    "vidzone", "littlstar", "littlstar cinema", "live events viewer",
    "sharefactory", "headset companion app", "within", "ps@e3",
})
# Word-boundaried so real titles aren't caught ("Demon's Souls", "Alpha
# Protocol", "Jackbox Party Pack" all stay).
NON_GAME_PATTERN = re.compile(
    r"\b(demo|beta|trial version|trial edition|soundtrack|artbook|art book|"
    r"bonus content|unlock key|season pass|dlc|expansion pass|wallpaper)\b"
    r"|the art of |first look alpha|episode duscae|edition upgrade",
    re.IGNORECASE,
)


def _clean_for_match(title: str) -> str:
    cleaned = (title or "").replace("™", "").replace("®", "").replace("©", "")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def is_non_game(title: str) -> bool:
    """True for apps / demos / DLC-style entries that aren't base games."""
    if _clean_for_match(title) in NON_GAME_APPS:
        return True
    return bool(NON_GAME_PATTERN.search(title or ""))

# Display names for platform rows created on the fly (short_name -> name).
PLATFORM_DISPLAY_NAMES = {
    "PS5": "PlayStation 5", "PS4": "PlayStation 4", "PS3": "PlayStation 3",
    "Vita": "PlayStation Vita", "PSP": "PlayStation Portable",
    "Xbox": "Xbox", "X360": "Xbox 360", "OGXbox": "Xbox (original)",
    "Switch": "Nintendo Switch", "WiiU": "Wii U", "3DS": "Nintendo 3DS",
}


def match_key(title: str) -> str:
    """Title normalization for matching (mirrors how migrate_db stores it)."""
    return models.normalize_title(models.clean_title(title))


def load_excluded_games() -> list[dict]:
    """Load the per-user 'not a game' exclusion list (gitignored). [] if absent."""
    try:
        with open(EXCLUDED_GAMES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def add_excluded_games(entries: list[dict]) -> int:
    """Append exclusion entries, deduped on (source, external_id, normalized_title).

    Each entry: {source, external_id, normalized_title, title}. Returns how many
    new entries were written (0 leaves the file untouched).
    """
    existing = load_excluded_games()
    seen = {(e.get("source"), e.get("external_id"), e.get("normalized_title")) for e in existing}
    added = 0
    for entry in entries:
        key = (entry.get("source"), entry.get("external_id"), entry.get("normalized_title"))
        if key in seen:
            continue
        seen.add(key)
        existing.append(entry)
        added += 1
    if added:
        with open(EXCLUDED_GAMES_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    return added


@dataclass(frozen=True)
class ExclusionIndex:
    """Set-based view of the exclusion list for O(1) per-game membership checks."""
    by_external_id: frozenset  # of (source, external_id) tuples
    by_title: frozenset        # of normalized titles


def build_exclusion_index(entries: Optional[list[dict]] = None) -> ExclusionIndex:
    """Index the exclusion list (loaded once when not supplied) for fast lookups."""
    if entries is None:
        entries = load_excluded_games()
    return ExclusionIndex(
        by_external_id=frozenset((e.get("source"), e.get("external_id"))
                                 for e in entries if e.get("external_id")),
        by_title=frozenset(e.get("normalized_title") for e in entries
                           if e.get("normalized_title") is not None),
    )


def is_excluded(source: Optional[str], external_id: Optional[str], title: str,
                index: Optional[ExclusionIndex] = None) -> bool:
    """True if a scraped row was manually marked 'not a game'.

    Matches by exact (source, external_id) when the row carries an external id,
    and always by exact normalized title — so the same non-game is skipped even if
    its store id changes or it has none. Keys are exact, so a real game is never
    caught unless it was explicitly excluded. Callers checking many rows should
    pass a prebuilt `index` (build_exclusion_index) so the exclusion file is read
    once per run instead of once per row.
    """
    if index is None:
        index = build_exclusion_index()
    if external_id and (source, external_id) in index.by_external_id:
        return True
    return match_key(title) in index.by_title


@dataclass
class MatchResult:
    game_id: Optional[int]
    method: str               # "external_id" | "title" | "fuzzy" | "new"
    score: float = 1.0
    matched_title: Optional[str] = None


def resolve_game(conn: sqlite3.Connection, source: str, external_id: Optional[str],
                 title: str) -> MatchResult:
    """Resolve a scraped game to an existing game_id via the match cascade."""
    if external_id:
        row = conn.execute(
            "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        if row:
            return MatchResult(row[0], "external_id")

    key = match_key(title)
    row = conn.execute("SELECT id FROM games WHERE normalized_title = ?", (key,)).fetchone()
    if row:
        return MatchResult(row[0], "title")

    best = MatchResult(None, "new", 0.0, None)
    for g in conn.execute("SELECT id, title, normalized_title FROM games").fetchall():
        score = difflib.SequenceMatcher(None, key, g["normalized_title"]).ratio()
        if score > best.score:
            best = MatchResult(g["id"], "fuzzy", score, g["title"])
    if best.score >= FUZZY_MATCH_THRESHOLD:
        return best
    return MatchResult(None, "new", best.score, best.matched_title)


@dataclass
class ImportStats:
    new_games: int = 0
    external_id_matches: int = 0
    title_matches: int = 0
    fuzzy_confirmed: int = 0
    fuzzy_rejected: int = 0
    platform_links_added: int = 0
    external_ids_added: int = 0
    ratings_created: int = 0
    skipped_non_games: int = 0
    skipped_excluded: int = 0
    bundles_expanded: int = 0
    platforms_created: list[tuple[str, str]] = field(default_factory=list)
    fuzzy_candidates: list[tuple[str, str, float]] = field(default_factory=list)

    def merge(self, other: "ImportStats") -> None:
        self.new_games += other.new_games
        self.external_id_matches += other.external_id_matches
        self.title_matches += other.title_matches
        self.fuzzy_confirmed += other.fuzzy_confirmed
        self.fuzzy_rejected += other.fuzzy_rejected
        self.platform_links_added += other.platform_links_added
        self.external_ids_added += other.external_ids_added
        self.ratings_created += other.ratings_created
        self.skipped_non_games += other.skipped_non_games
        self.skipped_excluded += other.skipped_excluded
        self.bundles_expanded += other.bundles_expanded
        self.platforms_created += other.platforms_created
        self.fuzzy_candidates += other.fuzzy_candidates


def _create_game(conn: sqlite3.Connection, game: dict) -> int:
    display = models.clean_title(game["title"])
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title, cover_url) VALUES (?, ?, ?)",
        (display, models.normalize_title(display), game.get("cover_url")),
    )
    return cur.lastrowid


def _apply_or_plan(conn: sqlite3.Connection, game_id: Optional[int], game: dict, source: str,
                   stats: ImportStats, *, dry_run: bool, is_new: bool) -> None:
    """Add platform link + external id + default rating. Read-only when dry_run.

    When is_new, the game has no existing rows, so every sub-add is new (and the
    game_id may be None during a dry run — never queried in that case).
    """
    short = game["platform"]

    prow = conn.execute("SELECT id FROM platforms WHERE short_name = ?", (short,)).fetchone()
    if prow:
        platform_id = prow[0]
    else:
        stats.platforms_created.append((short, models.classify_platform(short)))
        if dry_run:
            platform_id = None
        else:
            name = PLATFORM_DISPLAY_NAMES.get(short, short)
            platform_id = conn.execute(
                "INSERT INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
                (name, short, models.classify_platform(short)),
            ).lastrowid

    link_is_new = is_new or platform_id is None or conn.execute(
        "SELECT 1 FROM game_platforms WHERE game_id = ? AND platform_id = ?",
        (game_id, platform_id),
    ).fetchone() is None
    if link_is_new:
        stats.platform_links_added += 1
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                (game_id, platform_id),
            )

    ext = game.get("external_id")
    if ext:
        ext_is_new = is_new or conn.execute(
            "SELECT 1 FROM game_external_ids WHERE source = ? AND external_id = ?",
            (source, ext),
        ).fetchone() is None
        if ext_is_new:
            stats.external_ids_added += 1
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO game_external_ids "
                    "(game_id, source, external_id, source_title) VALUES (?, ?, ?, ?)",
                    (game_id, source, ext, game.get("source_title") or game["title"]),
                )

    rating_is_new = is_new or conn.execute(
        "SELECT 1 FROM user_ratings WHERE game_id = ?", (game_id,)
    ).fetchone() is None
    if rating_is_new:
        stats.ratings_created += 1
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO user_ratings (game_id, status) VALUES (?, ?)",
                (game_id, DEFAULT_STATUS),
            )


def _interactive_confirm(scraped: str, existing: str, score: float) -> bool:
    answer = input(f"  Merge '{scraped}' into existing '{existing}' "
                   f"(similarity {score:.2f})? [y/N] ")
    return answer.strip().lower() == "y"


def _auto_confirm(scraped: str, existing: str, score: float) -> bool:
    """confirm_fn that accepts every fuzzy match (used by --accept-fuzzy)."""
    return True


def _safe_auto_confirm(scraped: str, existing: str, score: float) -> bool:
    """Auto-confirm only when titles differ solely by spacing/punctuation
    (e.g. 'NieR:Automata' vs 'Nier: Automata'); reject real differences like
    'Final Fantasy XV' vs 'Final Fantasy XIV'."""
    return match_key(scraped).replace(" ", "") == match_key(existing).replace(" ", "")


def import_games(conn: sqlite3.Connection, games: list[dict], source: str, *,
                 dry_run: bool = False, skip_non_games: bool = True,
                 confirm_fn: Callable[[str, str, float], bool] = _interactive_confirm,
                 progress: Callable[[int, int | None, int | None], None] | None = None) -> ImportStats:
    """Reconcile a list of scraped game dicts into the DB. Returns stats."""
    stats = ImportStats()
    # Normalized keys of games created earlier in THIS call. In a dry run the new
    # rows aren't inserted, so without this a same-batch duplicate title would be
    # miscounted as another "new" game; a real run unifies them via the DB.
    batch_new_keys: set[str] = set()
    excluded = build_exclusion_index()  # one file read per run, not per game
    done = 0
    for game in games:
        if is_excluded(source, game.get("external_id"), game["title"], excluded):
            stats.skipped_excluded += 1
            done += 1
            if progress:
                progress(done, len(games), stats.new_games)
            continue
        # Curated bundles are authoritative, so expand them BEFORE the name-based
        # is_non_game heuristic. A Nintendo 7007 game-bundle's title legitimately
        # contains a DLC keyword (e.g. "... Expansion Pass Bundle"); checking the
        # heuristic first wrongly dropped such owned games before they could be
        # expanded into their base game(s).
        constituents = bundles.expand_bundle(source, game.get("external_id"))
        if constituents is not None:
            # Owning the bundle = owning its games; import each, never the phantom.
            stats.bundles_expanded += 1
            for title in constituents:
                _import_one(conn, _constituent_game(game, title, source), source, stats,
                            batch_new_keys, dry_run=dry_run, confirm_fn=confirm_fn)
            done += 1
            if progress:
                progress(done, len(games), stats.new_games)
            continue
        if skip_non_games and is_non_game(game["title"]):
            stats.skipped_non_games += 1
            done += 1
            if progress:
                progress(done, len(games), stats.new_games)
            continue
        _import_one(conn, game, source, stats, batch_new_keys,
                    dry_run=dry_run, confirm_fn=confirm_fn)
        done += 1
        if progress:
            progress(done, len(games), stats.new_games)
    return stats


def _constituent_game(bundle_game: dict, title: str, source: str) -> dict:
    """Synthesize a scraped-game dict for one constituent of a bundle.

    Inherits the bundle's platform; carries no external id (the constituent is
    identified by title, and the bundle's own id is intentionally dropped).
    """
    return {"title": title, "platform": bundle_game.get("platform"), "source": source,
            "external_id": None, "cover_url": None, "source_title": title}


def _import_one(conn: sqlite3.Connection, game: dict, source: str, stats: ImportStats,
                batch_new_keys: set[str], *, dry_run: bool,
                confirm_fn: Callable[[str, str, float], bool]) -> None:
    """Reconcile a single scraped game through the match cascade (mutates stats)."""
    m = resolve_game(conn, source, game.get("external_id"), game["title"])
    is_new = False

    if m.method == "external_id":
        stats.external_id_matches += 1
        game_id = m.game_id
    elif m.method == "title":
        stats.title_matches += 1
        game_id = m.game_id
    elif m.method == "fuzzy":
        stats.fuzzy_candidates.append((game["title"], m.matched_title, round(m.score, 3)))
        if dry_run:
            return
        if confirm_fn(game["title"], m.matched_title, m.score):
            stats.fuzzy_confirmed += 1
            game_id = m.game_id
        else:
            stats.fuzzy_rejected += 1
            stats.new_games += 1
            is_new = True
            game_id = _create_game(conn, game)
            batch_new_keys.add(match_key(game["title"]))
    else:  # new
        key = match_key(game["title"])
        if dry_run and key in batch_new_keys:
            # A real run would unify this with the earlier same-batch new game.
            stats.title_matches += 1
            return
        batch_new_keys.add(key)
        stats.new_games += 1
        is_new = True
        game_id = None if dry_run else _create_game(conn, game)

    _apply_or_plan(conn, game_id, game, source, stats, dry_run=dry_run, is_new=is_new)


# Curation fields whose listed values mean "untouched by the user" (uncurated).
_DEFAULT_CURATION = {
    "status": ("backlog", "", None), "rating": (None,), "notes": ("", None),
    "started_at": (None,), "completed_at": (None,),
    "sort_order": (None,), "hours_played": (0, 0.0, None), "priority": (5, None),
}


def _is_curated(conn: sqlite3.Connection, game_id: int) -> bool:
    """True if the user has touched this row (any curation field off its default)."""
    row = conn.execute(
        "SELECT status, rating, notes, started_at, completed_at, "
        "sort_order, hours_played, priority FROM user_ratings WHERE game_id = ?",
        (game_id,)).fetchone()
    if not row:
        return False
    return any(row[field] not in defaults for field, defaults in _DEFAULT_CURATION.items())


def _resolve_constituent_ids(conn: sqlite3.Connection,
                             constituents: tuple[str, ...]) -> list[int]:
    """game_ids for constituents that exist (matched by normalized title).

    Missing titles are omitted; in a real cleanup run import_games has already
    created them, in a dry run a not-yet-created constituent simply has nothing to
    migrate onto.
    """
    ids: list[int] = []
    for title in constituents:
        row = conn.execute("SELECT id FROM games WHERE normalized_title = ?",
                           (match_key(title),)).fetchone()
        if row:
            ids.append(row[0])
    return ids


# Phantom curation that cannot be meaningfully split across several constituents;
# its presence blocks auto-delete (the phantom is kept for manual handling).
_AMBIGUOUS_FIELDS = ("rating", "notes", "hours_played")


def _migrate_bundle_curation(conn: sqlite3.Connection, bundle_id: int,
                             constituent_ids: list[int], *, dry_run: bool) -> dict:
    """Fill-only migrate the phantom's curation onto its constituents.

    Copies status (+ started/completed) onto each constituent still at its default,
    never overwriting curation the user already set. If the phantom carries a
    non-default rating/notes/hours_played, migrates nothing and returns
    {"ambiguous": True} so the caller keeps the phantom. Writes nothing when
    dry_run; the returned report stays accurate for already-existing constituents.
    """
    row = conn.execute(
        "SELECT status, rating, notes, started_at, completed_at, hours_played "
        "FROM user_ratings WHERE game_id = ?", (bundle_id,)).fetchone()
    report: dict = {"ambiguous": False, "status": None}
    if not row:
        return report
    if any(row[f] not in _DEFAULT_CURATION[f] for f in _AMBIGUOUS_FIELDS):
        return {"ambiguous": True, "status": None}

    if row["status"] not in _DEFAULT_CURATION["status"]:
        for cid in constituent_ids:
            cur = conn.execute("SELECT status FROM user_ratings WHERE game_id = ?",
                               (cid,)).fetchone()
            if cur is None or cur["status"] in _DEFAULT_CURATION["status"]:
                report["status"] = row["status"]
                if not dry_run:
                    conn.execute(
                        "INSERT INTO user_ratings (game_id, status, started_at, completed_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(game_id) DO UPDATE SET "
                        "status = excluded.status, started_at = excluded.started_at, "
                        "completed_at = excluded.completed_at, updated_at = CURRENT_TIMESTAMP",
                        (cid, row["status"], row["started_at"], row["completed_at"]))
    return report


def cleanup_bundles(conn: sqlite3.Connection, *, dry_run: bool = False,
                    include_curated: bool = False,
                    confirm_fn: Callable[[str, str, float], bool] = _safe_auto_confirm
                    ) -> list[dict]:
    """One-time pass: expand every known bundle that exists in the DB as a phantom.

    For each mapped (source, external_id) present: ensure its constituents exist
    and are owned on the bundle's platform(s), then delete the phantom row. An
    uncurated phantom is deleted outright. A curated phantom is kept (reported
    `kept_curated`) UNLESS include_curated is set, in which case its curation is
    migrated onto its constituents (fill-only) and the phantom deleted
    (`migrated_deleted`); a phantom carrying un-splittable curation
    (rating/notes/hours) is kept and reported `kept_ambiguous`. Honors dry_run.
    """
    results: list[dict] = []
    for (source, external_id), constituents in bundles.BUNDLE_CONTENTS.items():
        row = conn.execute(
            "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
            (source, external_id)).fetchone()
        if not row:
            continue
        bundle_id = row[0]
        bundle = conn.execute("SELECT title FROM games WHERE id = ?", (bundle_id,)).fetchone()
        if not bundle:
            continue
        platforms = [r[0] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
            "WHERE gp.game_id = ?", (bundle_id,))] or [None]
        synth = [_constituent_game({"platform": pf}, title, source)
                 for title in constituents for pf in platforms]
        stats = import_games(conn, synth, source, dry_run=dry_run, confirm_fn=confirm_fn)
        curated = _is_curated(conn, bundle_id)
        migrated: dict = {}
        if not curated:
            action = "deleted"
            if not dry_run:
                conn.execute("DELETE FROM games WHERE id = ?", (bundle_id,))
        elif include_curated:
            ids = _resolve_constituent_ids(conn, constituents)
            migrated = _migrate_bundle_curation(conn, bundle_id, ids, dry_run=dry_run)
            if migrated["ambiguous"]:
                action = "kept_ambiguous"
            else:
                action = "migrated_deleted"
                if not dry_run:
                    conn.execute("DELETE FROM games WHERE id = ?", (bundle_id,))
        else:
            action = "kept_curated"
        results.append({"bundle_id": bundle_id, "title": bundle[0],
                        "source": source, "external_id": external_id,
                        "action": action, "constituents_created": stats.new_games,
                        "migrated": migrated})
    if not dry_run:
        conn.commit()
    return results


_VALID_BUNDLE_TYPES = ("compilation", "entitlement", "anthology")


def apply_bundle_catalog(conn: sqlite3.Connection, *, dry_run: bool = False,
                         only_titles: set[str] | None = None) -> list[dict]:
    """Expand catalogued multi-game products that exist in the DB. Catalog-keyed
    sibling of cleanup_bundles (which is keyed by vendor id).

    only_titles limits the pass to those normalized-title catalog keys — the
    runtime IGDB fallback expands just the bundle it resolved through this same
    path, instead of re-walking the whole catalog.

    For each entry in models.load_bundle_catalog() whose parent (matched by
    normalized_title) is owned:
      - anthology  -> no-op (kept; A2 handles its contents list);
      - compilation/entitlement -> ensure constituents exist on the parent's
        platform(s) (reusing the importer), migrate the parent's curation fill-only,
        then delete the parent (uncurated -> 'deleted'; curated+splittable ->
        'migrated_deleted'; un-splittable rating/notes/hours -> 'kept_ambiguous').
      - compilation -> additionally stamp collection_name = parent title on each
        constituent (the badge + 'Part of X' cue). entitlement constituents stay plain.
    Idempotent (a removed parent is skipped next run). Honors dry_run. Returns a report.
    """
    catalog = models.load_bundle_catalog()
    results: list[dict] = []
    for norm_title, entry in catalog.items():
        if only_titles is not None and norm_title not in only_titles:
            continue
        ptype = entry.get("type")
        if ptype not in _VALID_BUNDLE_TYPES:
            continue  # unknown type: never guessed
        parent = conn.execute(
            "SELECT id, title FROM games WHERE normalized_title = ?", (norm_title,)).fetchone()
        if not parent:
            continue
        parent_id, parent_title = parent["id"], parent["title"]
        if ptype == "anthology":
            results.append({"title": parent_title, "type": "anthology", "action": "kept",
                            "constituents_created": 0, "migrated": {}})
            continue

        constituents = tuple(entry.get("constituents") or ())
        platforms = [r[0] for r in conn.execute(
            "SELECT p.short_name FROM game_platforms gp JOIN platforms p ON p.id = gp.platform_id "
            "WHERE gp.game_id = ?", (parent_id,))] or [None]
        synth = [_constituent_game({"platform": pf}, title, "catalog")
                 for title in constituents for pf in platforms]
        stats = import_games(conn, synth, "catalog", dry_run=dry_run, confirm_fn=_safe_auto_confirm)

        ids = _resolve_constituent_ids(conn, constituents)
        if ptype == "compilation" and not dry_run:
            for cid in ids:
                conn.execute("UPDATE games SET collection_name = ? WHERE id = ?",
                             (parent_title, cid))

        curated = _is_curated(conn, parent_id)
        migrated: dict = {}
        if not curated:
            action = "deleted"
            if not dry_run:
                conn.execute("DELETE FROM games WHERE id = ?", (parent_id,))
        else:
            migrated = _migrate_bundle_curation(conn, parent_id, ids, dry_run=dry_run)
            if migrated["ambiguous"]:
                action = "kept_ambiguous"
            else:
                action = "migrated_deleted"
                if not dry_run:
                    conn.execute("DELETE FROM games WHERE id = ?", (parent_id,))
        results.append({"title": parent_title, "type": ptype, "action": action,
                        "constituents_created": stats.new_games, "migrated": migrated})
    if not dry_run:
        conn.commit()
    return results


def run_dlc_enrichment(conn: sqlite3.Connection,
                       progress: Callable[[int, int | None, int | None], None] | None = None) -> Optional[dict]:
    """Enrich never-enriched games with IGDB DLC. Returns totals, or None if no
    Twitch credentials are configured (enrichment skipped)."""
    import config
    import igdb_dlc
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        logger.info("DLC enrich skipped (no Twitch credentials in config.json)")
        return None
    token = igdb_dlc.get_access_token(client_id, secret)
    totals = igdb_dlc.enrich_missing(conn, client_id=client_id, token=token,
                                     progress=progress)
    # Backfill genres for games enriched before genre fetching existed (and any
    # left without genre tags), so every scrape fills in what's missing.
    tagged = igdb_dlc.backfill_genres(conn, client_id=client_id, token=token, progress=progress)
    totals["genres_tagged"] = tagged
    logger.info("DLC enrich: %d games, %d matched, +%d dlc, %d errors; +%d genre-tagged",
                totals["games"], totals["matched"], totals["added"], totals["errors"], tagged)
    return totals


def _iter_json_paths(paths: Sequence[str]) -> Iterator[Path]:
    for p in paths:
        path = Path(p)
        if path.is_dir():
            yield from sorted(path.glob("*.json"))
        else:
            yield path


def _log_summary(total: ImportStats, *, dry_run: bool) -> None:
    label = "WOULD CHANGE (dry run)" if dry_run else "CHANGED"
    logger.info("--- %s ---", label)
    logger.info("new games:          %d", total.new_games)
    logger.info("matched by id:      %d", total.external_id_matches)
    logger.info("matched by title:   %d", total.title_matches)
    logger.info("platform links:     +%d", total.platform_links_added)
    logger.info("external ids:       +%d", total.external_ids_added)
    logger.info("default ratings:    +%d", total.ratings_created)
    if total.skipped_non_games:
        logger.info("skipped non-games:  %d", total.skipped_non_games)
    if total.skipped_excluded:
        logger.info("skipped excluded:   %d", total.skipped_excluded)
    if total.bundles_expanded:
        logger.info("bundles expanded:   %d", total.bundles_expanded)
    if total.fuzzy_confirmed or total.fuzzy_rejected:
        logger.info("fuzzy merged/new:   %d / %d", total.fuzzy_confirmed, total.fuzzy_rejected)
    if total.platforms_created:
        logger.info("new platform rows:  %s", sorted(set(total.platforms_created)))
    if total.fuzzy_candidates:
        logger.info("FUZZY — needs your review (%d):", len(total.fuzzy_candidates))
        for scraped, existing, score in total.fuzzy_candidates:
            logger.info("  '%s'  ~  '%s'  (%.2f)", scraped, existing, score)


def _log_ownership(report: "dlc_ownership.OwnershipReport", *, dry_run: bool) -> None:
    label = "WOULD MARK (dry run)" if dry_run else "MARKED"
    logger.info("--- DLC OWNERSHIP (%s) ---", label)
    logger.info("created:            %d", report.created)
    logger.info("reconciled:         %d", report.reconciled)
    logger.info("already owned:      %d", report.already_owned)
    logger.info("needs review:       %d", len(report.review))
    for m in report.review:
        logger.info("  REVIEW     '%s'  [%s]", m.addon_title, m.reason)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Import scraped library JSON into games.db")
    parser.add_argument("paths", nargs="*", help="JSON files or a directory of them (e.g. scraped)")
    parser.add_argument("--dry-run", action="store_true", help="preview changes; write nothing")
    parser.add_argument("--cleanup-bundles", action="store_true",
                        help="expand known phantom bundles already in the DB, then exit")
    parser.add_argument("--apply-bundle-catalog", action="store_true",
                        help="expand catalogued compilations/entitlement bundles "
                             "(bundle_catalog.json) already in the DB, then exit")
    parser.add_argument("--include-curated", action="store_true",
                        help="with --cleanup-bundles: also migrate curated phantoms' "
                             "status onto constituents, then delete them")
    parser.add_argument("--accept-fuzzy", action="store_true",
                        help="auto-confirm ALL fuzzy matches")
    parser.add_argument("--auto-fuzzy", action="store_true",
                        help="auto-confirm only spacing/punctuation renames; reject the rest")
    parser.add_argument("--keep-non-games", action="store_true",
                        help="do not skip apps / demos / DLC-style entries")
    parser.add_argument("--no-dlc", action="store_true",
                        help="skip DLC enrichment after import (IGDB + Steam catalogue)")
    parser.add_argument("--no-ownership", action="store_true",
                        help="skip scrape-driven DLC ownership matching after enrichment")
    args = parser.parse_args(argv)

    models.migrate_db()  # ensure schema (incl. game_external_ids, dlc_external_ids) is current
    conn = models.get_db()

    if args.cleanup_bundles:
        report = cleanup_bundles(conn, dry_run=args.dry_run,
                                 include_curated=args.include_curated)
        for r in report:
            extra = ""
            mig = r.get("migrated") or {}
            if mig.get("status"):
                extra += f" status={mig['status']}"
            logger.info("%s: %s (+%d constituents)%s [%s]", r["title"], r["action"],
                        r["constituents_created"], extra, f"{r['source']}/{r['external_id']}")
        logger.info("DRY RUN — no changes written." if args.dry_run
                    else "bundles processed: %d" % len(report))
        conn.close()
        return

    if args.apply_bundle_catalog:
        report = apply_bundle_catalog(conn, dry_run=args.dry_run)
        for r in report:
            logger.info("%s: %s (+%d constituents) [%s]", r["title"], r["action"],
                        r["constituents_created"], r["type"])
        logger.info("DRY RUN — no changes written." if args.dry_run
                    else "bundle-catalog entries processed: %d" % len(report))
        conn.close()
        return

    if not args.paths:
        parser.error("paths are required unless --cleanup-bundles or --apply-bundle-catalog is given")

    if args.accept_fuzzy:
        confirm = _auto_confirm
    elif args.auto_fuzzy:
        confirm = _safe_auto_confirm
    else:
        confirm = _interactive_confirm

    total = ImportStats()
    all_addons: list[dict] = []
    for path in _iter_json_paths(args.paths):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data["games"]
        games_only = [g for g in rows if g.get("kind", "game") == "game"]
        all_addons.extend(g for g in rows if g.get("kind") == "addon")
        stats = import_games(conn, games_only, data["source"], dry_run=args.dry_run,
                             skip_non_games=not args.keep_non_games, confirm_fn=confirm)
        total.merge(stats)
        logger.info("%s (%s): +%d new, %d id, %d title, %d fuzzy, %d add-ons",
                    Path(path).name, data["source"], stats.new_games,
                    stats.external_id_matches, stats.title_matches,
                    len(stats.fuzzy_candidates),
                    sum(1 for g in rows if g.get("kind") == "addon"))

    if args.dry_run:
        logger.info("DRY RUN — no changes written.")
    else:
        conn.commit()
    _log_summary(total, dry_run=args.dry_run)
    steam_addons = [a for a in all_addons if a.get("source") == "steam"]
    other_addons = [a for a in all_addons if a.get("source") != "steam"]

    if not args.dry_run and not args.no_dlc:
        run_dlc_enrichment(conn)                       # skips steam (vendor catalogue)
        import steam_dlc
        owned_app_ids = {int(a["external_id"]) for a in steam_addons if a.get("external_id")}
        sr = steam_dlc.enrich_and_mark(conn, owned_app_ids)
        conn.commit()
        if sr.games:
            logger.info("STEAM DLC: %d games, +%d catalogue, %d owned marked, %d errors",
                        sr.games, sr.catalogue_added, sr.owned_marked, sr.errors)
    if not args.no_ownership and other_addons:
        if args.dry_run:
            logger.info("(dry run skipped DLC enrichment, so ownership preview "
                        "omits not-yet-imported games)")
        report = dlc_ownership.mark_ownership(conn, other_addons, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
        _log_ownership(report, dry_run=args.dry_run)
    conn.close()


if __name__ == "__main__":
    main()
