import models
import import_scraped as imp


def _platform_id(conn, sn):
    return conn.execute("SELECT id FROM platforms WHERE short_name=?", (sn,)).fetchone()[0]


def _add_parent(conn, title, platform="Switch", *, status="backlog", rating=None):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (gid, _platform_id(conn, platform)))
    conn.execute("INSERT INTO user_ratings (game_id, status, rating) VALUES (?, ?, ?)",
                 (gid, status, rating))
    conn.commit()
    return gid


def _titles(conn):
    return {r[0] for r in conn.execute("SELECT title FROM games")}


def _collection_of(conn, title):
    r = conn.execute("SELECT collection_name FROM games WHERE title=?", (title,)).fetchone()
    return r[0] if r else None


def test_compilation_breaks_out_sets_collection_and_deletes_parent(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.apply_bundle_catalog(conn)
    titles = _titles(conn)
    assert "Mega Man Legacy Collection" not in titles
    assert "Mega Man" in titles and "Mega Man 2" in titles
    assert _collection_of(conn, "Mega Man") == "Mega Man Legacy Collection"
    assert _collection_of(conn, "Mega Man 2") == "Mega Man Legacy Collection"
    conn.close()


def test_entitlement_breaks_out_without_collection_name(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Pikmin 1plus2")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        models.normalize_title(models.clean_title("Pikmin 1plus2")):
            {"type": "entitlement", "constituents": ["Pikmin 1", "Pikmin 2"]}})
    imp.apply_bundle_catalog(conn)
    titles = _titles(conn)
    assert "Pikmin 1plus2" not in titles
    assert "Pikmin 1" in titles and "Pikmin 2" in titles
    assert _collection_of(conn, "Pikmin 1") is None
    assert _collection_of(conn, "Pikmin 2") is None
    conn.close()


def test_anthology_is_noop(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Atari 50")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "atari 50": {"type": "anthology", "constituents": []}})
    report = imp.apply_bundle_catalog(conn)
    assert "Atari 50" in _titles(conn)
    assert any(r["type"] == "anthology" and r["action"] == "kept" for r in report)
    conn.close()


def test_missing_parent_is_skipped(monkeypatch, temp_db):
    conn = models.get_db()
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "not owned collection": {"type": "compilation", "constituents": ["A", "B"]}})
    report = imp.apply_bundle_catalog(conn)
    assert report == []
    assert _titles(conn) == set()
    conn.close()


def test_unknown_type_is_skipped(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Weird Pack")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "weird pack": {"type": "bogus", "constituents": ["A"]}})
    report = imp.apply_bundle_catalog(conn)
    assert report == []
    assert "Weird Pack" in _titles(conn)
    conn.close()


def test_dry_run_writes_nothing(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.apply_bundle_catalog(conn, dry_run=True)
    assert "Mega Man Legacy Collection" in _titles(conn)
    assert "Mega Man" not in _titles(conn)
    conn.close()


def test_idempotent_second_run_noop(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection")
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    imp.apply_bundle_catalog(conn)
    before = _titles(conn)
    imp.apply_bundle_catalog(conn)
    assert _titles(conn) == before
    conn.close()


def test_ambiguous_curation_keeps_parent(monkeypatch, temp_db):
    conn = models.get_db()
    _add_parent(conn, "Mega Man Legacy Collection", status="completed", rating=4)
    monkeypatch.setattr(models, "load_bundle_catalog", lambda: {
        "mega man legacy collection": {"type": "compilation",
                                       "constituents": ["Mega Man", "Mega Man 2"]}})
    report = imp.apply_bundle_catalog(conn)
    assert "Mega Man Legacy Collection" in _titles(conn)
    assert any(r["action"] == "kept_ambiguous" for r in report)
    assert "Mega Man" in _titles(conn)
    conn.close()
