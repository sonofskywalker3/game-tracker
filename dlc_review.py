"""Apply user-picked decisions to queued DLC review items.

Each review row in `dlc_review_queue` represents an owned add-on the engine
couldn't auto-link to a game/DLC (no parent / ambiguous parent / ambiguous dlc).
`resolve` lets the modal hand back the user's pick (a game_id, a dlc_id, or
"create a new DLC row") and runs the same per-add-on reconcile/create logic the
scrape engine uses, then marks the row resolved.

Pure DB; no Flask. See
docs/superpowers/specs/2026-05-25-dlc-sp3-modal-and-resolution-design.md.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

import dlc_ownership
from dlc_ownership import Match, OwnershipReport

logger = logging.getLogger(__name__)


@dataclass
class RematchReport:
    """Outcome of a re-match pass over still-open review rows."""
    resolved: int = 0  # review rows newly marked resolved this pass
    marked: int = 0     # dlc rows newly set owned this pass (created + reconciled)
    resolved_items: list[Match] = field(default_factory=list)


def resolve(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    picked_game_id: int | None = None,
    picked_dlc_id: int | None = None,
    create_new_dlc: bool = False,
) -> Match:
    """Apply one user-resolved review item; return the resulting Match.

    Exactly one of picked_game_id / picked_dlc_id / create_new_dlc must be set.
    Idempotent: resolving an already-resolved row is a no-op that returns a
    synthesized Match for the current state. Raises ValueError if the picked
    game or DLC doesn't exist, or if the row is dismissed, or if the choice
    count is wrong.
    """
    picks = [picked_game_id is not None, picked_dlc_id is not None, create_new_dlc]
    if sum(picks) != 1:
        raise ValueError(
            "resolve requires exactly one of picked_game_id, picked_dlc_id, "
            "or create_new_dlc=True")

    row = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, "
        "game_id, resolved_at, dismissed_at "
        "FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ValueError(f"review_id {review_id} not found")
    if row["resolved_at"] is not None:
        logger.info("review_id %s already resolved; no-op", review_id)
        return Match(row["addon_title"], game_id=row["game_id"], reason="already resolved")
    if row["dismissed_at"] is not None:
        raise ValueError(f"review_id {review_id} is dismissed; cannot resolve")

    addon = {"title": row["addon_title"], "source": row["source"],
             "external_id": row["external_id"], "source_title": row["source_title"]}

    # Resolve the parent for the apply call.
    if picked_dlc_id is not None:
        dlc_row = conn.execute(
            "SELECT game_id FROM dlc WHERE id = ?", (picked_dlc_id,)).fetchone()
        if dlc_row is None:
            raise ValueError(f"picked_dlc_id {picked_dlc_id} not found")
        parent = dlc_row["game_id"]
    elif picked_game_id is not None:
        if conn.execute("SELECT 1 FROM games WHERE id = ?", (picked_game_id,)).fetchone() is None:
            raise ValueError(f"picked_game_id {picked_game_id} not found")
        parent = picked_game_id
    else:  # create_new_dlc
        if row["game_id"] is None:
            raise ValueError("create_new_dlc requires an 'ambiguous dlc' row "
                             "(which carries the known parent game_id)")
        parent = row["game_id"]

    parent_title_row = conn.execute(
        "SELECT title, normalized_title FROM games WHERE id = ?", (parent,)).fetchone()
    if parent_title_row is None:
        raise ValueError(f"parent game {parent} not found")
    parent_norm = parent_title_row["normalized_title"] or ""
    titles = {parent: parent_title_row["title"]}

    report = OwnershipReport()
    dlc_ownership.apply_addon_to_parent(
        conn, report, parent, parent_norm, titles, addon,
        dry_run=False,
        forced_dlc_id=picked_dlc_id,
        force_create=create_new_dlc,
    )

    conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (review_id,))

    if report.marked_items:
        return report.marked_items[0]
    # marked_items is empty when _apply hit "already_owned"; synthesize a Match.
    return Match(row["addon_title"], game_id=parent, reason="already owned")


def rematch_unresolved(conn: sqlite3.Connection) -> RematchReport:
    """Re-run the full matcher over still-open review rows; clear those now linkable.

    Review rows queued before a matcher improvement (e.g. the PSN title-id
    fallback) never get a second chance from a re-scrape unless the vendor library
    is fetched again. This replays the exact engine `mark_ownership` uses against
    every open (not resolved, not dismissed) row: it resolves a parent by name
    prefix, then by the PSN title-id fallback, and applies the add-on through
    `apply_addon_to_parent` (no one-off UPDATE -- same pipeline as the live
    scrape). A row is marked resolved only when the apply yields a real
    create/reconcile/already-owned outcome; rows that merely refine to an
    ambiguous-dlc review are left open. Pure DB; the caller owns commit.
    """
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM games")}
    prefix_map = dlc_ownership.psn_prefix_map(conn)

    rows = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, game_id "
        "FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL ORDER BY id").fetchall()

    report = RematchReport()
    for row in rows:
        addon = {"title": row["addon_title"], "source": row["source"],
                 "external_id": row["external_id"], "source_title": row["source_title"]}

        parent = dlc_ownership.parent_of(row["addon_title"], library)
        if not isinstance(parent, int):
            parent = dlc_ownership.parent_by_title_id(
                prefix_map, row["source"], row["external_id"])
        if not isinstance(parent, int):
            continue  # still unresolvable -- leave the row open

        parent_norm = next((gnorm for gid, gnorm in library if gid == parent), "") or ""
        sub = OwnershipReport()
        dlc_ownership.apply_addon_to_parent(
            conn, sub, parent, parent_norm, titles, addon, dry_run=False)

        if sub.marked or sub.already_owned:
            conn.execute(
                "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],))
            report.resolved += 1
            report.marked += sub.marked
            report.resolved_items.extend(sub.marked_items)

    logger.info("rematch_unresolved: %d row(s) resolved, %d dlc row(s) newly owned",
                report.resolved, report.marked)
    return report


def dismiss(conn: sqlite3.Connection, review_id: int) -> None:
    """Mark a review row dismissed (user said: not a real add-on). Idempotent."""
    if conn.execute("SELECT 1 FROM dlc_review_queue WHERE id = ?", (review_id,)).fetchone() is None:
        raise ValueError(f"review_id {review_id} not found")
    conn.execute(
        "UPDATE dlc_review_queue "
        "SET dismissed_at = COALESCE(dismissed_at, CURRENT_TIMESTAMP) "
        "WHERE id = ?", (review_id,))
