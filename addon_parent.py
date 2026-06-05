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

import dlc_ownership
import import_scraped
from dlc_ownership import Match, OwnershipReport

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


def _clear_review(conn: sqlite3.Connection, source: str, ext: str | None) -> int:
    """Mark any open review rows for this vendor add-on resolved. Returns rowcount."""
    if not ext:
        return 0
    cur = conn.execute(
        "UPDATE dlc_review_queue SET resolved_at = CURRENT_TIMESTAMP "
        "WHERE source = ? AND external_id = ? "
        "AND resolved_at IS NULL AND dismissed_at IS NULL",
        (source, ext))
    return cur.rowcount


def resolve_and_link(
    conn: sqlite3.Connection, source: str, platform: str, addons: list,
    resolver: ParentResolver, *, create_missing: bool = True,
) -> ResolveReport:
    """Resolve each owned add-on to its parent game and mark it owned.

    For every add-on (a scrape dict with title/source/external_id/source_title):
    ask `resolver` for its parent, ensure that parent game exists (see
    `_ensure_parent_game`), link the add-on owned via the shared engine
    `dlc_ownership.apply_addon_to_parent`, and clear matching open review rows.
    Add-ons with no catalogue parent go to `report.unresolved` for the caller to
    fall back on `dlc_ownership.mark_ownership`. Caller owns commit.
    """
    report = ResolveReport()
    ids = [dlc_ownership._addon_field(a, "external_id") for a in addons]
    ids = [i for i in ids if i]
    parents = resolver(ids) if ids else {}

    for addon in addons:
        ext = dlc_ownership._addon_field(addon, "external_id")
        parent_ref = parents.get(ext) if ext else None
        if parent_ref is None:
            report.unresolved.append(addon)
            continue
        parent_id, how = _ensure_parent_game(
            conn, source, platform, parent_ref, create_missing=create_missing)
        if parent_id is None:
            report.unresolved.append(addon)
            continue
        if how == "created":
            report.created_parents += 1
        elif how == "backfill":
            report.backfilled_ids += 1

        prow = conn.execute(
            "SELECT title, normalized_title FROM games WHERE id = ?", (parent_id,)).fetchone()
        parent_norm = (prow["normalized_title"] if prow else "") or ""
        titles = {parent_id: prow["title"] if prow else ""}

        sub = OwnershipReport()
        dlc_ownership.apply_addon_to_parent(
            conn, sub, parent_id, parent_norm, titles, addon, dry_run=False)
        report.linked += sub.marked
        report.linked_items.extend(sub.marked_items)
        if sub.marked or sub.already_owned:
            report.review_cleared += _clear_review(conn, source, ext)

    return report


# Per-source add-on parent resolvers. Populated at import time by scrape wiring
# (see scrape_service). A source with no resolver falls back to name matching.
RESOLVERS: dict[str, ParentResolver] = {}


def _register_default_resolvers() -> None:
    """Register built-in vendor resolvers (called once at import)."""
    try:
        from scrapers import xbox_catalog
    except ImportError:  # pragma: no cover - scrapers always present in app runtime
        logger.warning("addon_parent: xbox_catalog unavailable; xbox parent resolution disabled")
        return
    RESOLVERS.setdefault("xbox", xbox_catalog.resolve_addon_parents)


_register_default_resolvers()
