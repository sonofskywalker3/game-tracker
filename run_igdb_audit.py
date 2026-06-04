"""Run the IGDB match audit: self-heal authoritative (bundle) matches and flag
ambiguous ones for review. Use --dry-run to report counts without persisting.

    uv run python run_igdb_audit.py --dry-run
    uv run python run_igdb_audit.py
"""
import argparse
import logging

import config
import igdb_dlc
import igdb_match
from models import get_db

logger = logging.getLogger(__name__)


def run(*, dry_run: bool) -> dict[str, int]:
    """Execute the audit. Returns {'applied': N, 'flagged': M}. When dry_run is
    True the transaction is rolled back so nothing is persisted."""
    client_id, secret = config.get_twitch_credentials()
    if not client_id:
        raise RuntimeError("IGDB/Twitch credentials are not configured")
    token = igdb_dlc.get_access_token(client_id, secret)
    conn = get_db()
    try:
        result = igdb_match.audit_igdb_matches(conn, client_id=client_id, token=token)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    return {"applied": len(result["applied"]), "flagged": len(result["flagged"])}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report applied/flagged counts without persisting")
    args = parser.parse_args()
    summary = run(dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    logger.info("%saudit complete: %d applied (locked), %d flagged for review",
                prefix, summary["applied"], summary["flagged"])


if __name__ == "__main__":
    main()
