"""IGDB bundle fallback: auto-split store bundles the local catalog misses.

When an import/add resolves a game to an IGDB entry with game_type == 3
(Bundle), this module decides what to do (see the SP-A phase-0 findings doc):

- bundle_catalog already knows the title -> expand through the SAME
  apply_bundle_catalog path the seed uses (local catalog knows).
- Unknown -> reverse-lookup the constituents on IGDB (finds out). A
  high-confidence result is cached into the per-user bundle_catalog.json and
  expanded immediately; a low-confidence one lands in bundle_review_queue for
  the owner to approve or dismiss (auto-with-review flow).

Confidence is deterministic: the local title must match the IGDB bundle name
exactly (a fuzzy identity match must never destroy the wrong row), the reverse
lookup must yield at least one base-game constituent, and oversized bundles
(subscription-style catalogs) always go to review.
"""
from __future__ import annotations

import json
import logging
import sqlite3

import igdb_dlc
import igdb_resolve
import import_scraped
import models

logger = logging.getLogger(__name__)

# IGDB game_type values a bundle constituent may have to be imported as a
# library row: main, standalone expansion, remake, remaster, expanded, port.
# Add-on types (DLC/expansion/pack) are dropped — the DLC pipeline owns those.
CONSTITUENT_GAME_TYPES = frozenset({0, 4, 8, 9, 10, 11})
# Bigger than any real boxed compilation in the catalog (Castlevania
# Anniversary = 8); a reverse lookup beyond this smells like a subscription
# catalog, so it goes to review instead of flooding the library.
MAX_AUTO_CONSTITUENTS = 15

_REASON_TITLE_MISMATCH = "title_mismatch"
_REASON_NO_CONSTITUENTS = "no_constituents"
_REASON_TOO_MANY = "too_many_constituents"


def _norm(title: str) -> str:
    return models.normalize_title(models.clean_title(title or ""))


def assess_bundle_split(local_norm_title: str, bundle_name: str,
                        constituents: list[dict]) -> tuple[str, list[str], str | None]:
    """Deterministic auto-vs-review verdict for one resolved bundle.

    Returns (verdict, constituent_names, reason): verdict "auto" (safe to split
    now) or "review" (queue for the owner), names filtered to base-game types
    in IGDB order, reason set only for "review".
    """
    names = [c.get("name") for c in constituents
             if c.get("name") and c.get("game_type") in CONSTITUENT_GAME_TYPES]
    if not names:
        return "review", [], _REASON_NO_CONSTITUENTS
    if local_norm_title != _norm(bundle_name):
        return "review", names, _REASON_TITLE_MISMATCH
    if len(names) > MAX_AUTO_CONSTITUENTS:
        return "review", names, _REASON_TOO_MANY
    return "auto", names, None


def _enrich_constituents(conn: sqlite3.Connection, names: list[str] | tuple[str, ...],
                         client_id: str | None, token: str | None) -> None:
    """Best-effort polish for freshly split constituents: IGDB identity + DLC +
    genres (enrich_game, fallback disabled so a mis-typed constituent can never
    recurse) and collection memberships. Errors are logged, never raised."""
    if not client_id or not token:
        return
    by_igdb: dict[int, int] = {}
    for cid in import_scraped._resolve_constituent_ids(conn, tuple(names)):
        row = conn.execute("SELECT igdb_id FROM games WHERE id = ?", (cid,)).fetchone()
        if row and not row["igdb_id"]:
            try:
                igdb_dlc.enrich_game(conn, cid, client_id, token, bundle_fallback=False)
                conn.commit()
            except Exception as exc:
                logger.warning("constituent enrich failed for game %s: %s", cid, exc)
                continue
            row = conn.execute("SELECT igdb_id FROM games WHERE id = ?", (cid,)).fetchone()
        if row and row["igdb_id"]:
            by_igdb[row["igdb_id"]] = cid
    if not by_igdb:
        return
    try:
        fetched = igdb_resolve.fetch_game_collections(list(by_igdb), client_id, token)
        for igdb_id, info in fetched.items():
            igdb_resolve.sync_game_collections(conn, by_igdb[igdb_id], info)
    except Exception as exc:
        logger.warning("constituent collections sync failed: %s", exc)


def _split_via_catalog(conn: sqlite3.Connection, norm_title: str, names: list[str],
                       client_id: str | None, token: str | None) -> list[dict]:
    """Expand one catalog entry through the shared seed path, then polish the
    constituents. One code path for seed + runtime, per the SP-A design."""
    report = import_scraped.apply_bundle_catalog(conn, only_titles={norm_title})
    _enrich_constituents(conn, names, client_id, token)
    return report


def _queue_review(conn: sqlite3.Connection, *, game_id: int, game_title: str,
                  igdb_id: int, bundle_name: str | None, constituents: list[str],
                  reason: str) -> bool:
    """Add one open review row; a still-open row for the same IGDB bundle is
    left alone (re-imports must not multiply the queue). Returns True if added."""
    open_row = conn.execute(
        "SELECT 1 FROM bundle_review_queue WHERE igdb_id = ? "
        "AND resolved_at IS NULL AND dismissed_at IS NULL", (igdb_id,)).fetchone()
    if open_row:
        return False
    conn.execute(
        "INSERT INTO bundle_review_queue (game_id, game_title, igdb_id, "
        "bundle_name, constituents_json, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (game_id, game_title, igdb_id, bundle_name,
         json.dumps(constituents, ensure_ascii=False), reason))
    conn.commit()
    return True


def handle_enriched_bundle(conn: sqlite3.Connection, game_id: int, igdb_payload: dict,
                           client_id: str | None, token: str | None) -> dict | None:
    """React to a game whose IGDB auto-resolution came back game_type == 3.

    Catalog hit -> expand it now. Miss -> reverse-lookup constituents; auto
    verdicts cache into the per-user catalog and split immediately, review
    verdicts queue for the owner. Returns an outcome dict ({"action": ...}) or
    None when the game row is gone.
    """
    row = conn.execute("SELECT title, normalized_title FROM games WHERE id = ?",
                       (game_id,)).fetchone()
    if not row:
        return None
    norm = row["normalized_title"]
    catalog = models.load_bundle_catalog()
    if norm in catalog:
        names = list(catalog[norm].get("constituents") or ())
        report = _split_via_catalog(conn, norm, names, client_id, token)
        return {"action": "applied_catalog", "constituents": names, "report": report}

    constituents = igdb_resolve.resolve_bundle_constituents(
        igdb_payload["id"], client_id, token)
    verdict, names, reason = assess_bundle_split(
        norm, igdb_payload.get("name") or "", constituents)
    if verdict == "auto":
        models.add_bundle_catalog_entry(norm, {
            "type": "compilation", "constituents": names,
            "source": "igdb", "igdb_id": igdb_payload["id"]})
        report = _split_via_catalog(conn, norm, names, client_id, token)
        logger.info("bundle fallback: auto-split %r into %d constituents",
                    row["title"], len(names))
        return {"action": "split", "constituents": names, "report": report}

    added = _queue_review(conn, game_id=game_id, game_title=row["title"],
                          igdb_id=igdb_payload["id"],
                          bundle_name=igdb_payload.get("name"),
                          constituents=names, reason=reason)
    logger.info("bundle fallback: %r -> review (%s)%s", row["title"], reason,
                "" if added else " [already queued]")
    return {"action": "queued", "reason": reason, "constituents": names}


def pending_reviews(conn: sqlite3.Connection) -> list[dict]:
    """Open review items, oldest first, with constituents decoded."""
    rows = conn.execute(
        "SELECT id, game_id, game_title, igdb_id, bundle_name, "
        "constituents_json, reason, created_at FROM bundle_review_queue "
        "WHERE resolved_at IS NULL AND dismissed_at IS NULL ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["constituents"] = json.loads(d.pop("constituents_json") or "[]")
        except json.JSONDecodeError:
            d["constituents"] = []
        out.append(d)
    return out


def approve_review(conn: sqlite3.Connection, review_id: int, *,
                   client_id: str | None, token: str | None,
                   constituents: list[str] | None = None) -> dict:
    """Approve one queued split: cache it into the per-user catalog and expand
    through the shared path, then mark the row resolved.

    `constituents` overrides the proposed list (the owner may edit it in the
    modal). Raises ValueError for a missing/closed row or an empty final list.
    """
    row = conn.execute(
        "SELECT id, game_id, game_title, igdb_id, constituents_json, "
        "resolved_at, dismissed_at FROM bundle_review_queue WHERE id = ?",
        (review_id,)).fetchone()
    if row is None:
        raise ValueError(f"bundle review {review_id} not found")
    if row["resolved_at"] is not None or row["dismissed_at"] is not None:
        raise ValueError(f"bundle review {review_id} is already closed")
    names = constituents if constituents is not None else json.loads(
        row["constituents_json"] or "[]")
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        raise ValueError("a bundle split needs at least one constituent title")

    game = conn.execute("SELECT normalized_title FROM games WHERE id = ?",
                        (row["game_id"],)).fetchone() if row["game_id"] else None
    norm = game["normalized_title"] if game else _norm(row["game_title"])
    models.add_bundle_catalog_entry(norm, {
        "type": "compilation", "constituents": names,
        "source": "igdb", "igdb_id": row["igdb_id"]})
    report = _split_via_catalog(conn, norm, names, client_id, token)
    conn.execute("UPDATE bundle_review_queue SET resolved_at = CURRENT_TIMESTAMP "
                 "WHERE id = ?", (review_id,))
    conn.commit()
    return {"action": "split", "constituents": names, "report": report}


def dismiss_review(conn: sqlite3.Connection, review_id: int) -> None:
    """Mark one review row dismissed (the phantom bundle row stays as-is)."""
    conn.execute("UPDATE bundle_review_queue SET dismissed_at = CURRENT_TIMESTAMP "
                 "WHERE id = ? AND resolved_at IS NULL AND dismissed_at IS NULL",
                 (review_id,))
    conn.commit()
