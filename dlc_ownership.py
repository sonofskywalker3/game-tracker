"""Match scraped owned add-ons to existing IGDB-sourced dlc rows and flip owned.

Pure helpers (parent_of, match_dlc, classify) are unit-tested; mark_ownership
orchestrates against a (temp or live) connection. Ownership is only ever set
0 -> 1, never 1 -> 0; the pass is idempotent. Add-ons that match no existing dlc
row are reported, never inserted (the dlc list stays IGDB-curated). See
docs/superpowers/specs/2026-05-25-dlc-scrape-ownership-design.md.
"""
from __future__ import annotations

import logging

import models

logger = logging.getLogger(__name__)

# Sentinel: more than one equally-plausible match (parent or dlc). Distinct from
# None ("no match at all").
AMBIGUOUS = "__ambiguous__"


def _norm(title: str | None) -> str:
    """Normalized match key, matching how games.normalized_title is stored
    (normalize_title(clean_title(...)))."""
    return models.normalize_title(models.clean_title(title or ""))


def parent_of(addon_title: str, library: list[tuple[int, str]]):
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
