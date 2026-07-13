"""Flatten normalized scrape payloads into Excel-friendly CSVs (one per vendor)."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS: tuple[str, ...] = (
    "title", "platform", "source", "kind", "external_id", "source_title", "cover_url",
)


def write_vendor_csvs(payloads: list[dict], out_dir: Path) -> dict[str, int]:
    """One CSV per vendor (backlogquest_<source>.csv) in out_dir; returns
    per-source row counts."""
    counts: dict[str, int] = {}
    for payload in payloads:
        source = str(payload.get("source", "unknown"))
        counts[source] = write_csv([payload], out_dir / f"backlogquest_{source}.csv")
    return counts


def write_csv(payloads: list[dict], out_path: Path) -> int:
    """Write every game from every payload; returns the number of rows."""
    rows = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for payload in payloads:
            for game in payload.get("games", []):
                writer.writerow({col: game.get(col) or "" for col in COLUMNS})
                rows += 1
    return rows
