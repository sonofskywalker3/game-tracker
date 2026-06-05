import igdb_dlc
import models
import steam_dlc


# --- pure parsers ---

def test_parse_catalogue():
    assert steam_dlc.parse_catalogue({"dlc": [10, 20, 30]}) == [10, 20, 30]
    assert steam_dlc.parse_catalogue({}) == []


def test_parse_name_and_type():
    assert steam_dlc.parse_appdetails_name({"name": "  Season Pass "}) == "Season Pass"
    assert steam_dlc.parse_type({"type": "dlc"}) == "dlc"


# --- fetch_appdetails caching ---

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResp(self.payload)


def test_fetch_appdetails_caches(tmp_path):
    sess = _FakeSession({"620": {"success": True, "data": {"type": "game", "name": "Portal 2"}}})
    data = steam_dlc.fetch_appdetails(620, cache_dir=tmp_path, session=sess, delay_s=0)
    assert data["name"] == "Portal 2" and sess.calls == 1
    # second call is a cache hit -> no new network call
    data2 = steam_dlc.fetch_appdetails(620, cache_dir=tmp_path, session=sess, delay_s=0)
    assert data2["name"] == "Portal 2" and sess.calls == 1
    assert (tmp_path / "620.json").exists()


def test_fetch_appdetails_failure_returns_none(tmp_path):
    sess = _FakeSession({"99": {"success": False}})
    assert steam_dlc.fetch_appdetails(99, cache_dir=tmp_path, session=sess, delay_s=0) is None


# --- enrich_and_mark (temp DB, injected fetch) ---

def _seed_steam_game(conn, appid="620", title="Portal 2"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    gid = conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'steam', ?)", (gid, appid))
    return gid


def _fake_fetch(catalogue_map):
    return lambda appid: catalogue_map.get(appid)


def test_enrich_and_mark_creates_catalogue_and_marks_owned(temp_db):
    conn = models.get_db()
    gid = _seed_steam_game(conn)
    conn.commit()
    fetch = _fake_fetch({
        620: {"type": "game", "name": "Portal 2", "dlc": [10, 20]},
        10: {"type": "dlc", "name": "DLC A"},
        20: {"type": "dlc", "name": "DLC B"},
    })
    rep = steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    assert rep.games == 1 and rep.catalogue_added == 2 and rep.owned_marked == 1
    rows = {r["name"]: r["owned"] for r in conn.execute(
        "SELECT name, owned FROM dlc WHERE game_id=?", (gid,))}
    assert rows == {"DLC A": 1, "DLC B": 0}
    ext = {r["external_id"] for r in conn.execute(
        "SELECT external_id FROM dlc_external_ids WHERE source='steam'")}
    assert ext == {"10", "20"}
    conn.close()


def test_enrich_and_mark_idempotent(temp_db):
    conn = models.get_db()
    _seed_steam_game(conn)
    conn.commit()
    fetch = _fake_fetch({620: {"type": "game", "dlc": [10]}, 10: {"name": "DLC A"}})
    steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    rep = steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    assert rep.catalogue_added == 0 and rep.owned_marked == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc_external_ids").fetchone()[0] == 1
    conn.close()


def test_enrich_and_mark_reconciles_existing_row_by_name(temp_db):
    conn = models.get_db()
    gid = _seed_steam_game(conn)
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, 'DLC A', 'igdb')", (gid,))
    conn.commit()
    fetch = _fake_fetch({620: {"type": "game", "dlc": [10]}, 10: {"name": "DLC A"}})
    rep = steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch)
    conn.commit()
    assert rep.catalogue_added == 0 and rep.owned_marked == 1
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 1
    assert conn.execute("SELECT owned FROM dlc WHERE name='DLC A'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT dlc_id FROM dlc_external_ids WHERE source='steam' AND external_id='10'"
    ).fetchone() is not None
    conn.close()


def test_enrich_missing_skips_steam_games(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Steamy', 'steamy')")
    sid = conn.execute("SELECT id FROM games WHERE title='Steamy'").fetchone()[0]
    conn.execute("INSERT INTO game_external_ids (game_id, source, external_id) "
                 "VALUES (?, 'steam', '620')", (sid,))
    conn.execute("INSERT INTO games (title, normalized_title) VALUES ('Other', 'other')")
    oid = conn.execute("SELECT id FROM games WHERE title='Other'").fetchone()[0]
    conn.commit()

    seen = []

    def fake_enrich_game(c, gid, client_id, token, *, slug=None):
        seen.append(gid)
        return {"matched": False, "cover_set": False, "added": 0, "existing": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_game", fake_enrich_game)
    igdb_dlc.enrich_missing(conn, client_id="c", token="t")
    assert oid in seen and sid not in seen
    conn.close()


def test_enrich_and_mark_skips_game_on_fetch_error(temp_db):
    import requests
    conn = models.get_db()
    _seed_steam_game(conn)
    conn.commit()

    def boom(appid):
        raise requests.ConnectionError("network down")

    rep = steam_dlc.enrich_and_mark(conn, set(), fetch=boom)
    conn.commit()
    assert rep.errors == 1 and rep.games == 0 and rep.catalogue_added == 0
    assert conn.execute("SELECT COUNT(*) FROM dlc").fetchone()[0] == 0
    conn.close()


def _rec():
    calls = []
    def cb(done, total=None, found=None):
        calls.append((done, total, found))
    cb.calls = calls
    return cb


def test_enrich_and_mark_progress_called_per_game(temp_db):
    """progress fires once per steam game with climbing done/total/catalogue_added."""
    conn = models.get_db()
    _seed_steam_game(conn, appid="620", title="Portal 2")
    _seed_steam_game(conn, appid="730", title="CS:GO")
    conn.commit()
    fetch = _fake_fetch({
        620: {"type": "game", "name": "Portal 2", "dlc": [10]},
        10:  {"type": "dlc", "name": "DLC A"},
        730: {"type": "game", "name": "CS:GO", "dlc": []},
    })
    rec = _rec()
    steam_dlc.enrich_and_mark(conn, {10}, fetch=fetch, progress=rec)
    conn.commit()
    assert len(rec.calls) == 2
    dones = [c[0] for c in rec.calls]
    assert dones == [1, 2]
    totals_col = [c[1] for c in rec.calls]
    assert totals_col[0] == totals_col[1] == 2
    # catalogue_added accumulates: after Portal 2 it is 1, after CS:GO still 1
    founds = [c[2] for c in rec.calls]
    assert founds[0] == 1 and founds[1] == 1
    conn.close()
