"""Match scraped owned add-ons to DLC rows and flip owned (vendor = source of truth).

Parent is resolved by longest normalized-title prefix (the only link a
purchased-library scrape gives -- no vendor exposes a parent pointer). On a
*confident* parent the owned add-on is reconciled to an existing dlc row by vendor
id, then by normalized-name equality (recording the vendor id), else a new
vendor-sourced dlc row is created (owned=1). Every vendor id is recorded in
dlc_external_ids so re-scrapes match by id and the later per-game deep-fetch can
reconcile by id. Uncertain parents are reported for review; nothing is written for
them. Ownership is only ever set 0 -> 1; the pass is idempotent. See
docs/superpowers/specs/2026-05-25-dlc-vendor-source-foundation-design.md.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

import models

logger = logging.getLogger(__name__)

# Sentinel: more than one equally-plausible match (parent or dlc). Distinct from
# None ("no match at all").
AMBIGUOUS = "__ambiguous__"

# Vendor whose product ids carry a game-unique title-id we can resolve a parent
# by. PS Store ids are REGION-TITLEID_00-CONCEPT16; the title-id prefix is shared
# by the base game and all its add-ons. Other vendors do not share this shape, so
# the title-id fallback is guarded to this source only.
PLAYSTATION_SOURCE = "playstation"


def norm(title: str | None) -> str:
    """Normalized match key (normalize_title(clean_title(...)))."""
    return models.normalize_title(models.clean_title(title or ""))


def parent_of(addon_title: str, library: list[tuple[int, str]]) -> int | str | None:
    """Resolve an add-on's parent game by longest normalized-title prefix.

    `library` is [(game_id, normalized_title)]. Returns the game_id, None (no
    prefix matched), or AMBIGUOUS (the longest match ties across different
    game_ids). A normalized game title matches when it equals the normalized
    add-on title or is a whole-word prefix of it.
    """
    addon = norm(addon_title)
    if not addon:
        return None
    best_len = 0
    winners: set[int] = set()
    for game_id, gnorm in library:
        if not gnorm:
            continue
        if addon == gnorm or addon.startswith(gnorm + " "):
            if len(gnorm) > best_len:
                best_len, winners = len(gnorm), {game_id}
            elif len(gnorm) == best_len:
                winners.add(game_id)
    if not winners:
        return None
    if len(winners) > 1:
        return AMBIGUOUS
    return next(iter(winners))


def title_id_prefix(external_id: str | None) -> str | None:
    """The PS-Store title-id prefix of a product id, or None.

    A PS Store product id is REGION-TITLEID_00-CONCEPT16, e.g.
    JP0177-PPSA24478_00-MAJIMAOUTFITPACK. The title-id prefix is everything up to
    the last '-' (JP0177-PPSA24478_00) and is shared by the base game and all its
    add-ons. Returns None when external_id is empty or carries no '-'.
    """
    if not external_id or "-" not in external_id:
        return None
    prefix = external_id.rsplit("-", 1)[0]
    return prefix or None


def parent_by_title_id(prefix_map: dict[str, set[int]], source: str | None,
                       external_id: str | None) -> int | None:
    """Resolve a PSN add-on's parent game by shared title-id prefix.

    Source-guarded: returns None unless `source` is PLAYSTATION_SOURCE (only PS
    ids share this game-unique prefix). `prefix_map` is {title_id_prefix:
    {game_id, ...}}. Returns the game_id only when the add-on's prefix maps to
    exactly one game; an unknown or ambiguous (>=2 games) prefix returns None so
    the add-on is left for review.
    """
    if source != PLAYSTATION_SOURCE:
        return None
    prefix = title_id_prefix(external_id)
    if prefix is None:
        return None
    gids = prefix_map.get(prefix)
    if gids is None or len(gids) != 1:
        return None
    return next(iter(gids))


def psn_prefix_map(conn: sqlite3.Connection) -> dict[str, set[int]]:
    """Build {title_id_prefix: {game_id, ...}} from playstation game_external_ids.

    Rows whose external_id has no '-' (no resolvable title-id) are skipped. A
    schema predating game_external_ids yields an empty map (the fallback is
    purely additive, so its absence must not break ownership marking).
    """
    prefix_map: dict[str, set[int]] = {}
    try:
        rows = conn.execute(
            "SELECT game_id, external_id FROM game_external_ids WHERE source = ?",
            (PLAYSTATION_SOURCE,)).fetchall()
    except sqlite3.OperationalError:
        logger.debug("game_external_ids table absent; PSN title-id fallback disabled")
        return prefix_map
    for row in rows:
        prefix = title_id_prefix(row["external_id"])
        if prefix is None:
            continue
        prefix_map.setdefault(prefix, set()).add(row["game_id"])
    return prefix_map


def remainder(addon_title: str, parent_norm: str) -> str:
    """The normalized add-on title with the leading game-name words removed.

    Strips the longest whole-word prefix the add-on shares with the parent's
    normalized title -- not just the full parent title. PS4 and PS5 sell the same
    DLC under different store-title prefixes (the full "Ys VIII: Lacrimosa of Dana
    - X" vs the short "Ys VIII - X"); reducing both to the shared-prefix remainder
    ("X") lets them reconcile to one row instead of creating duplicates.
    """
    addon = norm(addon_title)
    if addon == parent_norm:
        return ""
    addon_words = addon.split()
    parent_words = parent_norm.split()
    i = 0
    while (i < len(addon_words) and i < len(parent_words)
           and addon_words[i] == parent_words[i]):
        i += 1
    if i == 0:
        return addon
    return " ".join(addon_words[i:])


def match_equal(remainder: str, dlc_rows: list[tuple[int, str]]) -> int | str | None:
    """Match an add-on remainder to a parent's dlc row by normalized-name equality.

    Returns a dlc_id, AMBIGUOUS (several equal), or None. `dlc_rows` is
    [(dlc_id, name)]. Equality only -- containment is intentionally not used (it
    produced false matches in the old engine).
    """
    rem = (remainder or "").strip()
    if not rem:
        return None
    equal = [dlc_id for dlc_id, name in dlc_rows if norm(name) == rem]
    if len(equal) == 1:
        return equal[0]
    if len(equal) > 1:
        return AMBIGUOUS
    return None


def _clean_remainder(addon_title: str, parent_title: str) -> str:
    """Display name for a created DLC row: the add-on's original title with the
    parent's display-title prefix and any joining separator stripped; falls back to
    the full add-on title when the parent is not a clean prefix."""
    addon = (addon_title or "").strip()
    parent = (parent_title or "").strip()
    if parent and addon.lower().startswith(parent.lower()):
        rem = addon[len(parent):].lstrip(" -–:|").strip()
        if rem:
            return rem
    return addon


@dataclass
class Match:
    """One add-on's outcome: a newly-owned row (dlc_id set) or a review item."""
    addon_title: str
    game_id: int | None = None
    dlc_id: int | None = None
    reason: str = ""


@dataclass
class OwnershipReport:
    """Outcome counts + the rows newly owned and the add-ons needing review."""
    created: int = 0
    reconciled: int = 0
    already_owned: int = 0
    marked: int = 0  # created + reconciled (rows newly set owned this run)
    marked_items: list[Match] = field(default_factory=list)
    review: list[Match] = field(default_factory=list)


def _addon_field(addon, key: str) -> str | None:
    """Read a field off a scrape dict or a ScrapedGame-like object."""
    return addon.get(key) if isinstance(addon, dict) else getattr(addon, key, None)


_UPSERT_REVIEW_SQL = """
INSERT INTO dlc_review_queue
    (addon_title, source, external_id, source_title, reason, game_id)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (source, external_id) WHERE source IS NOT NULL AND external_id IS NOT NULL
DO UPDATE SET
    addon_title  = excluded.addon_title,
    source_title = excluded.source_title,
    reason       = excluded.reason,
    game_id      = excluded.game_id
"""


def _persist_review(conn: sqlite3.Connection, addon, reason: str, game_id: int | None) -> None:
    """UPSERT a review item into dlc_review_queue.

    Keyed on (source, external_id) via the partial unique index so re-scrapes
    of the same vendor add-on refresh the reason without duplicating rows.
    Resolved/dismissed timestamps are intentionally NOT touched by the UPSERT.
    """
    title = _addon_field(addon, "title")
    source = _addon_field(addon, "source")
    ext = _addon_field(addon, "external_id")
    source_title = _addon_field(addon, "source_title") or title
    conn.execute(_UPSERT_REVIEW_SQL, (title, source, ext, source_title, reason, game_id))


def _record_ext_id(conn: sqlite3.Connection, dlc_id: int, source: str | None,
                   ext: str | None, source_title: str | None) -> None:
    """Record a vendor add-on id for a dlc row (no-op without source+id)."""
    if source and ext:
        conn.execute(
            "INSERT OR IGNORE INTO dlc_external_ids "
            "(dlc_id, source, external_id, source_title) VALUES (?, ?, ?, ?)",
            (dlc_id, source, ext, source_title))


def _flip(conn: sqlite3.Connection, report: OwnershipReport, dlc_id: int, title: str,
          parent: int, dry_run: bool) -> None:
    """Set an existing dlc row owned (0 -> 1). Idempotent: an already-owned row is
    counted, not re-marked."""
    owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (dlc_id,)).fetchone()[0]
    if owned:
        report.already_owned += 1
        return
    report.marked += 1
    report.reconciled += 1
    report.marked_items.append(Match(title, game_id=parent, dlc_id=dlc_id, reason="reconciled"))
    if not dry_run:
        conn.execute("UPDATE dlc SET owned = 1 WHERE id = ?", (dlc_id,))


def apply_addon_to_parent(
    conn: sqlite3.Connection,
    report: OwnershipReport,
    parent: int,
    parent_norm: str,
    titles: dict[int, str],
    addon,
    *,
    dry_run: bool,
    forced_dlc_id: int | None = None,
    force_create: bool = False,
) -> None:
    """Reconcile or create one add-on against a known parent game.

    Inner block factored out of mark_ownership; reused by dlc_review.resolve to
    land a user-picked decision. When forced_dlc_id is given, that DLC row is
    flipped directly (skipping reconcile-by-id and reconcile-by-name). When
    force_create is True, the create branch is taken unconditionally (skipping
    both reconcile steps); used when the user picks "none of these — create a
    new DLC row". Otherwise this is identical to the engine's normal flow.
    """
    title = _addon_field(addon, "title")
    source = _addon_field(addon, "source")
    ext = _addon_field(addon, "external_id")
    source_title = _addon_field(addon, "source_title") or title

    # (forced_dlc_id) user picked a specific DLC row -> attach id + flip.
    if forced_dlc_id is not None:
        if not dry_run:
            _record_ext_id(conn, forced_dlc_id, source, ext, source_title)
        _flip(conn, report, forced_dlc_id, title, parent, dry_run)
        return

    # (force_create) user said "none of these — create new"; skip both reconciles.
    if not force_create:
        # (a) reconcile by vendor id
        dlc_id = None
        if source and ext:
            row = conn.execute(
                "SELECT dlc_id FROM dlc_external_ids WHERE source = ? AND external_id = ?",
                (source, ext)).fetchone()
            if row:
                dlc_id = row[0]
        if dlc_id is not None:
            _flip(conn, report, dlc_id, title, parent, dry_run)
            return

        # (b) reconcile by normalized-name equality, reducing BOTH the add-on and
        # the existing rows by the shared game-name prefix so PS4/PS5 title-prefix
        # variants of the same DLC match regardless of which imported first.
        rows = [(r["id"], remainder(r["name"], parent_norm))
                for r in conn.execute("SELECT id, name FROM dlc WHERE game_id = ?", (parent,))]
        match = match_equal(remainder(title, parent_norm), rows)
        if match is AMBIGUOUS:
            report.review.append(Match(title, game_id=parent, reason="ambiguous dlc"))
            if not dry_run:
                _persist_review(conn, addon, "ambiguous dlc", parent)
            return
        if match is not None:
            if not dry_run:
                _record_ext_id(conn, match, source, ext, source_title)
            _flip(conn, report, match, title, parent, dry_run)
            return

    # (c) create a vendor-sourced owned row. A UNIQUE(game_id, name) collision
    #     (a same-name row that equality-matching missed) is unreachable with
    #     normal data, but would otherwise abort the whole pass mid-scrape, so
    #     fall back to reconciling the existing row instead. When force_create is
    #     True the caller explicitly wants a new row; skip the reconcile fallback
    #     on collision so the existing row is not silently flipped.
    name = _clean_remainder(title, titles.get(parent, ""))
    if dry_run:
        report.created += 1
        report.marked += 1
        report.marked_items.append(Match(title, game_id=parent, reason="created"))
        return
    try:
        cur = conn.execute(
            "INSERT INTO dlc (game_id, name, kind, owned, source) VALUES (?, ?, 'dlc', 1, ?)",
            (parent, name, source or "vendor"))
    except sqlite3.IntegrityError:
        if force_create:
            # User explicitly said "create new", but the name already exists.
            # Surface the collision rather than silently reconciling to the
            # existing row (which would contradict the user's choice).
            raise
        existing = conn.execute(
            "SELECT id FROM dlc WHERE game_id = ? AND name = ?", (parent, name)).fetchone()
        _record_ext_id(conn, existing[0], source, ext, source_title)
        _flip(conn, report, existing[0], title, parent, dry_run)
        return
    new_id = cur.lastrowid
    report.created += 1
    report.marked += 1
    _record_ext_id(conn, new_id, source, ext, source_title)
    report.marked_items.append(Match(title, game_id=parent, dlc_id=new_id, reason="created"))


def mark_ownership(conn: sqlite3.Connection, addons, *, dry_run: bool = False) -> OwnershipReport:
    """Flip dlc.owned for scraped owned add-ons (0 -> 1 only; idempotent).

    Each add-on is a scrape dict/obj carrying `title`, `source` (vendor), and
    `external_id`. On a confident parent: reconcile by vendor id, then by
    name-equality (recording the vendor id), else create a vendor-sourced owned
    row. Uncertain parents go to `report.review`. Writes nothing when dry_run
    (the caller owns commit).
    """
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM games")}
    prefix_map = psn_prefix_map(conn)

    report = OwnershipReport()
    for addon in addons:
        title = _addon_field(addon, "title")
        parent = parent_of(title, library)
        if not isinstance(parent, int):
            # Name matching missed/tied. Try the PSN title-id fallback (source-
            # guarded) before giving up to review.
            by_id = parent_by_title_id(
                prefix_map, _addon_field(addon, "source"), _addon_field(addon, "external_id"))
            if by_id is not None:
                parent = by_id
        if parent is None:
            report.review.append(Match(title, reason="no parent game"))
            if not dry_run:
                _persist_review(conn, addon, "no parent game", None)
            continue
        if parent is AMBIGUOUS:
            report.review.append(Match(title, reason="ambiguous parent"))
            if not dry_run:
                _persist_review(conn, addon, "ambiguous parent", None)
            continue
        parent_norm = next(gnorm for gid, gnorm in library if gid == parent)
        apply_addon_to_parent(conn, report, parent, parent_norm, titles, addon, dry_run=dry_run)
    return report


def _dedup_survivor_key(row) -> tuple:
    """Sort key picking the cleanest DLC row to keep in a duplicate group:
    an IGDB-named row first, then a non-artifact (alphanumeric-leading) name, then
    the shortest normalized name, then the lowest id."""
    name = row["name"] or ""
    return (row["igdb_id"] is None, not name[:1].isalnum(), len(norm(name)), row["id"])


def dedup_dlc(conn: sqlite3.Connection, *, dry_run: bool = True) -> list[dict]:
    """Collapse duplicate DLC rows within each game into one.

    Two DLC rows duplicate each other when they reduce to the same name after the
    shared game-name prefix is stripped (see `remainder`) -- e.g. PS4's "Ys VIII -
    Bottled Potion Set" and PS5's clean "Bottled Potion Set". Keeps one survivor
    (see `_dedup_survivor_key`), moves every vendor id onto it, ORs ownership, and
    deletes the rest. Returns a per-group report; `dry_run` writes nothing. The
    caller owns commit. Idempotent: a second pass finds no groups.
    """
    games = {r["id"]: (r["normalized_title"] or "")
             for r in conn.execute("SELECT id, normalized_title FROM games")}
    rows_by_game: dict[int, list] = defaultdict(list)
    for r in conn.execute("SELECT id, game_id, name, igdb_id, owned FROM dlc"):
        rows_by_game[r["game_id"]].append(r)

    report: list[dict] = []
    for gid, rows in rows_by_game.items():
        parent_norm = games.get(gid, "")
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[remainder(r["name"], parent_norm)].append(r)
        for grp in groups.values():
            if len(grp) < 2:
                continue
            survivor = min(grp, key=_dedup_survivor_key)
            dropped = [r for r in grp if r["id"] != survivor["id"]]
            owned = any(r["owned"] for r in grp)
            report.append({"game_id": gid, "survivor": survivor["name"],
                           "dropped": [r["name"] for r in dropped], "owned": owned})
            if dry_run:
                continue
            survivor_has_igdb = survivor["igdb_id"] is not None
            for d in dropped:
                conn.execute("UPDATE OR IGNORE dlc_external_ids SET dlc_id = ? WHERE dlc_id = ?",
                             (survivor["id"], d["id"]))
                if d["igdb_id"] is not None and not survivor_has_igdb:
                    conn.execute("UPDATE dlc SET igdb_id = ? WHERE id = ?",
                                 (d["igdb_id"], survivor["id"]))
                    survivor_has_igdb = True
                conn.execute("DELETE FROM dlc WHERE id = ?", (d["id"],))
            if owned:
                conn.execute("UPDATE dlc SET owned = 1 WHERE id = ?", (survivor["id"],))
    return report
