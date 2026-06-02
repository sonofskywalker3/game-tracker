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
