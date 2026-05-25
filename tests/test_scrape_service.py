from pathlib import Path

import pytest

import models
import scrape_service
from scrapers.base import ScrapedGame


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the global scrape state around every test (it is module-level)."""
    scrape_service._reset()
    yield
    scrape_service._continue.set()
    scrape_service._cancel.set()
    scrape_service._reset()


def test_status_initial_shape():
    st = scrape_service.status()
    assert st["phase"] == "idle"
    assert st["vendor"] is None
    assert st["summary"] == {}


def test_vendors_constant():
    assert scrape_service.VENDORS == ("playstation", "xbox", "nintendo")


def test_backup_db_copies_when_present(temp_db):
    path = scrape_service.backup_db()
    assert path is not None
    assert Path(path).exists()
    assert ".bak-" in Path(path).name


def test_backup_db_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "DB_PATH", tmp_path / "nope.db")
    assert scrape_service.backup_db() is None


def _fake_enrich(conn, *, client_id, token):
    for (gid,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
        conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (gid,))
        conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                     "VALUES (?, 'Hearts of Stone', 'igdb')", (gid,))
    conn.commit()
    return {"games": 1, "matched": 1, "added": 1, "errors": 0}


def test_run_pipeline_imports_enriches_marks(temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    games = [
        ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                    source="playstation", external_id="G1"),
        ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone", platform="PS5",
                    source="playstation", external_id="A1", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()
    assert summary["new_games"] == 1
    assert summary["owned_marked"] == 1
    assert summary["dlc_added"] == 1
    assert summary["enrich_skipped"] is False
    assert summary["backup_path"] and Path(summary["backup_path"]).exists()
    assert conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0] == 1
    conn.close()


def test_run_pipeline_skips_enrich_without_creds(temp_db, monkeypatch):
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))
    games = [ScrapedGame(title="Hades", platform="PS5", source="playstation",
                         external_id="G2")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()
    assert summary["enrich_skipped"] is True
    assert summary["new_games"] == 1
    assert summary["owned_marked"] == 0
    conn.close()
