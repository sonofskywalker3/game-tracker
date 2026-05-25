"""Match scraped owned add-ons to existing IGDB-sourced dlc rows and flip owned.

Pure helpers (parent_of, match_dlc, classify) are unit-tested; mark_ownership
orchestrates against a (temp or live) connection. Ownership is only ever set
0 -> 1, never 1 -> 0; the pass is idempotent. Add-ons that match no existing dlc
row are reported, never inserted (the dlc list stays IGDB-curated). See
docs/superpowers/specs/2026-05-25-dlc-scrape-ownership-design.md.
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
    """Normalized match key, matching how games.normalized_title is stored
    (normalize_title(clean_title(...)))."""
    return models.normalize_title(models.clean_title(title or ""))


def parent_of(addon_title: str, library: list[tuple[int, str]]) -> int | str | None:
    """Resolve an add-on's parent game by longest normalized-title prefix.

    `library` is [(game_id, normalized_title)]. Returns the game_id, None (no
    prefix matched), or AMBIGUOUS (the longest match is a tie across different
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


def _contains_words(haystack: str, needle: str) -> bool:
    """True if `needle` occurs as a whole-word run inside `haystack`.

    Both args are already normalized (lowercase, single-spaced)."""
    if not needle or not haystack:
        return False
    return f" {needle} " in f" {haystack} "


def match_dlc(remainder: str, dlc_rows: list[tuple[int, str]]) -> tuple[int | str | None, str | None]:
    """Match an add-on remainder to one of a parent's dlc rows.

    Returns (result, method): result is a dlc_id, None, or AMBIGUOUS; method is
    "equality", "containment", or None. Equality on normalized names is tried
    first; then whole-word containment in either direction. `dlc_rows` is
    [(dlc_id, name)].
    """
    rem = (remainder or "").strip()
    if not rem:
        return None, None
    norm = [(dlc_id, models.normalize_title(name)) for dlc_id, name in dlc_rows]
    equal = [dlc_id for dlc_id, n in norm if n == rem]
    if len(equal) == 1:
        return equal[0], "equality"
    if len(equal) > 1:
        return AMBIGUOUS, "equality"
    contained = [dlc_id for dlc_id, n in norm
                 if _contains_words(rem, n) or _contains_words(n, rem)]
    if len(contained) == 1:
        return contained[0], "containment"
    if len(contained) > 1:
        return AMBIGUOUS, "containment"
    return None, None


@dataclass
class Match:
    """The matching verdict for one scraped add-on."""

    action: str  # "apply" | "hold" | "unmatched"
    addon_title: str
    game_id: int | None = None
    dlc_id: int | None = None
    reason: str = ""


def classify(
    addon_title: str,
    library: list[tuple[int, str]],
    dlc_by_game: dict[int, list[tuple[int, str]]],
) -> Match:
    """Decide whether an add-on should apply, hold, or be reported unmatched.

    apply  = parent resolves uniquely AND a dlc name matches by equality.
    hold   = ambiguous parent/dlc, or a containment-only dlc match (plausible,
             not certain) -- never auto-applied without include_flagged.
    unmatched = no parent, or parent has no matching dlc row.
    """
    parent = parent_of(addon_title, library)
    if parent is None:
        return Match("unmatched", addon_title, reason="no parent game")
    if parent is AMBIGUOUS:
        return Match("hold", addon_title, reason="ambiguous parent")
    rows = dlc_by_game.get(parent) or []
    if not rows:
        return Match("unmatched", addon_title, game_id=parent, reason="parent has no dlc")
    parent_norm = next(gnorm for gid, gnorm in library if gid == parent)
    result, method = match_dlc(_remainder(addon_title, parent_norm), rows)
    if result is None:
        return Match("unmatched", addon_title, game_id=parent, reason="no dlc name match")
    if result is AMBIGUOUS:
        return Match("hold", addon_title, game_id=parent, reason="ambiguous dlc")
    if method == "equality":
        return Match("apply", addon_title, game_id=parent, dlc_id=result)
    return Match("hold", addon_title, game_id=parent, dlc_id=result, reason="containment only")


@dataclass
class OwnershipReport:
    """Outcome counts + the held/unmatched lists for manual review."""
    marked: int = 0
    already_owned: int = 0
    held: list[Match] = field(default_factory=list)
    unmatched: list[Match] = field(default_factory=list)
    marked_items: list[Match] = field(default_factory=list)


def mark_ownership(conn: sqlite3.Connection, addons, *, dry_run: bool = False,
                   include_flagged: bool = False) -> OwnershipReport:
    """Flip dlc.owned for scraped owned add-ons (0 -> 1 only; idempotent).

    `addons` is an iterable of dicts (scrape rows) or objects with a `.title`.
    Applies "apply" verdicts always, and "hold" verdicts only when
    include_flagged. Reports held/unmatched for review; inserts nothing. Writes
    nothing when dry_run (the caller owns commit).
    """
    library = [(r["id"], r["normalized_title"])
               for r in conn.execute("SELECT id, normalized_title FROM games")]
    dlc_by_game: dict[int, list[tuple[int, str]]] = {}
    for r in conn.execute("SELECT id, game_id, name FROM dlc"):
        dlc_by_game.setdefault(r["game_id"], []).append((r["id"], r["name"]))

    report = OwnershipReport()
    for addon in addons:
        title = addon["title"] if isinstance(addon, dict) else addon.title
        m = classify(title, library, dlc_by_game)
        apply_it = m.action == "apply" or (
            m.action == "hold" and include_flagged and m.dlc_id is not None)
        if apply_it:
            owned = conn.execute("SELECT owned FROM dlc WHERE id = ?", (m.dlc_id,)).fetchone()[0]
            if owned:
                report.already_owned += 1
            else:
                report.marked += 1
                report.marked_items.append(m)
                if not dry_run:
                    conn.execute("UPDATE dlc SET owned = 1 WHERE id = ?", (m.dlc_id,))
        elif m.action == "hold":
            report.held.append(m)
        elif m.action == "unmatched":
            report.unmatched.append(m)
    return report
