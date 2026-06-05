"""Vendor-agnostic add-on -> parent-game resolution at scrape time.

Store vendors (Xbox, Nintendo) scrape add-ons as a flat list with no parent
reference, so the name-based matcher in `dlc_ownership.mark_ownership` files them
as "no parent game". This module resolves each owned add-on to its parent GAME via
a per-vendor resolver (e.g. Microsoft displaycatalog's `addOnParent`), ensures that
parent exists in the library (match by vendor id, else by name + backfill the id,
else create it from catalog metadata), links the add-on owned through the existing
ownership engine, and clears any matching review-queue rows. Pure DB; the resolver's
network I/O is injected. See
docs/superpowers/specs/2026-06-05-addon-parent-resolution-design.md.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

import dlc_ownership  # noqa: F401  (used by resolve_and_link in a later task)
import import_scraped
from dlc_ownership import Match, OwnershipReport  # noqa: F401  (OwnershipReport: later task)

logger = logging.getLogger(__name__)

# A resolver maps a list of add-on vendor product ids to each one's parent GAME.
ParentResolver = Callable[[list[str]], "dict[str, ParentRef | None]"]


@dataclass
class ParentRef:
    """A resolved parent GAME identity for one add-on (from the vendor catalogue)."""
    product_id: str
    name: str | None = None
    cover_url: str | None = None


@dataclass
class ResolveReport:
    """Outcome of a resolve_and_link pass."""
    linked: int = 0              # dlc rows newly set owned (created + reconciled)
    created_parents: int = 0     # parent games created from the catalogue
    backfilled_ids: int = 0      # existing games that gained a vendor id this pass
    review_cleared: int = 0      # open review rows marked resolved
    linked_items: list[Match] = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # add-ons with no catalogue parent


def _ensure_parent_game(
    conn: sqlite3.Connection, source: str, platform: str, parent: ParentRef,
    *, create_missing: bool = True,
) -> tuple[int | None, str]:
    """Return (game_id, how) for an add-on's parent.

    `how` is one of: "id" (matched a game already carrying this vendor product id),
    "backfill" (the import name-matched an existing game, recording the id onto it),
    "created" (the import created a new game), or "" (no parent: create_missing False
    with no id match, or the import produced no game, e.g. a non-game title).

    Resolution order: (1) existing game by vendor product id; (2) otherwise, when
    create_missing, a synthetic one-game `import_scraped.import_games` call, which
    name-matches an existing game (id backfilled via its INSERT OR IGNORE) or creates
    a new one (`ImportStats.new_games` distinguishes the two).
    """
    row = conn.execute(
        "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
        (source, parent.product_id)).fetchone()
    if row:
        return row[0], "id"
    if not create_missing or not parent.name:
        return None, ""
    synthetic = {
        "title": parent.name, "platform": platform, "source": source,
        "external_id": parent.product_id, "cover_url": parent.cover_url, "kind": "game",
    }
    stats = import_scraped.import_games(
        conn, [synthetic], source, confirm_fn=import_scraped._safe_auto_confirm)
    row = conn.execute(
        "SELECT game_id FROM game_external_ids WHERE source = ? AND external_id = ?",
        (source, parent.product_id)).fetchone()
    if not row:
        logger.warning("addon_parent: parent %r (%s) did not import", parent.name, parent.product_id)
        return None, ""
    return row[0], ("created" if stats.new_games > 0 else "backfill")
