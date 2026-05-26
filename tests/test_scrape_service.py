import contextlib
import time
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


class _FakePage:
    def goto(self, url):
        pass

    def wait_for_timeout(self, ms):
        pass


@contextlib.contextmanager
def _fake_browser(headless=False):
    yield _FakePage(), []


def _wait_phase(target, timeout=3.0):
    """Poll status() until phase == target (or in target tuple); return reached."""
    targets = (target,) if isinstance(target, str) else tuple(target)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if scrape_service.status()["phase"] in targets:
            return True
        time.sleep(0.02)
    return False


def test_start_runs_full_flow(temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)

    def fake_collect(page, captured):
        return [
            ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                        source="playstation", external_id="G1"),
            ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone",
                        platform="PS5", source="playstation",
                        external_id="A1", kind="addon"),
        ]

    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=fake_collect)
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.signal_continue()
    assert _wait_phase("complete")
    st = scrape_service.status()
    assert st["summary"]["owned_marked"] == 1
    assert st["summary"]["new_games"] == 1


def test_cancel_before_continue(temp_db, monkeypatch):
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)
    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=lambda p, c: [])
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.cancel()
    assert _wait_phase("cancelled")
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    conn.close()


def test_start_rejects_unknown_vendor():
    ok, msg = scrape_service.start("steam")
    assert ok is False


def test_start_rejects_when_active():
    scrape_service._set(phase="scraping")
    ok, msg = scrape_service.start("xbox")
    assert ok is False


def test_run_pipeline_reports_added_owned_review(temp_db, monkeypatch):
    import igdb_dlc
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 ("The Witcher 3: Wild Hunt",
                  models.normalize_title(models.clean_title("The Witcher 3: Wild Hunt"))))
    gid = conn.execute("SELECT id FROM games WHERE title LIKE 'The Witcher%'").fetchone()[0]
    # pre-existing DLC, clearly created before this run
    conn.execute("INSERT INTO dlc (game_id, name, source, created_at) "
                 "VALUES (?, 'Hearts of Stone', 'igdb', '2000-01-01 00:00:00')", (gid,))
    conn.commit()
    conn.close()

    def fake_enrich(conn, *, client_id, token):
        for (g,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
            conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (g,))
            conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                         "VALUES (?, 'Blood and Wine', 'igdb')", (g,))
        conn.commit()
        return {"games": 1, "matched": 1, "added": 1, "errors": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_missing", fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    games = [
        ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                    source="playstation", external_id="G1"),
        # reconciles to the pre-existing IGDB row -> newly owned
        ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone", platform="PS5",
                    source="playstation", external_id="A1", kind="addon"),
        # confident parent, no matching row -> auto-created + owned this run
        ScrapedGame(title="The Witcher 3: Wild Hunt - Mystery Pack", platform="PS5",
                    source="playstation", external_id="A2", kind="addon"),
        # no parent in the library -> review
        ScrapedGame(title="Unknown Game - Bonus", platform="PS5",
                    source="playstation", external_id="A3", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()

    # added this run: IGDB "Blood and Wine" (not owned) + created "Mystery Pack" (owned).
    added = {d["name"]: d for d in summary["added_dlc"]}
    assert "Blood and Wine" in added and added["Blood and Wine"]["owned"] is False
    assert "Mystery Pack" in added and added["Mystery Pack"]["owned"] is True
    assert "Hearts of Stone" not in added  # created before this run

    owned_names = sorted(d["name"] for d in summary["newly_owned"])
    assert owned_names == ["Hearts of Stone", "Mystery Pack"]

    review_titles = [r["title"] for r in summary["review"]]
    assert review_titles == ["Unknown Game - Bonus"]
    assert summary["owned_marked"] == 2
    assert summary["created"] == 1
    assert len(summary["newly_owned"]) == summary["owned_marked"]
    conn.close()


def test_run_pipeline_steam_routes_to_steam_dlc(temp_db, monkeypatch):
    import steam_dlc
    import import_scraped

    # IGDB enrichment and the title matcher must NOT be called for steam.
    def _boom_enrich(conn):
        raise AssertionError("run_dlc_enrichment should not run for steam")

    def _boom_mark(conn, addons, **kw):
        raise AssertionError("mark_ownership should not run for steam")

    monkeypatch.setattr(import_scraped, "run_dlc_enrichment", _boom_enrich)
    import dlc_ownership
    monkeypatch.setattr(dlc_ownership, "mark_ownership", _boom_mark)

    def fetch(appid):
        return {
            620: {"type": "game", "name": "Portal 2", "dlc": [10, 20]},
            10:  {"type": "dlc", "name": "DLC A"},
            20:  {"type": "dlc", "name": "DLC B"},
        }.get(appid)
    real = steam_dlc.enrich_and_mark
    monkeypatch.setattr(steam_dlc, "enrich_and_mark",
                        lambda conn, owned, **kw: real(conn, owned, fetch=fetch))

    games = [
        ScrapedGame(title="Portal 2", platform="Steam", source="steam", external_id="620"),
        ScrapedGame(title="10", platform="Steam", source="steam", external_id="10", kind="addon"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "steam", games)
    conn.commit()
    assert summary["new_games"] == 1
    assert summary["owned_marked"] == 1     # appid 10 owned
    assert summary["created"] == 2          # DLC A + DLC B catalogue rows
    assert [d["name"] for d in summary["newly_owned"]] == ["DLC A"]
    added = {d["name"]: d["owned"] for d in summary["added_dlc"]}
    assert added == {"DLC A": True, "DLC B": False}
    assert summary["review"] == []
    conn.close()
