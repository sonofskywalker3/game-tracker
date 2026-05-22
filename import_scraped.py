"""Load scraped vendor library JSON into games.db with rename-proof identity.

Match cascade per scraped game: exact (source, external_id) -> exact normalized
title -> fuzzy title (needs confirmation) -> new game. Never overwrites existing
user curation (status / rating / notes / series); only adds platform links and
external ids. Idempotent; supports --dry-run.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import models

logger = logging.getLogger(__name__)

FUZZY_MATCH_THRESHOLD = 0.85
DEFAULT_STATUS = "backlog"

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


@dataclass
class MatchResult:
    game_id: Optional[int]
    method: str               # "external_id" | "title" | "fuzzy" | "new"
    score: float = 1.0
    matched_title: Optional[str] = None


def resolve_game(conn, source: str, external_id: Optional[str], title: str) -> MatchResult:
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
    platforms_created: list = field(default_factory=list)
    fuzzy_candidates: list = field(default_factory=list)  # (scraped, existing, score)

    def merge(self, other: "ImportStats") -> None:
        self.new_games += other.new_games
        self.external_id_matches += other.external_id_matches
        self.title_matches += other.title_matches
        self.fuzzy_confirmed += other.fuzzy_confirmed
        self.fuzzy_rejected += other.fuzzy_rejected
        self.platform_links_added += other.platform_links_added
        self.external_ids_added += other.external_ids_added
        self.ratings_created += other.ratings_created
        self.platforms_created += other.platforms_created
        self.fuzzy_candidates += other.fuzzy_candidates


def _create_game(conn, game: dict) -> int:
    display = models.clean_title(game["title"])
    cur = conn.execute(
        "INSERT INTO games (title, normalized_title, cover_url) VALUES (?, ?, ?)",
        (display, models.normalize_title(display), game.get("cover_url")),
    )
    return cur.lastrowid


def _apply_or_plan(conn, game_id, game, source, stats, *, dry_run, is_new) -> None:
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


def import_games(conn, games: list[dict], source: str, *, dry_run: bool = False,
                 confirm_fn: Callable[[str, str, float], bool] = _interactive_confirm) -> ImportStats:
    """Reconcile a list of scraped game dicts into the DB. Returns stats."""
    stats = ImportStats()
    for game in games:
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
                continue
            if confirm_fn(game["title"], m.matched_title, m.score):
                stats.fuzzy_confirmed += 1
                game_id = m.game_id
            else:
                stats.fuzzy_rejected += 1
                stats.new_games += 1
                is_new = True
                game_id = _create_game(conn, game)
        else:  # new
            stats.new_games += 1
            is_new = True
            game_id = None if dry_run else _create_game(conn, game)

        _apply_or_plan(conn, game_id, game, source, stats, dry_run=dry_run, is_new=is_new)
    return stats


def _iter_json_paths(paths: list[str]):
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
    if total.platforms_created:
        logger.info("new platform rows:  %s", total.platforms_created)
    if total.fuzzy_candidates:
        logger.info("FUZZY — needs your review (%d):", len(total.fuzzy_candidates))
        for scraped, existing, score in total.fuzzy_candidates:
            logger.info("  '%s'  ~  '%s'  (%.2f)", scraped, existing, score)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Import scraped library JSON into games.db")
    parser.add_argument("paths", nargs="+", help="JSON files or a directory of them (e.g. scraped)")
    parser.add_argument("--dry-run", action="store_true", help="preview changes; write nothing")
    parser.add_argument("--accept-fuzzy", action="store_true",
                        help="auto-confirm fuzzy matches instead of prompting")
    args = parser.parse_args(argv)

    models.migrate_db()  # ensure schema (incl. game_external_ids) is current
    conn = models.get_db()
    confirm = (lambda *a: True) if args.accept_fuzzy else _interactive_confirm

    total = ImportStats()
    for path in _iter_json_paths(args.paths):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        stats = import_games(conn, data["games"], data["source"],
                             dry_run=args.dry_run, confirm_fn=confirm)
        total.merge(stats)
        logger.info("%s (%s): +%d new, %d id, %d title, %d fuzzy",
                    Path(path).name, data["source"], stats.new_games,
                    stats.external_id_matches, stats.title_matches, len(stats.fuzzy_candidates))

    if args.dry_run:
        logger.info("DRY RUN — no changes written.")
    else:
        conn.commit()
    _log_summary(total, dry_run=args.dry_run)
    conn.close()


if __name__ == "__main__":
    main()
