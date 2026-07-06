"""models support for the IGDB bundle fallback: review-queue table + the
per-user bundle_catalog.json writer (runtime cache = the same file the seed
uses; one code path)."""
import json

import models


def _connect(temp_db):
    conn = models.get_db()
    return conn


# --- bundle_review_queue table ---------------------------------------------

def test_bundle_review_queue_table_exists(temp_db):
    conn = _connect(temp_db)
    conn.execute(
        "INSERT INTO bundle_review_queue (game_id, game_title, igdb_id, "
        "bundle_name, constituents_json, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (None, "Some Bundle", 146075, "Some Bundle on IGDB",
         json.dumps(["Game A", "Game B"]), "title_mismatch"))
    row = conn.execute(
        "SELECT game_title, igdb_id, bundle_name, constituents_json, reason, "
        "created_at, resolved_at, dismissed_at FROM bundle_review_queue").fetchone()
    assert row["game_title"] == "Some Bundle"
    assert row["igdb_id"] == 146075
    assert json.loads(row["constituents_json"]) == ["Game A", "Game B"]
    assert row["reason"] == "title_mismatch"
    assert row["created_at"] is not None
    assert row["resolved_at"] is None and row["dismissed_at"] is None
    conn.close()


def test_bundle_review_queue_game_delete_sets_null(temp_db):
    conn = _connect(temp_db)
    gid = conn.execute(
        "INSERT INTO games (title, normalized_title) VALUES ('B', 'b')").lastrowid
    conn.execute(
        "INSERT INTO bundle_review_queue (game_id, game_title, igdb_id, reason) "
        "VALUES (?, 'B', 1, 'no_constituents')", (gid,))
    conn.commit()
    conn.execute("DELETE FROM games WHERE id = ?", (gid,))
    conn.commit()
    row = conn.execute("SELECT game_id FROM bundle_review_queue").fetchone()
    assert row["game_id"] is None
    conn.close()


def test_migrate_bundle_review_queue_idempotent(temp_db):
    conn = _connect(temp_db)
    models.migrate_bundle_review_queue(conn)
    models.migrate_bundle_review_queue(conn)  # second run must not raise
    conn.close()


# --- add_bundle_catalog_entry ------------------------------------------------

def _patch_catalog_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "BUNDLE_CATALOG_PATH", tmp_path / "bundle_catalog.json")
    monkeypatch.setattr(models, "BUNDLE_CATALOG_DEFAULT_PATH",
                        tmp_path / "bundle_catalog.default.json")


def test_add_entry_seeds_user_file_from_default(monkeypatch, tmp_path):
    _patch_catalog_paths(monkeypatch, tmp_path)
    (tmp_path / "bundle_catalog.default.json").write_text(
        json.dumps({"existing bundle": {"type": "compilation",
                                        "constituents": ["A"]}}),
        encoding="utf-8")
    models.add_bundle_catalog_entry(
        "new bundle", {"type": "compilation", "constituents": ["X", "Y"]})
    written = json.loads((tmp_path / "bundle_catalog.json").read_text(encoding="utf-8"))
    assert written["existing bundle"] == {"type": "compilation", "constituents": ["A"]}
    assert written["new bundle"] == {"type": "compilation", "constituents": ["X", "Y"]}


def test_add_entry_updates_existing_user_file(monkeypatch, tmp_path):
    _patch_catalog_paths(monkeypatch, tmp_path)
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")
    (tmp_path / "bundle_catalog.json").write_text(
        json.dumps({"mine": {"type": "entitlement", "constituents": []}}),
        encoding="utf-8")
    models.add_bundle_catalog_entry(
        "added", {"type": "compilation", "constituents": ["Z"]})
    written = json.loads((tmp_path / "bundle_catalog.json").read_text(encoding="utf-8"))
    assert set(written) == {"mine", "added"}


def test_add_entry_visible_via_load_bundle_catalog(monkeypatch, tmp_path):
    _patch_catalog_paths(monkeypatch, tmp_path)
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")
    models.add_bundle_catalog_entry(
        "fresh", {"type": "compilation", "constituents": ["C1", "C2"]})
    assert models.load_bundle_catalog()["fresh"] == {
        "type": "compilation", "constituents": ["C1", "C2"]}


def test_add_entry_stable_format(monkeypatch, tmp_path):
    """sort_keys + indent 2 + ensure_ascii off + trailing newline (the catalog
    files' round-trip convention)."""
    _patch_catalog_paths(monkeypatch, tmp_path)
    (tmp_path / "bundle_catalog.default.json").write_text("{}", encoding="utf-8")
    models.add_bundle_catalog_entry("zeta", {"type": "compilation", "constituents": []})
    models.add_bundle_catalog_entry("alpha", {"type": "compilation", "constituents": []})
    text = (tmp_path / "bundle_catalog.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.index('"alpha"') < text.index('"zeta"')
