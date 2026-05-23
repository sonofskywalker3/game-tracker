"""Library deduplication: one row per playable game.

This tracker tracks games and progress to decide what to play or buy next — not
a wishlist or a catalog of every edition/version owned. The data model is one
row per playable game; this module collapses duplicate rows (editions, regional
variants, cross-platform copies, cross-source imports) into one. Bundle
expansion (a single store product that grants several separate games) is a
separate concern (see import_scraped/bundles); single-product launchers played
as one thing (Ezio Collection, GTA Trilogy) deliberately stay one game.

Pure detection + merge engine over a sqlite3 connection; the Flask layer in
app.py is a thin wrapper.
"""
from __future__ import annotations

import difflib
import logging
import sqlite3
from collections import defaultdict

from models import clean_title, normalize_title

log = logging.getLogger(__name__)

FUZZY_THRESHOLD = 0.85

# Edition / qualifier phrases that mark the SAME game in a different edition.
# Used for matching only (display titles are chosen in the merge modal).
# Extensible curated table.
EDITION_QUALIFIERS = (
    "Digital Deluxe Edition", "Spacer's Choice Edition", "Game of the Year Edition",
    "Console Edition", "Definitive Edition", "Complete Edition", "Deluxe Edition",
    "Ultimate Edition", "Special Edition", "Legendary Edition", "Anniversary Edition",
    "Enhanced Edition", "Gold Edition", "Royal Edition", "Collection Edition",
    "GOTY Edition", "The Final Cut", "Remastered", "Remaster", "Redux",
)


def base_key(title: str) -> str:
    """Fresh match key for a title: normalize_title(clean_title(title))."""
    return normalize_title(clean_title(title))


# Normalized qualifier keys, longest first so the most specific strips first.
_NORM_QUALIFIERS = tuple(
    sorted((normalize_title(q) for q in EDITION_QUALIFIERS), key=len, reverse=True)
)


def strip_edition_key(key: str) -> str:
    """Remove a single trailing known edition qualifier from a normalized key."""
    for qualifier in _NORM_QUALIFIERS:
        suffix = " " + qualifier
        if key.endswith(suffix):
            return key[: -len(suffix)].strip()
    return key


def _dismissed_pairs(conn: sqlite3.Connection) -> set[frozenset[int]]:
    try:
        rows = conn.execute(
            "SELECT game_id_lo, game_id_hi FROM not_duplicates"
        ).fetchall()
    except sqlite3.OperationalError:  # table not migrated yet
        return set()
    return {frozenset((r[0], r[1])) for r in rows}


def _contains(a: str, b: str) -> bool:
    """True if one key word-contains the other (different lengths)."""
    if a == b:
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return f" {short} " in f" {long} " or long.startswith(short + " ") or long.endswith(" " + short)


def find_duplicate_groups(conn: sqlite3.Connection) -> dict:
    """Detect duplicate games. Returns {"definite": [[id,...]], "candidates": [...]}.

    definite  = identical base_key (auto-mergeable).
    candidates = pairs flagged for yes/no, each {"a", "b", "reason", "score"};
                 reason in {"edition", "contains", "similar"}. Pairs already in
                 not_duplicates are excluded. Pure read; computes fresh keys in
                 memory so stored normalized_title staleness does not matter.
    """
    games = [(r["id"], r["title"]) for r in
             conn.execute("SELECT id, title FROM games ORDER BY id").fetchall()]
    dismissed = _dismissed_pairs(conn)
    keys = {gid: base_key(title) for gid, title in games}

    by_key: dict[str, list[int]] = defaultdict(list)
    for gid, _ in games:
        by_key[keys[gid]].append(gid)
    definite = [sorted(ids) for ids in by_key.values() if len(ids) > 1]

    candidates: list[dict] = []
    ids = [gid for gid, _ in games]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if frozenset((a, b)) in dismissed:
                continue
            ka, kb = keys[a], keys[b]
            if ka == kb:
                continue  # already definite
            if strip_edition_key(ka) == strip_edition_key(kb):
                reason, score = "edition", 1.0
            elif _contains(ka, kb):
                reason, score = "contains", 0.95
            else:
                ratio = difflib.SequenceMatcher(None, ka, kb).ratio()
                if ratio < FUZZY_THRESHOLD:
                    continue
                reason, score = "similar", round(ratio, 3)
            candidates.append({"a": a, "b": b, "reason": reason, "score": score})

    return {"definite": definite, "candidates": candidates}


# Status progression rank (higher = further along). "100" is a completion tier.
_STATUS_RANK = {
    "completed": 6, "100": 6, "playing": 5, "dropped": 4,
    "backlog": 3, "wishlist": 2, "": 0, None: 0,
}


def compute_merged_curation(rows: list[dict]) -> dict:
    """Combine curation from rows (survivor first) into one preferred set."""
    def _max(field):
        vals = [r.get(field) for r in rows if r.get(field) not in (None, "")]
        return max(vals) if vals else None

    def _first(field):  # survivor first; first set value wins
        for r in rows:
            if r.get(field) not in (None, ""):
                return r.get(field)
        return None

    status = max((r.get("status") for r in rows), key=lambda s: _STATUS_RANK.get(s, 0))
    notes = "\n\n".join(dict.fromkeys(
        r["notes"].strip() for r in rows if r.get("notes") and r["notes"].strip()
    )) or None
    started = min((r["started_at"] for r in rows if r.get("started_at")), default=None)
    completed = max((r["completed_at"] for r in rows if r.get("completed_at")), default=None)

    return {
        "status": status or "backlog",
        "rating": _max("rating"),
        "hours_played": _max("hours_played") or 0,
        "priority": _max("priority") or 5,
        "notes": notes,
        "series_id": _first("series_id"),
        "series_order": _first("series_order"),
        "started_at": started,
        "completed_at": completed,
        "sort_order": rows[0].get("sort_order"),
    }


_CURATION_FIELDS = (
    "status", "rating", "notes", "priority", "hours_played",
    "started_at", "completed_at", "sort_order", "series_id", "series_order",
)


def _rating_row(conn: sqlite3.Connection, game_id: int) -> dict:
    row = conn.execute(
        "SELECT status, rating, notes, priority, hours_played, started_at, "
        "completed_at, sort_order, series_id, series_order "
        "FROM user_ratings WHERE game_id = ?", (game_id,)
    ).fetchone()
    return dict(row) if row else {"status": "backlog"}


def merge_games(conn: sqlite3.Connection, survivor_id: int, drop_ids: list[int], *,
                title: str | None = None, curation: dict | None = None,
                dry_run: bool = False) -> dict:
    """Merge drop_ids into survivor_id: one row per playable game.

    Moves external ids onto the survivor, unions platform links and tags,
    combines curation (or applies the supplied `curation` override), sets the
    survivor's title (+ recomputed normalized_title), and deletes the drops
    (ON DELETE CASCADE removes their leftover children). dry_run writes nothing.
    Returns a plan/result dict.
    """
    all_ids = [survivor_id] + list(drop_ids)
    rows = [_rating_row(conn, gid) for gid in all_ids]
    merged_curation = curation or compute_merged_curation(rows)
    new_title = title if title is not None else conn.execute(
        "SELECT title FROM games WHERE id = ?", (survivor_id,)).fetchone()["title"]

    plan = {"survivor_id": survivor_id, "drop_ids": list(drop_ids),
            "title": new_title, "curation": merged_curation}
    if dry_run:
        return plan

    for drop_id in drop_ids:
        conn.execute("UPDATE game_external_ids SET game_id = ? WHERE game_id = ?",
                     (survivor_id, drop_id))
        conn.execute(
            "INSERT OR IGNORE INTO game_platforms (game_id, platform_id, owned, psprices_id) "
            "SELECT ?, platform_id, owned, psprices_id FROM game_platforms WHERE game_id = ?",
            (survivor_id, drop_id))
        conn.execute(
            "INSERT OR IGNORE INTO game_tags (game_id, tag_id) "
            "SELECT ?, tag_id FROM game_tags WHERE game_id = ?", (survivor_id, drop_id))
        conn.execute("DELETE FROM games WHERE id = ?", (drop_id,))

    conn.execute("UPDATE games SET title = ?, normalized_title = ?, "
                 "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (new_title, base_key(new_title), survivor_id))

    sets = ", ".join(f"{f} = ?" for f in _CURATION_FIELDS)
    conn.execute(
        f"INSERT INTO user_ratings (game_id, {', '.join(_CURATION_FIELDS)}) "
        f"VALUES (?, {', '.join('?' * len(_CURATION_FIELDS))}) "
        f"ON CONFLICT(game_id) DO UPDATE SET {sets}",
        [survivor_id] + [merged_curation.get(f) for f in _CURATION_FIELDS]
        + [merged_curation.get(f) for f in _CURATION_FIELDS])
    conn.commit()
    return plan


def refresh_normalized_titles(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[dict]:
    """Recompute stored normalized_title = base_key(title) for all games.

    Safe to run after dedup (duplicates merged, so no UNIQUE collisions). On a
    residual collision, logs and skips that row instead of crashing. Returns the
    changed rows as {"id", "old", "new"}.
    """
    changes = []
    for row in conn.execute("SELECT id, title, normalized_title FROM games").fetchall():
        new = base_key(row["title"])
        if new == row["normalized_title"]:
            continue
        changes.append({"id": row["id"], "old": row["normalized_title"], "new": new})
        if not dry_run:
            try:
                conn.execute("UPDATE games SET normalized_title = ? WHERE id = ?",
                             (new, row["id"]))
            except sqlite3.IntegrityError:
                log.warning("normalized_title collision for game %s (%r) -> %r; skipped",
                            row["id"], row["title"], new)
                changes.pop()
    if not dry_run:
        conn.commit()
    return changes
