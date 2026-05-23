import json

import models


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_auto_populate_prefers_longest_prefix(temp_db):
    # "Cyberpunk 2077" must win over the shorter "Cyberpunk" prefix regardless of
    # the order patterns appear in the file (regression: sorted JSON shadowed it).
    conn = models.get_db()
    conn.executemany(
        "INSERT INTO games (title, normalized_title) VALUES (?, ?)",
        [("Cyberpunk 2077", "cyberpunk 2077"),
         ("Cyberpunk 2077: Phantom Liberty", "cyberpunk 2077 phantom liberty")],
    )
    conn.commit()
    conn.close()

    models.auto_populate_series()

    conn = models.get_db()
    names = [r["name"] for r in conn.execute(
        "SELECT s.name FROM games g JOIN user_ratings ur ON ur.game_id = g.id "
        "JOIN series s ON s.id = ur.series_id WHERE g.title LIKE 'Cyberpunk%'")]
    conn.close()
    assert names and all(n == "Cyberpunk 2077" for n in names)


def test_load_falls_back_to_default(tmp_path, monkeypatch):
    default = tmp_path / "series_patterns.default.json"
    user = tmp_path / "series_patterns.json"
    _write(default, {"Halo": "Halo"})
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", default)
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", user)
    assert models.load_series_patterns() == {"Halo": "Halo"}


def test_load_prefers_user_file(tmp_path, monkeypatch):
    default = tmp_path / "series_patterns.default.json"
    user = tmp_path / "series_patterns.json"
    _write(default, {"Halo": "Halo"})
    _write(user, {"Halo": "Halo", "Doom": "DOOM"})
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", default)
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", user)
    assert models.load_series_patterns() == {"Halo": "Halo", "Doom": "DOOM"}


def test_add_seeds_from_default_and_is_idempotent(tmp_path, monkeypatch):
    default = tmp_path / "series_patterns.default.json"
    user = tmp_path / "series_patterns.json"
    _write(default, {"Halo": "Halo"})
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", default)
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", user)
    assert models.add_series_pattern("SteamWorld", "SteamWorld") is True
    saved = json.loads(user.read_text(encoding="utf-8"))
    assert saved == {"Halo": "Halo", "SteamWorld": "SteamWorld"}
    assert models.add_series_pattern("SteamWorld", "SteamWorld") is False


def test_add_rejects_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SERIES_PATTERNS_DEFAULT_PATH", tmp_path / "d.json")
    monkeypatch.setattr(models, "SERIES_PATTERNS_PATH", tmp_path / "u.json")
    assert models.add_series_pattern("", "x") is False
    assert models.add_series_pattern("x", "  ") is False
