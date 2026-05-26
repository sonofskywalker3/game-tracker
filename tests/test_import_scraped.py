import json

import dlc_ownership
import igdb_dlc
import import_scraped
import models
import import_scraped as imp


def _add_existing_game(title, platform_short="PS4", category="modern_console",
                       status="playing", rating=4):
    """Insert a curated game the way the live DB holds one."""
    conn = models.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (platform_short, platform_short, category),
    )
    display = models.clean_title(title)
    norm = models.normalize_title(display)
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)", (display, norm))
    gid = conn.execute("SELECT id FROM games WHERE normalized_title = ?", (norm,)).fetchone()[0]
    pid = conn.execute("SELECT id FROM platforms WHERE short_name = ?", (platform_short,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)", (gid, pid))
    conn.execute("INSERT INTO user_ratings (game_id, status, rating) VALUES (?, ?, ?)", (gid, status, rating))
    conn.commit()
    conn.close()
    return gid


def _g(title, platform, source="playstation", external_id=None, cover_url=None):
    return {"title": title, "platform": platform, "source": source,
            "external_id": external_id, "cover_url": cover_url, "source_title": title}


def test_new_legacy_game_creates_legacy_platform(temp_db):
    conn = models.get_db()
    stats = imp.import_games(conn, [_g("Tomba", "PS3", external_id="X1")], "playstation")
    conn.commit()
    assert stats.new_games == 1
    cat = conn.execute("SELECT category FROM platforms WHERE short_name = 'PS3'").fetchone()[0]
    assert cat == "legacy_console"
    conn.close()


def test_external_id_match_survives_rename(temp_db):
    conn = models.get_db()
    imp.import_games(conn, [_g("NieR:Automata", "PS4", external_id="CUSA07")], "playstation")
    conn.commit()
    conn.execute("UPDATE games SET title = 'Nier', normalized_title = ?",
                 (models.normalize_title("Nier"),))
    conn.commit()
    stats = imp.import_games(conn, [_g("NieR:Automata", "PS4", external_id="CUSA07")], "playstation")
    conn.commit()
    assert stats.external_id_matches == 1
    assert stats.new_games == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_existing_curation_is_preserved(temp_db):
    gid = _add_existing_game("Hades", "PS4", status="completed", rating=4)
    conn = models.get_db()
    imp.import_games(conn, [_g("Hades", "PS4", external_id="C1")], "playstation")
    conn.commit()
    row = conn.execute("SELECT status, rating FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()
    assert row["status"] == "completed"
    assert row["rating"] == 4
    conn.close()


def test_import_is_idempotent(temp_db):
    conn = models.get_db()
    games = [_g("Celeste", "PS4", external_id="C2")]
    imp.import_games(conn, games, "playstation")
    conn.commit()
    stats = imp.import_games(conn, games, "playstation")
    conn.commit()
    assert stats.new_games == 0
    assert stats.platform_links_added == 0
    assert stats.external_ids_added == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_cross_vendor_unifies_into_one_game(temp_db):
    conn = models.get_db()
    imp.import_games(conn, [_g("Hades", "PS5", "playstation", external_id="P1")], "playstation")
    conn.commit()
    imp.import_games(conn, [_g("Hades", "Xbox", "xbox", external_id="X9")], "xbox")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    gid = conn.execute("SELECT id FROM games").fetchone()[0]
    plats = {r[0] for r in conn.execute(
        "SELECT p.short_name FROM platforms p "
        "JOIN game_platforms gp ON gp.platform_id = p.id WHERE gp.game_id = ?", (gid,))}
    assert plats == {"PS5", "Xbox"}
    assert conn.execute("SELECT COUNT(*) FROM game_external_ids WHERE game_id = ?", (gid,)).fetchone()[0] == 2
    conn.close()


def test_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    stats = imp.import_games(conn, [_g("Bastion", "PS4", external_id="B1")], "playstation", dry_run=True)
    conn.commit()
    assert stats.new_games == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    conn.close()


def test_fuzzy_confirm_merges(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        confirm_fn=lambda *a: True,
    )
    conn.commit()
    assert stats.fuzzy_confirmed == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_fuzzy_reject_creates_new(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        confirm_fn=lambda *a: False,
    )
    conn.commit()
    assert stats.fuzzy_rejected == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    conn.close()


def test_dry_run_reports_fuzzy_without_calling_confirm(temp_db):
    _add_existing_game("The Legend of Zelda: Breath of the Wild", "Switch")
    conn = models.get_db()
    calls = []
    stats = imp.import_games(
        conn,
        [_g("Legend of Zelda Breath of the Wild", "Switch", "nintendo", external_id="N1")],
        "nintendo",
        dry_run=True,
        confirm_fn=lambda *a: calls.append(a) or True,
    )
    conn.commit()
    assert len(stats.fuzzy_candidates) == 1
    assert calls == []  # confirm_fn must not be called in a dry run
    assert stats.fuzzy_confirmed == 0 and stats.fuzzy_rejected == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1  # nothing written
    conn.close()


def test_dry_run_dedups_same_batch_new_titles(temp_db):
    conn = models.get_db()
    # Same title twice in one batch, no external id: a real run unifies them into
    # one game, so the dry-run preview must report new_games == 1, not 2.
    games = [_g("Untracked Indie", "PS4"), _g("Untracked Indie", "PS4")]
    dry = imp.import_games(conn, games, "playstation", dry_run=True)
    conn.commit()
    assert dry.new_games == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    # A real run of the same batch indeed creates exactly one game.
    real = imp.import_games(conn, games, "playstation")
    conn.commit()
    assert real.new_games == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    conn.close()


def test_is_non_game_filters_apps_demos_and_dlc():
    # demos / trials
    assert imp.is_non_game("FINAL FANTASY XVI DEMO")
    assert imp.is_non_game("Stranger of Paradise TRIAL VERSION")
    assert imp.is_non_game("CODE VEIN Trial Edition")
    assert imp.is_non_game("Halo Infinite (Beta)")
    # apps
    assert imp.is_non_game("Netflix")
    assert imp.is_non_game("Amazon Prime Video")
    assert imp.is_non_game("SHAREfactory")
    # dlc / media
    assert imp.is_non_game("inFAMOUS Second Son Soundtrack")
    assert imp.is_non_game("FINAL FANTASY VII REMAKE Digital Artbook")
    assert imp.is_non_game("The Art of Horizon Zero Dawn")
    assert imp.is_non_game("Sea of Solitude - Unlock Key")
    assert imp.is_non_game("Ratchet & Clank: Rift Apart Bonus Content")
    # real games must NOT be caught
    assert not imp.is_non_game("Demon's Souls")
    assert not imp.is_non_game("Trials Rising")
    assert not imp.is_non_game("Alpha Protocol")
    assert not imp.is_non_game("The Jackbox Party Pack 3")
    assert not imp.is_non_game("Assassin's Creed Odyssey - Ultimate Edition")
    assert not imp.is_non_game("The Last of Us Remastered")


def test_skip_non_games_excludes_them(temp_db):
    conn = models.get_db()
    stats = imp.import_games(
        conn,
        [_g("Cool Game", "PS4", external_id="C1"),
         _g("Cool Game DEMO", "PS4", external_id="C1D"),
         _g("Netflix", "PS4", external_id="APP1")],
        "playstation",
    )
    conn.commit()
    assert stats.new_games == 1
    assert stats.skipped_non_games == 2
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Cool Game" in titles
    assert "Netflix" not in titles
    conn.close()


def test_safe_auto_confirm_merges_punctuation_not_real_differences():
    assert imp._safe_auto_confirm("NieR:Automata", "Nier: Automata", 0.96)
    assert not imp._safe_auto_confirm("Final Fantasy XV", "Final Fantasy XIV", 0.97)
    assert not imp._safe_auto_confirm("Life is Strange 2", "Life is Strange", 0.94)


# --- Thread C: manual "not a game" durable exclusions ---

def _write_excluded(monkeypatch, tmp_path, entries):
    path = tmp_path / "excluded_games.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(imp, "EXCLUDED_GAMES_PATH", path)
    return path


def test_load_excluded_games_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(imp, "EXCLUDED_GAMES_PATH", tmp_path / "nope.json")
    assert imp.load_excluded_games() == []


def test_add_excluded_games_writes_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr(imp, "EXCLUDED_GAMES_PATH", tmp_path / "excluded_games.json")
    entry = {"source": "nintendo", "external_id": "70010000123",
             "normalized_title": "island transfer tool", "title": "Island Transfer Tool"}
    assert imp.add_excluded_games([entry]) == 1
    assert imp.add_excluded_games([entry]) == 0  # already present, no duplicate
    assert imp.load_excluded_games() == [entry]


def test_is_excluded_by_source_and_external_id(tmp_path, monkeypatch):
    _write_excluded(monkeypatch, tmp_path, [
        {"source": "nintendo", "external_id": "ID1",
         "normalized_title": "transfer tool", "title": "Transfer Tool"}])
    # id match holds even if the title was later renamed.
    assert imp.is_excluded("nintendo", "ID1", "Whatever Renamed") is True
    # same id, different source -> not a match.
    assert imp.is_excluded("playstation", "ID1", "x") is False


def test_is_excluded_by_normalized_title(tmp_path, monkeypatch):
    _write_excluded(monkeypatch, tmp_path, [
        {"source": None, "external_id": None,
         "normalized_title": imp.match_key("Batman"), "title": "Batman"}])
    assert imp.is_excluded(None, None, "Batman") is True
    # title match applies regardless of whether the scraped row carries a source.
    assert imp.is_excluded("playstation", None, "Batman") is True


def test_is_excluded_false_for_unrelated(tmp_path, monkeypatch):
    _write_excluded(monkeypatch, tmp_path, [
        {"source": "nintendo", "external_id": "ID1",
         "normalized_title": "transfer tool", "title": "Transfer Tool"}])
    assert imp.is_excluded("nintendo", "OTHER", "A Real Game") is False


def test_import_skips_excluded_row(temp_db, tmp_path, monkeypatch):
    _write_excluded(monkeypatch, tmp_path, [
        {"source": "playstation", "external_id": "EXC1",
         "normalized_title": imp.match_key("Some Tool"), "title": "Some Tool"}])
    conn = models.get_db()
    stats = imp.import_games(conn, [
        _g("Some Tool", "PS4", external_id="EXC1"),
        _g("Real Game", "PS4", external_id="OK1"),
    ], "playstation")
    conn.commit()
    assert stats.new_games == 1            # only Real Game
    assert stats.skipped_excluded == 1
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Some Tool" not in titles and "Real Game" in titles
    conn.close()


def test_run_dlc_enrichment_skips_without_credentials(temp_db, monkeypatch):
    import config
    import models
    import import_scraped as imp
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: (None, None))
    conn = models.get_db()
    assert imp.run_dlc_enrichment(conn) is None
    conn.close()


def test_run_dlc_enrichment_populates_dlc(temp_db, monkeypatch):
    import config
    import igdb_dlc
    import models
    import import_scraped as imp
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('G', 'g')")
    conn.commit()
    monkeypatch.setattr(config, "get_twitch_credentials", lambda: ("cid", "sec"))
    # run_dlc_enrichment calls igdb_dlc.get_access_token — patch THAT binding.
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 3, "name": "G", "dlcs": [{"id": 1, "name": "P"}]}])
    totals = imp.run_dlc_enrichment(conn)
    assert totals["matched"] == 1 and totals["added"] == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1
    conn.close()


def test_partition_imports_games_and_marks_addon_ownership(temp_db):
    conn = models.get_db()
    # Existing game with an IGDB-sourced DLC row already present.
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 ("The Witcher 3: Wild Hunt",
                  models.normalize_title(models.clean_title("The Witcher 3: Wild Hunt"))))
    gid = conn.execute("SELECT id FROM games WHERE title LIKE 'The Witcher%'").fetchone()[0]
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, 'Hearts of Stone', 'igdb')", (gid,))
    conn.commit()
    conn.close()

    rows = [
        {"title": "The Witcher 3: Wild Hunt", "platform": "PS5", "source": "playstation",
         "external_id": "G1", "kind": "game"},
        {"title": "The Witcher 3: Wild Hunt - Hearts of Stone", "platform": "PS5",
         "source": "playstation", "external_id": "A1", "kind": "addon"},
    ]
    games_only = [g for g in rows if g.get("kind", "game") == "game"]
    addons = [g for g in rows if g.get("kind") == "addon"]

    conn = models.get_db()
    import_scraped.import_games(conn, games_only, "playstation",
                               confirm_fn=import_scraped._auto_confirm)
    report = dlc_ownership.mark_ownership(conn, addons)
    conn.commit()
    assert report.marked == 1
    owned = conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0]
    assert owned == 1
    conn.close()


def test_main_runs_steam_dlc_for_steam_files(temp_db, monkeypatch, tmp_path):
    import steam_dlc

    fake = {
        620: {"type": "game", "name": "Portal 2", "dlc": [10, 20]},
        10: {"type": "dlc", "name": "DLC A"},
        20: {"type": "dlc", "name": "DLC B"},
    }
    monkeypatch.setattr(steam_dlc, "fetch_appdetails", lambda appid, **kw: fake.get(appid))
    monkeypatch.setattr("config.get_twitch_credentials", lambda: (None, None))

    scrape = tmp_path / "steam_20260525.json"
    scrape.write_text(json.dumps({"source": "steam", "games": [
        {"title": "Portal 2", "platform": "Steam", "source": "steam",
         "external_id": "620", "kind": "game"},
        {"title": "10", "platform": "Steam", "source": "steam",
         "external_id": "10", "kind": "addon"},
    ]}), encoding="utf-8")

    import_scraped.main([str(scrape), "--auto-fuzzy"])

    conn = models.get_db()
    rows = {r["name"]: r["owned"] for r in conn.execute(
        "SELECT d.name, d.owned FROM dlc d JOIN games g ON g.id = d.game_id "
        "WHERE g.title = 'Portal 2'")}
    assert rows == {"DLC A": 1, "DLC B": 0}   # appid 10 owned, 20 not
    conn.close()


def test_main_runs_ownership_after_enrichment(temp_db, monkeypatch, tmp_path):
    # Mock IGDB enrichment so importing the base game populates a DLC row.
    def fake_enrich_missing(conn, *, client_id, token):
        for (gid,) in conn.execute("SELECT id FROM games WHERE igdb_id IS NULL").fetchall():
            conn.execute("UPDATE games SET igdb_id = 1 WHERE id = ?", (gid,))
            conn.execute("INSERT OR IGNORE INTO dlc (game_id, name, source) "
                         "VALUES (?, 'Hearts of Stone', 'igdb')", (gid,))
        conn.commit()
        return {"games": 1, "matched": 1, "added": 1, "errors": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_missing", fake_enrich_missing)
    monkeypatch.setattr("config.get_twitch_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(igdb_dlc, "get_access_token", lambda c, s: "tok")

    scrape = tmp_path / "playstation_20260525.json"
    scrape.write_text(json.dumps({"source": "playstation", "games": [
        {"title": "The Witcher 3: Wild Hunt", "platform": "PS5", "source": "playstation",
         "external_id": "G1", "kind": "game"},
        {"title": "The Witcher 3: Wild Hunt - Hearts of Stone", "platform": "PS5",
         "source": "playstation", "external_id": "A1", "kind": "addon"},
    ]}), encoding="utf-8")

    import_scraped.main([str(scrape), "--auto-fuzzy"])
    conn = models.get_db()
    owned = conn.execute("SELECT owned FROM dlc WHERE name='Hearts of Stone'").fetchone()[0]
    assert owned == 1
    conn.close()
