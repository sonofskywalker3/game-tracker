"""CSV export/import endpoints (Settings > Data Management)."""
import csv
import io

import models
import app as app_module


def _no_enrich(monkeypatch):
    monkeypatch.setattr(app_module, "get_twitch_credentials", lambda: (None, None))


def _add_game(conn, title, *, status="backlog", rating=None, priority=None,
              notes=None, platforms=()):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO user_ratings (game_id, status, rating, priority, notes) "
        "VALUES (?, ?, ?, ?, ?)", (gid, status, rating, priority, notes))
    for sn in platforms:
        pid = conn.execute("SELECT id FROM platforms WHERE short_name=?",
                           (sn,)).fetchone()[0]
        conn.execute("INSERT INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                     (gid, pid))
    conn.commit()
    return gid


def _parse_csv(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_export_returns_csv_download(client, temp_db):
    conn = models.get_db()
    _add_game(conn, "Hades", status="playing", rating=4, priority=8,
              notes="roguelike bliss", platforms=("Switch",))
    _add_game(conn, "Celeste", platforms=("Switch", "PC"))
    conn.close()

    resp = client.get("/api/data/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    assert "attachment" in resp.headers.get("Content-Disposition", "")

    rows = _parse_csv(resp.data)
    assert {r["title"] for r in rows} == {"Hades", "Celeste"}
    hades = next(r for r in rows if r["title"] == "Hades")
    assert hades["status"] == "playing"
    assert hades["rating"] == "4"
    assert hades["priority"] == "8"
    assert hades["notes"] == "roguelike bliss"
    assert hades["platforms"] == "Switch"
    celeste = next(r for r in rows if r["title"] == "Celeste")
    assert set(celeste["platforms"].split(",")) == {"Switch", "PC"}


def test_import_adds_games_with_fields(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    body = ("title,status,rating,priority,platforms,notes\r\n"
            "Hades,playing,4,8,\"Switch,PC\",great\r\n"
            "Celeste,,,,,\r\n")
    resp = client.post("/api/data/import", data={
        "file": (io.BytesIO(body.encode()), "library.csv")},
        content_type="multipart/form-data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["imported"] == 2
    assert data["skipped"] == 0
    assert data["errors"] == []

    conn = models.get_db()
    row = conn.execute(
        "SELECT ur.status, ur.rating, ur.priority, ur.notes FROM games g "
        "JOIN user_ratings ur ON ur.game_id = g.id WHERE g.title='Hades'").fetchone()
    assert (row["status"], row["rating"], row["priority"], row["notes"]) == \
        ("playing", 4, 8, "great")
    plats = {r[0] for r in conn.execute(
        "SELECT p.short_name FROM game_platforms gp JOIN platforms p "
        "ON p.id=gp.platform_id JOIN games g ON g.id=gp.game_id "
        "WHERE g.title='Hades'").fetchall()}
    assert plats == {"Switch", "PC"}
    celeste = conn.execute(
        "SELECT ur.status FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE g.title='Celeste'").fetchone()
    assert celeste["status"] == "backlog"   # default when column empty
    conn.close()


def test_import_skips_existing_and_reports_errors(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    conn = models.get_db()
    _add_game(conn, "Hades")
    conn.close()
    body = ("title,status\r\n"
            "Hades,playing\r\n"
            ",backlog\r\n"
            "Tunic,\r\n")
    resp = client.post("/api/data/import", data={
        "file": (io.BytesIO(body.encode()), "library.csv")},
        content_type="multipart/form-data")
    data = resp.get_json()
    assert data["imported"] == 1
    assert data["skipped"] == 1
    assert len(data["errors"]) == 1   # the title-less row, with its line number
    assert "2" in data["errors"][0] or "3" in data["errors"][0]


def test_import_without_file_is_400(client, temp_db):
    assert client.post("/api/data/import").status_code == 400


def test_import_without_title_column_is_400(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    body = "name,status\r\nHades,playing\r\n"
    resp = client.post("/api/data/import", data={
        "file": (io.BytesIO(body.encode()), "library.csv")},
        content_type="multipart/form-data")
    assert resp.status_code == 400


def test_export_import_round_trip(client, temp_db, monkeypatch):
    _no_enrich(monkeypatch)
    conn = models.get_db()
    _add_game(conn, "Hades", status="playing", rating=4, priority=8,
              notes='has "quotes", commas', platforms=("Switch",))
    conn.close()
    exported = client.get("/api/data/export").data

    # wipe and re-import
    conn = models.get_db()
    conn.execute("DELETE FROM games")
    conn.commit()
    conn.close()
    resp = client.post("/api/data/import", data={
        "file": (io.BytesIO(exported), "library.csv")},
        content_type="multipart/form-data")
    assert resp.get_json()["imported"] == 1

    conn = models.get_db()
    row = conn.execute(
        "SELECT ur.notes FROM games g JOIN user_ratings ur ON ur.game_id=g.id "
        "WHERE g.title='Hades'").fetchone()
    assert row["notes"] == 'has "quotes", commas'
    conn.close()
