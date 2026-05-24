import bundles
import import_scraped as imp
import models


def _bundle(title, external_id, platform="Switch", source="nintendo"):
    return {"title": title, "platform": platform, "source": source,
            "external_id": external_id, "cover_url": None, "source_title": title}


def _insert(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))


def _insert_phantom(conn, title, source, external_id, *, curated=False):
    """Insert a phantom bundle row on a Switch platform; return its id."""
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name, category) "
                 "VALUES ('Nintendo Switch', 'Switch', 'modern_console')")
    pid = conn.execute("SELECT id FROM platforms WHERE short_name='Switch'").fetchone()[0]
    _insert(conn, title)
    bid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?,?,1)", (bid, pid))
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) VALUES (?,?,?)",
                 (bid, source, external_id))
    if curated:
        conn.execute("INSERT INTO user_ratings (game_id, status, rating) VALUES (?, 'completed', 4)", (bid,))
    else:
        conn.execute("INSERT INTO user_ratings (game_id, status) VALUES (?, 'backlog')", (bid,))
    conn.commit()
    return bid


def test_expand_bundle_returns_constituents_for_mapped_id():
    assert bundles.expand_bundle("nintendo", "70070000014767") == (
        "Edna & Harvey: Harvey's New Eyes",
        "Edna & Harvey: the Breakout - Anniversary Edition",
    )


def test_expand_bundle_single_constituent():
    # The "Frozen Hearth" DLC is dropped; only the base game remains.
    assert bundles.expand_bundle("xbox", "9P6KBLVP8V3G") == ("Nobody Saves the World",)


def test_expand_bundle_none_for_unknown_id():
    assert bundles.expand_bundle("nintendo", "DOESNOTEXIST") is None


def test_expand_bundle_source_must_match():
    assert bundles.expand_bundle("playstation", "70070000014767") is None


def test_expand_bundle_none_for_missing_external_id():
    assert bundles.expand_bundle("nintendo", None) is None


def test_expand_bundle_user_added_entries():
    assert bundles.expand_bundle("xbox", "BRW49CBS558D") == (
        "Batman: Arkham Asylum", "Batman: Arkham City")
    assert bundles.expand_bundle("xbox", "9NZJGLTJX1J1") == (
        "Borderlands", "Borderlands 2", "Borderlands: The Pre-Sequel", "Borderlands 3")
    assert bundles.expand_bundle("nintendo", "70070000014049") == (
        "Deponia", "Chaos on Deponia", "Goodbye Deponia")
    assert bundles.expand_bundle("xbox", "C4HB1XWT02DK") == (
        "Assassin's Creed Chronicles: China",
        "Assassin's Creed Chronicles: India",
        "Assassin's Creed Chronicles: Russia")
    # FF I-VI links the existing rows: constituents are the plain franchise titles.
    assert bundles.expand_bundle("nintendo", "70070000017105") == (
        "Final Fantasy", "Final Fantasy II", "Final Fantasy III",
        "Final Fantasy IV", "Final Fantasy V", "Final Fantasy VI")


def test_import_expands_bundle_creating_missing_constituents(temp_db):
    conn = models.get_db()
    stats = imp.import_games(
        conn, [_bundle('"Edna & Harvey" Bundle', "70070000014767")], "nintendo",
        confirm_fn=imp._safe_auto_confirm)
    conn.commit()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert not any("Bundle" in t for t in titles)        # phantom never created
    assert "Edna & Harvey: Harvey's New Eyes" in titles  # constituent created
    assert any("Breakout" in t for t in titles)
    assert stats.new_games == 2 and stats.bundles_expanded == 1
    conn.close()


def test_import_bundle_matches_existing_constituents(temp_db):
    conn = models.get_db()
    _insert(conn, "Pikmin 1")
    _insert(conn, "Pikmin 2")
    conn.commit()
    stats = imp.import_games(
        conn, [_bundle("Pikmin 1+2 Bundle", "70070000018036")], "nintendo",
        confirm_fn=imp._safe_auto_confirm)
    conn.commit()
    assert stats.new_games == 0 and stats.bundles_expanded == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    conn.close()


def test_import_bundle_is_idempotent(temp_db):
    conn = models.get_db()
    args = [_bundle("Portal: Companion Collection", "70070000013722")]
    imp.import_games(conn, args, "nintendo", confirm_fn=imp._safe_auto_confirm)
    conn.commit()
    imp.import_games(conn, args, "nintendo", confirm_fn=imp._safe_auto_confirm)
    conn.commit()
    # Portal + Portal 2 only; no phantom, no duplicates on re-import.
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    conn.close()


def test_cleanup_deletes_uncurated_phantom_and_creates_constituents(temp_db):
    conn = models.get_db()
    _insert_phantom(conn, "Pikmin 1+2 Bundle", "nintendo", "70070000018036")
    results = imp.cleanup_bundles(conn)
    conn.commit()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Pikmin 1+2 Bundle" not in titles            # phantom deleted
    assert "Pikmin 1" in titles and "Pikmin 2" in titles
    assert any(r["action"] == "deleted" for r in results)
    conn.close()


def test_cleanup_keeps_curated_phantom_but_still_creates_constituents(temp_db):
    conn = models.get_db()
    _insert_phantom(conn, "Pikmin 1+2 Bundle", "nintendo", "70070000018036", curated=True)
    results = imp.cleanup_bundles(conn)
    conn.commit()
    titles = {r[0] for r in conn.execute("SELECT title FROM games")}
    assert "Pikmin 1+2 Bundle" in titles                # kept (curated)
    assert "Pikmin 1" in titles and "Pikmin 2" in titles
    assert any(r["action"] == "kept_curated" for r in results)
    conn.close()


def test_cleanup_dry_run_writes_nothing(temp_db):
    conn = models.get_db()
    _insert_phantom(conn, "Pikmin 1+2 Bundle", "nintendo", "70070000018036")
    imp.cleanup_bundles(conn, dry_run=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1  # nothing changed
    conn.close()
