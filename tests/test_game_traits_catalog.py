import json

import models


def test_load_game_traits_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "game_traits.default.json"
    default.write_text(json.dumps({"celeste": {"session_length": "short"}}), encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", default)
    assert models.load_game_traits() == {"celeste": {"session_length": "short"}}


def test_load_game_traits_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "game_traits.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "game_traits.json"
    per_user.write_text(json.dumps({"celeste": {"series_role": "mainline"}}), encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", per_user)
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", tmp_path / "game_traits.default.json")
    assert models.load_game_traits() == {"celeste": {"series_role": "mainline"}}


def test_load_game_traits_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_game_traits() == {}


def test_load_game_traits_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "game_traits.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH", bad)
    assert models.load_game_traits() == {}


TRAIT_COLUMNS = {
    "session_length", "session_length_source", "series_role", "series_role_source",
}


def test_migrate_game_traits_adds_columns(temp_db):
    conn = models.get_db()
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert TRAIT_COLUMNS <= cols
    conn.close()


def test_migrate_game_traits_idempotent(temp_db):
    conn = models.get_db()
    models.migrate_game_traits(conn)
    models.migrate_game_traits(conn)  # second run must not raise
    cols = {c[1] for c in conn.execute("PRAGMA table_info(games)").fetchall()}
    assert TRAIT_COLUMNS <= cols
    conn.close()


def _add_game(conn, title):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def _traits(conn, gid):
    r = conn.execute(
        "SELECT session_length, session_length_source, series_role, series_role_source "
        "FROM games WHERE id = ?", (gid,)).fetchone()
    return dict(r)


def test_apply_traits_catalog_sets_catalog_values(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short", "series_role": "mainline"}})
    models.apply_traits_catalog(conn)
    t = _traits(conn, gid)
    assert t == {"session_length": "short", "session_length_source": "catalog",
                 "series_role": "mainline", "series_role_source": "catalog"}
    conn.close()


def test_apply_traits_catalog_skips_manual(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    conn.execute("UPDATE games SET session_length = 'long', session_length_source = 'manual' "
                 "WHERE id = ?", (gid,))
    conn.commit()
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short", "series_role": "mainline"}})
    models.apply_traits_catalog(conn)
    t = _traits(conn, gid)
    assert t["session_length"] == "long" and t["session_length_source"] == "manual"  # locked, untouched
    assert t["series_role"] == "mainline" and t["series_role_source"] == "catalog"    # unlocked, set
    conn.close()


def test_apply_traits_catalog_absent_is_noop(monkeypatch, temp_db):
    conn = models.get_db()
    gid = _add_game(conn, "Celeste")
    monkeypatch.setattr(models, "load_game_traits", lambda: {})
    models.apply_traits_catalog(conn)
    assert _traits(conn, gid) == {"session_length": None, "session_length_source": None,
                                  "series_role": None, "series_role_source": None}
    conn.close()


def test_apply_traits_catalog_single_game(monkeypatch, temp_db):
    conn = models.get_db()
    a = _add_game(conn, "Celeste")
    b = _add_game(conn, "Hades")
    monkeypatch.setattr(models, "load_game_traits",
                        lambda: {"celeste": {"session_length": "short"},
                                 "hades": {"session_length": "short"}})
    models.apply_traits_catalog(conn, game_id=a)
    assert _traits(conn, a)["session_length"] == "short"
    assert _traits(conn, b)["session_length"] is None  # other game untouched
    conn.close()
