"""Combined CSV export from normalized scrape payloads."""
import csv
from pathlib import Path

from desktop.csv_export import COLUMNS, write_csv

_PS = {"source": "playstation", "games": [
    {"title": "Stray", "platform": "PS5", "source": "playstation", "kind": "game",
     "external_id": "123", "source_title": "Stray™", "cover_url": "http://c/1.png",
     "status_hint": None, "url_key": None},
]}
_XB = {"source": "xbox", "games": [
    {"title": "Halo", "platform": "XSX", "source": "xbox", "kind": "game",
     "external_id": None, "source_title": "Halo", "cover_url": None,
     "status_hint": None, "url_key": None},
]}


def test_combined_rows_and_columns(tmp_path: Path) -> None:
    out = tmp_path / "library.csv"
    assert write_csv([_PS, _XB], out) == 2
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == list(COLUMNS)
    assert rows[0]["title"] == "Stray" and rows[1]["source"] == "xbox"
    assert rows[1]["external_id"] == ""     # None -> empty cell, not "None"


def test_one_csv_per_vendor(tmp_path: Path) -> None:
    from desktop.csv_export import write_vendor_csvs
    counts = write_vendor_csvs([_PS, _XB], tmp_path)
    assert counts == {"playstation": 1, "xbox": 1}
    with (tmp_path / "backlogquest_playstation.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1 and rows[0]["title"] == "Stray"
    assert (tmp_path / "backlogquest_xbox.csv").exists()


def test_excel_bom(tmp_path: Path) -> None:
    out = tmp_path / "library.csv"
    write_csv([_PS], out)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
