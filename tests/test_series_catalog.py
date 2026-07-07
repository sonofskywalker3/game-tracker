import json

import models


def test_load_series_catalog_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "series_catalog.default.json"
    default.write_text(json.dumps({"halo": {"series": "Halo", "order": 1, "role": "mainline"}}),
                       encoding="utf-8")
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", tmp_path / "series_catalog.json")
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", default)
    assert models.load_series_catalog() == {"halo": {"series": "Halo", "order": 1, "role": "mainline"}}


def test_load_series_catalog_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "series_catalog.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "series_catalog.json"
    per_user.write_text(json.dumps({"doom": {"series": "DOOM"}}), encoding="utf-8")
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", per_user)
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", tmp_path / "series_catalog.default.json")
    assert models.load_series_catalog() == {"doom": {"series": "DOOM"}}


def test_load_series_catalog_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_series_catalog() == {}


def test_load_series_catalog_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "series_catalog.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "SERIES_CATALOG_PATH", tmp_path / "series_catalog.json")
    monkeypatch.setattr(models, "SERIES_CATALOG_DEFAULT_PATH", bad)
    assert models.load_series_catalog() == {}


def test_migrate_series_source_adds_column(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(user_ratings)").fetchall()}
    assert "series_source" in cols
    conn.close()


def test_migrate_series_source_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_series_source(conn)
    models.migrate_series_source(conn)  # second run must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(user_ratings)").fetchall()}
    assert "series_source" in cols
    conn.close()


def _seed_series(conn, name):
    conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return sid


def _add_game(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def _assign(conn, gid, sid, order=0, source=None):
    conn.execute(
        "INSERT INTO user_ratings (game_id, series_id, series_order, series_source) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(game_id) DO UPDATE SET series_id = excluded.series_id, "
        "series_order = excluded.series_order, series_source = excluded.series_source",
        (gid, sid, order, source))
    conn.commit()


def test_backfill_marks_prefix_match_as_auto(monkeypatch, temp_db):
    monkeypatch.setattr(models, "load_series_patterns", lambda: {"Halo": "Halo"})
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    gid = _add_game(conn, "Halo 2")
    _assign(conn, gid, sid, source=None)  # pre-existing, unstamped

    models.backfill_series_source(conn)

    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    assert src == "auto"  # current series == prefix result
    conn.close()


def test_backfill_marks_nonprefix_as_manual(monkeypatch, temp_db):
    monkeypatch.setattr(models, "load_series_patterns", lambda: {"Halo": "Halo"})
    conn = models.get_db()
    sid = _seed_series(conn, "Assassin's Creed")
    gid = _add_game(conn, "Brotherhood")  # does not start with "Assassin's Creed"
    _assign(conn, gid, sid, source=None)

    models.backfill_series_source(conn)

    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    assert src == "manual"  # human must have set it
    conn.close()


def test_backfill_leaves_already_stamped_rows(monkeypatch, temp_db):
    monkeypatch.setattr(models, "load_series_patterns", lambda: {"Halo": "Halo"})
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    gid = _add_game(conn, "Halo 2")
    _assign(conn, gid, sid, source="manual")  # already stamped manual

    models.backfill_series_source(conn)

    src = conn.execute("SELECT series_source FROM user_ratings WHERE game_id = ?", (gid,)).fetchone()[0]
    assert src == "manual"  # untouched
    conn.close()


def _assignment(conn, gid):
    r = conn.execute(
        "SELECT s.name AS series, ur.series_order, ur.series_source "
        "FROM user_ratings ur LEFT JOIN series s ON s.id = ur.series_id WHERE ur.game_id = ?",
        (gid,)).fetchone()
    return dict(r) if r else None


def _role(conn, gid):
    r = conn.execute("SELECT series_role, series_role_source FROM games WHERE id = ?",
                     (gid,)).fetchone()
    return dict(r)


def test_apply_creates_series_at_two_and_assigns(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man")
    b = _add_game(conn, "Mega Man 2")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man":   {"series": "Mega Man", "order": 1, "role": "mainline"},
        "mega man 2": {"series": "Mega Man", "order": 2, "role": "mainline"},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Mega Man"
    assert _assignment(conn, a)["series_order"] == 1
    assert _assignment(conn, a)["series_source"] == "catalog"
    assert _assignment(conn, b)["series_order"] == 2
    conn.close()


def test_apply_skips_singleton_new_series(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Yakuza Kiwami")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "yakuza kiwami": {"series": "Like a Dragon", "order": 1},
    })
    report = models.apply_series_catalog(conn)
    assert _assignment(conn, a) is None  # not assigned; no series created
    assert conn.execute("SELECT COUNT(*) FROM series WHERE name = 'Like a Dragon'").fetchone()[0] == 0
    assert any(r["action"] == "skipped_singleton" for r in report)
    conn.close()


def test_apply_joins_existing_series_even_singleton(monkeypatch, temp_db):
    conn = models.get_db()
    _seed_series(conn, "Assassin's Creed")
    a = _add_game(conn, "Brotherhood")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "brotherhood": {"series": "Assassin's Creed", "order": 3, "role": "spinoff"},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Assassin's Creed"
    assert _assignment(conn, a)["series_source"] == "catalog"
    conn.close()


def test_apply_fill_only_skips_manual_membership(monkeypatch, temp_db):
    conn = models.get_db()
    keep = _seed_series(conn, "Mario")
    a = _add_game(conn, "Brotherhood")
    _assign(conn, a, keep, order=9, source="manual")  # user pinned it elsewhere
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "brotherhood": {"series": "Assassin's Creed", "order": 3},
    })
    _seed_series(conn, "Assassin's Creed")  # exists so it could join, but manual lock wins
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Mario"  # manual lock respected
    assert _assignment(conn, a)["series_source"] == "manual"
    conn.close()


def test_apply_overrides_auto_membership(monkeypatch, temp_db):
    conn = models.get_db()
    wrong = _seed_series(conn, "Castlevania")
    _seed_series(conn, "Assassin's Creed")
    a = _add_game(conn, "Brotherhood")
    _assign(conn, a, wrong, order=1, source="auto")  # prefix-auto put it wrong
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "brotherhood": {"series": "Assassin's Creed", "order": 3},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series"] == "Assassin's Creed"  # re-homed
    assert _assignment(conn, a)["series_source"] == "catalog"
    conn.close()


def test_apply_writes_role_independent_of_membership(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man X")  # singleton series, won't be created
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man x": {"series": "Mega Man", "order": 1, "role": "spinoff"},
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a) is None       # membership skipped (singleton new series)
    assert _role(conn, a)["series_role"] == "spinoff"        # role still filled
    assert _role(conn, a)["series_role_source"] == "catalog"
    conn.close()


def test_apply_role_skips_manual(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man X")
    conn.execute("UPDATE games SET series_role = 'mainline', series_role_source = 'manual' WHERE id = ?",
                 (a,))
    conn.commit()
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man x": {"series": "Mega Man", "role": "spinoff"},
    })
    models.apply_series_catalog(conn)
    assert _role(conn, a)["series_role"] == "mainline"        # locked, untouched
    assert _role(conn, a)["series_role_source"] == "manual"
    conn.close()


def test_apply_absent_order_leaves_series_order(monkeypatch, temp_db):
    conn = models.get_db()
    sid = _seed_series(conn, "Halo")
    a = _add_game(conn, "Halo Wars")
    _assign(conn, a, sid, order=7, source="auto")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "halo wars": {"series": "Halo", "role": "spinoff"},  # no order
    })
    models.apply_series_catalog(conn)
    assert _assignment(conn, a)["series_order"] == 7  # preserved
    assert _assignment(conn, a)["series_source"] == "catalog"
    conn.close()


def test_apply_absent_entry_is_noop(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {})
    assert models.apply_series_catalog(conn) == []
    assert _assignment(conn, a) is None
    assert _role(conn, a)["series_role"] is None
    conn.close()


def test_apply_single_game_id(monkeypatch, temp_db):
    conn = models.get_db()
    _seed_series(conn, "Halo")
    a = _add_game(conn, "Halo 3")
    b = _add_game(conn, "Halo 4")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "halo 3": {"series": "Halo", "order": 3},
        "halo 4": {"series": "Halo", "order": 4},
    })
    models.apply_series_catalog(conn, game_id=a)
    assert _assignment(conn, a)["series"] == "Halo"
    assert _assignment(conn, b) is None  # other game untouched
    conn.close()


def test_apply_dry_run_writes_nothing_returns_report(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Mega Man")
    _add_game(conn, "Mega Man 2")
    monkeypatch.setattr(models, "load_series_catalog", lambda: {
        "mega man":   {"series": "Mega Man", "order": 1, "role": "mainline"},
        "mega man 2": {"series": "Mega Man", "order": 2, "role": "mainline"},
    })
    report = models.apply_series_catalog(conn, dry_run=True)
    assert _assignment(conn, a) is None  # nothing written
    assert _role(conn, a)["series_role"] is None
    assert conn.execute("SELECT COUNT(*) FROM series WHERE name = 'Mega Man'").fetchone()[0] == 0
    assert any(r["series"] == "Mega Man" and r["assigned"] == 2 for r in report)
    conn.close()
