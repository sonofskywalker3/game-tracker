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
