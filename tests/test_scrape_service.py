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


@pytest.fixture(autouse=True)
def _stub_collections_sync(monkeypatch):
    """Keep pipeline tests offline: the post-import collections sync would
    otherwise hit IGDB whenever a test supplies Twitch creds. Tests that assert
    on the sync override this stub."""
    import igdb_resolve
    monkeypatch.setattr(
        igdb_resolve, "backfill_collections",
        lambda conn, cid, tok, progress=None: {"games": 0, "collections": 0,
                                               "memberships": 0})


def test_status_initial_shape():
    st = scrape_service.status()
    assert st["phase"] == "idle"
    assert st["vendor"] is None
    assert st["summary"] == {}


def test_vendors_constant():
    assert scrape_service.VENDORS == ("playstation", "xbox", "nintendo", "steam")


def test_steam_registered_in_scrapers():
    import scrape_libraries
    assert "steam" in scrape_libraries.SCRAPERS


def test_backup_db_copies_when_present(temp_db):
    path = scrape_service.backup_db()
    assert path is not None
    assert Path(path).exists()
    assert ".bak-" in Path(path).name


def test_backup_db_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "DB_PATH", tmp_path / "nope.db")
    assert scrape_service.backup_db() is None


def _fake_enrich(conn, *, client_id, token, progress=None):
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
    def goto(self, url, **kwargs):
        pass

    def set_default_navigation_timeout(self, ms):
        pass

    def wait_for_timeout(self, ms):
        pass

    def bring_to_front(self):
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

    def fake_collect(page, captured, progress=None):
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
                                 collect=lambda p, c, progress=None: [])
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.cancel()
    assert _wait_phase("cancelled")
    conn = models.get_db()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    conn.close()


def test_start_rejects_unknown_vendor():
    ok, msg = scrape_service.start("bogus")
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

    def fake_enrich(conn, *, client_id, token, progress=None):
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


def test_run_pipeline_reports_added_games(temp_db, monkeypatch):
    """The summary lists base games newly created this run (id + title)."""
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    # one pre-existing game (created before this run) must NOT be listed
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title, created_at) "
                 "VALUES ('Old Game', 'old game', '2000-01-01 00:00:00')")
    conn.commit()
    conn.close()

    games = [
        ScrapedGame(title="Hades", platform="PS5", source="playstation", external_id="G1"),
        ScrapedGame(title="Celeste", platform="PS5", source="playstation", external_id="G2"),
    ]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.commit()
    titles = sorted(g["title"] for g in summary["added_games"])
    assert titles == ["Celeste", "Hades"]
    assert all("id" in g and "title" in g for g in summary["added_games"])
    conn.close()


def test_psn_flow_marks_addons_and_stamps_marker(temp_db, monkeypatch):
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)

    base_pid = "UP0082-PPSA10664_00-FF16SIEA00000002"

    def fake_collect(page, captured, progress=None):
        return [ScrapedGame(title="The Witcher 3: Wild Hunt", platform="PS5",
                            source="playstation", external_id=base_pid)]

    addon_pid = "UP0082-PPSA10664_00-ADDCONT000000300"

    def fake_collect_addons(page, product_ids, captured, progress=None, should_cancel=None):
        assert product_ids == [base_pid]   # backfill: nothing synced yet
        return ([ScrapedGame(title="The Witcher 3: Wild Hunt - Hearts of Stone",
                             platform="PS5", source="playstation",
                             external_id=addon_pid, kind="addon")],
                [base_pid], {addon_pid: base_pid})

    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=fake_collect, collect_addons=fake_collect_addons)
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.signal_continue()
    assert _wait_phase("complete")
    st = scrape_service.status()
    assert st["summary"]["owned_marked"] == 1

    conn = models.get_db()
    synced = conn.execute(
        "SELECT g.psn_addons_synced_at FROM games g "
        "JOIN game_external_ids ge ON ge.game_id = g.id "
        "WHERE ge.source='playstation' AND ge.external_id = ?", (base_pid,)).fetchone()[0]
    conn.close()
    assert synced is not None   # marker stamped so future scrapes skip it


def test_psn_crossgen_addon_links_via_parent_map(temp_db, monkeypatch):
    """A PS4 add-on (different title-id) surfaced on the owned PS5 game's page must
    link to that game via parent-down, not orphan to review on a prefix miss."""
    import igdb_dlc
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")
    monkeypatch.setattr(scrape_service, "write_scrape", lambda *a, **k: None)

    ps5_base = "UP1063-PPSA07527_00-YSIXMONSTRNOXPS5"     # owned PS5 Ys IX
    ps4_addon = "UP1063-CUSA20414_00-YS09USDLC00N0152"    # PS4 DLC, different title-id

    def fake_collect(page, captured, progress=None):
        return [ScrapedGame(title="Ys IX: Monstrum Nox", platform="PS5",
                            source="playstation", external_id=ps5_base)]

    def fake_collect_addons(page, product_ids, captured, progress=None, should_cancel=None):
        return ([ScrapedGame(title="Bottled Potion Set", platform="PS4",
                             source="playstation", external_id=ps4_addon, kind="addon")],
                [ps5_base], {ps4_addon: ps5_base})

    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=fake_collect, collect_addons=fake_collect_addons)
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.signal_continue()
    assert _wait_phase("complete")
    assert scrape_service.status()["summary"]["owned_marked"] == 1

    conn = models.get_db()
    row = conn.execute(
        "SELECT g.title FROM dlc d JOIN games g ON g.id = d.game_id "
        "JOIN dlc_external_ids e ON e.dlc_id = d.id "
        "WHERE e.source = 'playstation' AND e.external_id = ?", (ps4_addon,)).fetchone()
    conn.close()
    assert row is not None and row["title"] == "Ys IX: Monstrum Nox"


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


def test_run_pipeline_resolves_xbox_addon_parent_via_catalog(temp_db, monkeypatch):
    import addon_parent
    from addon_parent import ParentRef
    # Skip IGDB enrichment (no creds) like test_run_pipeline_skips_enrich_without_creds.
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))
    # Inject a fake xbox resolver: addon 'AON1' -> parent game 'PARENTPID' = 'Rock Band 4'.
    monkeypatch.setitem(
        addon_parent.RESOLVERS, "xbox",
        lambda ids: {i: (ParentRef("PARENTPID", "Rock Band 4") if i == "AON1" else None) for i in ids})

    conn = models.get_db()
    games = [
        ScrapedGame(title="Some Game", platform="Xbox", source="xbox", external_id="G1"),
        ScrapedGame(title="Synth Track", platform="Xbox", source="xbox",
                    external_id="AON1", kind="addon", source_title="Synth Track"),
    ]
    summary = scrape_service._run_pipeline(conn, "xbox", games)
    conn.commit()
    # The owned add-on is linked to a newly-created Rock Band 4 game.
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE owned=1").fetchone()[0] == 1
    assert conn.execute("SELECT 1 FROM games WHERE title='Rock Band 4'").fetchone() is not None
    assert summary["owned_marked"] >= 1
    conn.close()


def test_run_pipeline_xbox_mixed_resolved_and_unresolved(temp_db, monkeypatch):
    import addon_parent
    from addon_parent import ParentRef
    # Skip IGDB enrichment (no creds).
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))
    # Fake xbox resolver: 'AON1' resolves to Rock Band 4; 'AON2' returns None.
    monkeypatch.setitem(
        addon_parent.RESOLVERS, "xbox",
        lambda ids: {i: (ParentRef("PARENTPID", "Rock Band 4") if i == "AON1" else None) for i in ids})

    conn = models.get_db()
    games = [
        ScrapedGame(title="Some Game", platform="Xbox", source="xbox", external_id="G1"),
        ScrapedGame(title="Synth Track", platform="Xbox", source="xbox",
                    external_id="AON1", kind="addon", source_title="Synth Track"),
        # Unresolved by the catalogue, and its title matches no library game,
        # so the name matcher leaves it for review ("no parent game").
        ScrapedGame(title="Random Unmatched Pack", platform="Xbox", source="xbox",
                    external_id="AON2", kind="addon", source_title="Random Unmatched Pack"),
    ]
    summary = scrape_service._run_pipeline(conn, "xbox", games)
    conn.commit()
    # Resolved add-on: owned DLC under a newly-created Rock Band 4 game.
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE owned=1").fetchone()[0] >= 1
    assert conn.execute("SELECT 1 FROM games WHERE title='Rock Band 4'").fetchone() is not None
    # Auto-created parent game counts toward new_games (Some Game + Rock Band 4).
    assert summary["new_games"] >= 2
    # Unresolved add-on fell through to the name matcher and is in the review queue.
    assert summary["review"], "expected the unresolved add-on in the review summary"
    open_review = conn.execute(
        "SELECT 1 FROM dlc_review_queue WHERE external_id = 'AON2' AND resolved_at IS NULL"
    ).fetchone()
    assert open_review is not None
    conn.close()


def test_psn_addon_pass_failure_still_persists_base_scrape(temp_db, monkeypatch):
    """A blown add-on pass must not sink the run: the already-scraped base library
    still gets written + imported (same protection the Nintendo DLC pass has)."""
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))
    written: dict = {}

    def fake_write(vendor, games):
        written["vendor"] = vendor
        written["games"] = list(games)

    monkeypatch.setattr(scrape_service, "write_scrape", fake_write)

    def fake_collect(page, captured, progress=None):
        return [ScrapedGame(title="Hades", platform="PS5",
                            source="playstation", external_id="G1")]

    def boom_addons(page, product_ids, captured, progress=None, should_cancel=None):
        raise RuntimeError("add-on pass exploded")

    ok, _ = scrape_service.start("playstation", browser_factory=_fake_browser,
                                 collect=fake_collect, collect_addons=boom_addons)
    assert ok
    assert _wait_phase("awaiting_login")
    scrape_service.signal_continue()
    assert _wait_phase("complete")
    assert written["vendor"] == "playstation"
    assert [g.title for g in written["games"]] == ["Hades"]
    st = scrape_service.status()
    assert st["summary"]["new_games"] == 1


def test_start_guard_atomic_before_thread_runs(monkeypatch):
    """Two back-to-back start() calls must not both pass the active guard: the
    phase flips to 'launching' inside start() itself (one lock acquisition), not
    only once the spawned thread gets scheduled."""
    from types import SimpleNamespace
    launches: list[int] = []

    class _InertThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            launches.append(1)

    monkeypatch.setattr(scrape_service, "threading",
                        SimpleNamespace(Thread=_InertThread))
    ok1, _ = scrape_service.start("playstation")
    ok2, msg = scrape_service.start("playstation")
    assert ok1 is True
    assert ok2 is False and "already running" in msg
    assert len(launches) == 1
    assert scrape_service.status()["phase"] == "launching"


def test_scrape_progress_updates_status_message():
    cb = scrape_service._scrape_progress("playstation")
    cb(150)
    st = scrape_service.status()
    assert st["phase"] == "scraping"
    assert "150 games" in st["message"]

    cb2 = scrape_service._scrape_progress("xbox")
    cb2(3)
    assert "3 pages" in scrape_service.status()["message"]


# --- collections sync in the shared pipeline (SP-A Stage 1 follow-up) ---------

def _creds(monkeypatch):
    import igdb_dlc
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")
    monkeypatch.setattr(igdb_dlc, "backfill_genres",
                        lambda conn, *, client_id, token, progress=None: 0)


def test_run_pipeline_syncs_collections(temp_db, monkeypatch):
    import igdb_dlc
    import igdb_resolve
    _creds(monkeypatch)
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)
    calls = []

    def fake_backfill(conn, cid, tok, progress=None):
        calls.append((cid, tok))
        return {"games": 3, "collections": 2, "memberships": 5}
    monkeypatch.setattr(igdb_resolve, "backfill_collections", fake_backfill)
    games = [ScrapedGame(title="Hades", platform="PS5", source="playstation",
                         external_id="G9")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.close()
    assert calls == [("cid", "tok")]
    assert summary["collections_synced"] == 3


def test_run_pipeline_collections_sync_runs_for_steam_too(temp_db, monkeypatch):
    """The sync lives in the shared launcher, not a vendor branch."""
    import igdb_resolve
    import steam_dlc
    _creds(monkeypatch)
    monkeypatch.setattr(
        steam_dlc, "enrich_and_mark",
        lambda conn, ids, progress=None: steam_dlc.SteamReport())
    calls = []
    monkeypatch.setattr(igdb_resolve, "backfill_collections",
                        lambda conn, cid, tok, progress=None: calls.append(1) or
                        {"games": 1, "collections": 1, "memberships": 1})
    games = [ScrapedGame(title="Hades", platform="PC", source="steam",
                         external_id="1145360")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "steam", games)
    conn.close()
    assert calls == [1]
    assert summary["collections_synced"] == 1


def test_run_pipeline_collections_skipped_without_creds(temp_db, monkeypatch):
    import igdb_resolve
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))

    def boom(*a, **k):
        raise AssertionError("collections sync must not run without creds")
    monkeypatch.setattr(igdb_resolve, "backfill_collections", boom)
    games = [ScrapedGame(title="Hades", platform="PS5", source="playstation",
                         external_id="G9")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.close()
    assert summary["collections_synced"] is None


def test_run_pipeline_collections_error_never_sinks_scrape(temp_db, monkeypatch):
    import igdb_dlc
    import igdb_resolve
    _creds(monkeypatch)
    monkeypatch.setattr(igdb_dlc, "enrich_missing", _fake_enrich)

    def boom(*a, **k):
        raise RuntimeError("igdb down")
    monkeypatch.setattr(igdb_resolve, "backfill_collections", boom)
    games = [ScrapedGame(title="Hades", platform="PS5", source="playstation",
                         external_id="G9")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.close()
    assert summary["new_games"] == 1          # the scrape itself completed
    assert summary["collections_synced"] is None


def test_run_pipeline_reports_session_unclassified(temp_db, monkeypatch):
    """The post-scrape modal nudges toward SP-C when new games lack a
    session length; the summary carries the count."""
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))
    games = [ScrapedGame(title="Totally Unknown Indie", platform="PS5",
                         source="playstation", external_id="G77")]
    conn = models.get_db()
    summary = scrape_service._run_pipeline(conn, "playstation", games)
    conn.close()
    assert summary["session_unclassified"] == 1
