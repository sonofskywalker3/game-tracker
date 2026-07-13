"""Flatten normalized scrape payloads into one combined, Excel-friendly CSV."""
from __future__ import annotations

import csv
from pathlib import Path

COLUMNS: tuple[str, ...] = (
    "title", "platform", "source", "kind", "external_id", "source_title", "cover_url",
)


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
