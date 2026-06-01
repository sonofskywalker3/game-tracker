import json

import models


def test_load_bundle_catalog_reads_default(monkeypatch, tmp_path):
    default = tmp_path / "bundle_catalog.default.json"
    default.write_text(json.dumps({"mega man legacy collection":
                                   {"type": "compilation", "constituents": ["Mega Man"]}}),
                       encoding="utf-8")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", default)
    assert models.load_bundle_catalog() == {
        "mega man legacy collection": {"type": "compilation", "constituents": ["Mega Man"]}}


def test_load_bundle_catalog_prefers_per_user(monkeypatch, tmp_path):
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")
    per_user = tmp_path / "bundle_catalog.json"
    per_user.write_text(json.dumps({"x": {"type": "entitlement", "constituents": []}}),
                        encoding="utf-8")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", per_user)
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", tmp_path / "bundle_catalog.default.json")
    assert models.load_bundle_catalog() == {"x": {"type": "entitlement", "constituents": []}}


def test_load_bundle_catalog_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", tmp_path / "also-nope.json")
    assert models.load_bundle_catalog() == {}


def test_load_bundle_catalog_malformed_is_empty(monkeypatch, tmp_path):
    bad = tmp_path / "bundle_catalog.default.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH", bad)
    assert models.load_bundle_catalog() == {}
