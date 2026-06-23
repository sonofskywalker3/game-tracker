"""enrichment.select_eligible_pairs + state helpers (uses temp_db)."""
import enrichment
import models


def _platform(conn, name, short, category="modern_console"):
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, short_name, category) VALUES (?, ?, ?)",
        (name, short, category))
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (short,)).fetchone()[0]


def _game(conn, title, *, igdb_id=None, cover="c.jpg"):
    conn.execute(
        "INSERT INTO games (title, normalized_title, igdb_id, cover_url) VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(title), igdb_id, cover))
    return conn.execute("SELECT id FROM games WHERE normalized_title=?",
                        (models.normalize_title(title),)).fetchone()[0]


def _own(conn, gid, pid):
    conn.execute("INSERT INTO game_platforms (game_id, platform_id, owned) VALUES (?, ?, 1)",
                 (gid, pid))


def test_selects_owned_pairs_without_upc(temp_db):
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    g = _game(conn, "Hades", igdb_id=7)
    _own(conn, g, sw)
    conn.commit()
    pairs = enrichment.select_eligible_pairs(conn)
    assert [(r["title"], r["short_name"], r["igdb_id"]) for r in pairs] == [("Hades", "Switch", 7)]
    conn.close()


def test_excludes_pair_already_in_registry(temp_db):
    import barcode
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    g = _game(conn, "Hades")
    _own(conn, g, sw)
    barcode.registry_put(conn, "111", game_id=g, platform="Switch", title="Hades")
    conn.commit()
    assert enrichment.select_eligible_pairs(conn) == []
    conn.close()


def test_excludes_pair_with_any_review_row(temp_db):
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    g = _game(conn, "Hades")
    _own(conn, g, sw)
    conn.execute("INSERT INTO upc_review (game_id, platform, status, reason) "
                 "VALUES (?, 'Switch', 'no_match', 'x')", (g,))
    conn.commit()
    assert enrichment.select_eligible_pairs(conn) == []
    conn.close()


def test_excludes_mobile_and_subscription_platforms(temp_db):
    conn = models.get_db()
    ios = _platform(conn, "iOS", "iOS", category="mobile")
    gp = _platform(conn, "Game Pass", "GamePass", category="subscription")
    g = _game(conn, "Hades")
    _own(conn, g, ios)
    _own(conn, g, gp)
    conn.commit()
    assert enrichment.select_eligible_pairs(conn) == []
    conn.close()


def test_limit_caps_rows(temp_db):
    conn = models.get_db()
    sw = _platform(conn, "Switch", "Switch")
    for t in ("A", "B", "C"):
        _own(conn, _game(conn, t), sw)
    conn.commit()
    assert len(enrichment.select_eligible_pairs(conn, limit=2)) == 2
    assert enrichment.count_eligible_pairs(conn) == 3
    conn.close()


def test_state_round_trips(temp_db):
    conn = models.get_db()
    assert enrichment.get_enrichment_state(conn) == {"last_run_date": None, "last_run_count": 0}
    enrichment.set_enrichment_state(conn, last_run_date="2026-06-23", last_run_count=42)
    assert enrichment.get_enrichment_state(conn) == {"last_run_date": "2026-06-23", "last_run_count": 42}
    conn.close()
