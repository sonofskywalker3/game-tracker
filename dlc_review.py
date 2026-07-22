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
from identity import OWNER_USER_ID

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
    user_id: int = OWNER_USER_ID,
) -> Match:
    """Apply one user-resolved review item; return the resulting Match.

    Exactly one of picked_game_id / picked_dlc_id / create_new_dlc must be set.
    Idempotent: resolving an already-resolved row is a no-op that returns a
    synthesized Match for the current state. The row is marked resolved only
    when the apply yields a real create/reconcile/already-owned outcome; an
    apply that merely refines to an 'ambiguous dlc' review leaves the row open
    and returns a Match with that reason. Raises ValueError if the picked game
    or DLC doesn't exist, or if the row is dismissed, or if the choice count is
    wrong.

    Every DB read is scoped to ``user_id`` (defaults to the owner so the
    single-tenant/legacy path is unchanged): the review row, the picked game,
    and the picked DLC's parent game must ALL belong to the acting user, so
    user B can neither resolve user A's queue row nor write DLC ownership onto
    user A's game via a caller-supplied picked_game_id/picked_dlc_id. A row or
    target the acting user does not own reads as "not found" (→ 404 at the
    route), never a cross-user write.
    """
    picks = [picked_game_id is not None, picked_dlc_id is not None, create_new_dlc]
    if sum(picks) != 1:
        raise ValueError(
            "resolve requires exactly one of picked_game_id, picked_dlc_id, "
            "or create_new_dlc=True")

    row = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, "
        "game_id, resolved_at, dismissed_at "
        "FROM dlc_review_queue WHERE id = ? AND user_id = ?",
        (review_id, user_id)).fetchone()
    if row is None:
        raise ValueError(f"review_id {review_id} not found")
    if row["resolved_at"] is not None:
        logger.info("review_id %s already resolved; no-op", review_id)
        return Match(row["addon_title"], game_id=row["game_id"], reason="already resolved")
    if row["dismissed_at"] is not None:
        raise ValueError(f"review_id {review_id} is dismissed; cannot resolve")

    addon = {"title": row["addon_title"], "source": row["source"],
             "external_id": row["external_id"], "source_title": row["source_title"]}

    # Resolve the parent for the apply call. The picked game/DLC-parent must be
    # owned by the acting user, else a user could write DLC ownership onto
    # another user's game by supplying its id.
    if picked_dlc_id is not None:
        dlc_row = conn.execute(
            "SELECT d.game_id FROM dlc d JOIN games g ON g.id = d.game_id "
            "WHERE d.id = ? AND g.user_id = ?", (picked_dlc_id, user_id)).fetchone()
        if dlc_row is None:
            raise ValueError(f"picked_dlc_id {picked_dlc_id} not found")
        parent = dlc_row["game_id"]
    elif picked_game_id is not None:
        if conn.execute("SELECT 1 FROM games WHERE id = ? AND user_id = ?",
                        (picked_game_id, user_id)).fetchone() is None:
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

    if not (report.marked or report.already_owned):
        # The apply produced no create/reconcile/already-owned outcome — it only
        # refined this row to an 'ambiguous dlc' review (persisted by the apply's
        # own upsert). Leave the row OPEN so the user can pick a specific DLC,
        # and report the true outcome instead of a false "already owned" success.
        logger.info("review_id %s left open: apply outcome is %r", review_id,
                    report.review[0].reason if report.review else "unapplied")
        if report.review:
            return report.review[0]
        return Match(row["addon_title"], game_id=parent, reason="unapplied")

    conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (review_id,))

    if report.marked_items:
        return report.marked_items[0]
    # marked_items is empty when _apply hit "already_owned"; synthesize a Match.
    return Match(row["addon_title"], game_id=parent, reason="already owned")


def rematch_unresolved(conn: sqlite3.Connection,
                       user_id: int = OWNER_USER_ID) -> RematchReport:
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

    Scoped to ``user_id`` (defaults to the owner): only the acting user's
    library is reconciled and only their open queue rows are touched, so a
    rematch can never link add-ons onto — or resolve queue rows against —
    another user's games.
    """
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute(
                   "SELECT id, normalized_title FROM games WHERE user_id = ?", (user_id,))]
    titles = {r["id"]: r["title"]
              for r in conn.execute(
                  "SELECT id, title FROM games WHERE user_id = ?", (user_id,))}
    prefix_map = dlc_ownership.psn_prefix_map(conn)

    rows = conn.execute(
        "SELECT id, addon_title, source, external_id, source_title, reason, game_id "
        "FROM dlc_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL AND user_id = ? "
        "ORDER BY id", (user_id,)).fetchall()

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

        # The global PSN prefix_map spans every user's external ids; never apply
        # to a game outside the acting user's (scoped) library.
        library_ids = {gid for gid, _ in library}
        if parent not in library_ids:
            continue

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


def dismiss(conn: sqlite3.Connection, review_id: int,
            user_id: int = OWNER_USER_ID) -> None:
    """Mark a review row dismissed (user said: not a real add-on). Idempotent.

    Scoped to ``user_id`` (defaults to the owner): dismissing a queue row the
    acting user does not own is a "not found" (→ 404 at the route), not a
    cross-user write."""
    if conn.execute("SELECT 1 FROM dlc_review_queue WHERE id = ? AND user_id = ?",
                    (review_id, user_id)).fetchone() is None:
        raise ValueError(f"review_id {review_id} not found")
    conn.execute(
        "UPDATE dlc_review_queue "
        "SET dismissed_at = COALESCE(dismissed_at, CURRENT_TIMESTAMP) "
        "WHERE id = ? AND user_id = ?", (review_id, user_id))
