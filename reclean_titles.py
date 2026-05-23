"""Re-clean display titles in games.db with the current clean_title rules.

Workstream 2 (normalization), Part A. Display-only: updates games.title, never
normalized_title — recomputing the match key and merging the duplicates it
surfaces is the dedup workstream. Idempotent; --dry-run previews without writing.

    python reclean_titles.py --dry-run   # preview
    python reclean_titles.py             # apply

(Starting the app already applies this via models.migrate_db; this CLI is for
previewing the changes first and for applying them without launching Flask.)
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from typing import Optional

import models

logger = logging.getLogger(__name__)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Re-clean display titles in games.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview changes; write nothing")
    args = parser.parse_args(argv)

    if not models.DB_PATH.exists():
        logger.error("No database at %s — nothing to reclean.", models.DB_PATH)
        return

    # Note: we deliberately do NOT call migrate_db() here. migrate_db applies the
    # reclean itself, which would defeat --dry-run by writing before we preview.
    conn = models.get_db()
    changes = models.reclean_display_titles(conn, dry_run=args.dry_run)
    if not args.dry_run:
        conn.commit()
    conn.close()

    label = "WOULD CHANGE (dry run)" if args.dry_run else "CHANGED"
    logger.info("--- %s: %d display titles ---", label, len(changes))
    for change in changes:
        logger.info("  %s  ->  %s", change["original"], change["cleaned"])


if __name__ == "__main__":
    main()
