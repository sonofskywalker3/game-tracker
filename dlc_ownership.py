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
from dataclasses import dataclass, field

import models

logger = logging.getLogger(__name__)

# Sentinel: more than one equally-plausible match (parent or dlc). Distinct from
# None ("no match at all").
AMBIGUOUS = "__ambiguous__"


def _norm(title: str | None) -> str:
    """Normalized match key (normalize_title(clean_title(...)))."""
    return models.normalize_title(models.clean_title(title or ""))


def parent_of(addon_title: str, library: list[tuple[int, str]]) -> int | str | None:
    """Resolve an add-on's parent game by longest normalized-title prefix.

    `library` is [(game_id, normalized_title)]. Returns the game_id, None (no
    prefix matched), or AMBIGUOUS (the longest match ties across different
    game_ids). A normalized game title matches when it equals the normalized
    add-on title or is a whole-word prefix of it.
    """
    addon = _norm(addon_title)
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


def _remainder(addon_title: str, parent_norm: str) -> str:
    """The normalized add-on title with the parent's normalized prefix removed."""
    addon = _norm(addon_title)
    if addon == parent_norm:
        return ""
    prefix = parent_norm + " "
    if addon.startswith(prefix):
        return addon[len(prefix):]
    return addon


def match_equal(remainder: str, dlc_rows: list[tuple[int, str]]) -> int | str | None:
    """Match an add-on remainder to a parent's dlc row by normalized-name equality.

    Returns a dlc_id, AMBIGUOUS (several equal), or None. `dlc_rows` is
    [(dlc_id, name)]. Equality only -- containment is intentionally not used (it
    produced false matches in the old engine).
    """
    rem = (remainder or "").strip()
    if not rem:
        return None
    equal = [dlc_id for dlc_id, name in dlc_rows if _norm(name) == rem]
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

    report = OwnershipReport()
    for addon in addons:
        title = _addon_field(addon, "title")
        source = _addon_field(addon, "source")
        ext = _addon_field(addon, "external_id")
        source_title = _addon_field(addon, "source_title") or title

        parent = parent_of(title, library)
        if parent is None:
            report.review.append(Match(title, reason="no parent game"))
            continue
        if parent is AMBIGUOUS:
            report.review.append(Match(title, reason="ambiguous parent"))
            continue

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
            continue

        # (b) reconcile by normalized-name equality
        parent_norm = next(gnorm for gid, gnorm in library if gid == parent)
        rows = [(r["id"], r["name"])
                for r in conn.execute("SELECT id, name FROM dlc WHERE game_id = ?", (parent,))]
        match = match_equal(_remainder(title, parent_norm), rows)
        if match is AMBIGUOUS:
            report.review.append(Match(title, game_id=parent, reason="ambiguous dlc"))
            continue
        if match is not None:
            if not dry_run:
                _record_ext_id(conn, match, source, ext, source_title)
            _flip(conn, report, match, title, parent, dry_run)
            continue

        # (c) create a vendor-sourced owned row
        report.created += 1
        report.marked += 1
        if dry_run:
            report.marked_items.append(Match(title, game_id=parent, reason="created"))
            continue
        name = _clean_remainder(title, titles.get(parent, ""))
        cur = conn.execute(
            "INSERT INTO dlc (game_id, name, kind, owned, source) VALUES (?, ?, 'dlc', 1, ?)",
            (parent, name, source or "vendor"))
        new_id = cur.lastrowid
        _record_ext_id(conn, new_id, source, ext, source_title)
        report.marked_items.append(Match(title, game_id=parent, dlc_id=new_id, reason="created"))
    return report
