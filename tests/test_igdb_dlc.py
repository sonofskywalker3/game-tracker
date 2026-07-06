import igdb_dlc
import models


def test_dlc_fields_requests_nested_ids():
    # IGDB omits nested ids unless explicitly named; without these every stored
    # dlc.igdb_id would be NULL in production.
    for field in ("dlcs.id", "expansions.id", "standalone_expansions.id"):
        assert field in igdb_dlc._DLC_FIELDS


def test_dlc_fields_requests_genres():
    assert "genres.name" in igdb_dlc._DLC_FIELDS


def test_parse_genres_maps_to_canonical_names_and_dedupes():
    payload = {"id": 1, "name": "X", "genres": [
        {"name": "Shooter"}, {"name": "Role-playing (RPG)"},
        {"name": "Hack and slash/Beat 'em up"}, {"name": "Shooter"}]}
    # mapped to our tag vocabulary, order preserved, deduped
    assert igdb_dlc.parse_genres(payload) == ["Shooter", "RPG", "Action"]


def test_parse_genres_keeps_unmapped_name_and_handles_empty():
    assert igdb_dlc.parse_genres({"genres": [{"name": "Brawler"}]}) == ["Brawler"]
    assert igdb_dlc.parse_genres({"id": 1, "name": "x"}) == []


def test_igdb_query_passes_timeout(monkeypatch):
    """A hung IGDB connection must not block the enrichment thread forever:
    every _igdb_query POST carries the module timeout."""
    seen = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return []

    def fake_post(url, headers=None, data=None, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(igdb_dlc.requests, "post", fake_post)
    assert igdb_dlc._igdb_query("fields name;", "cid", "tok") == []
    assert seen["timeout"] == igdb_dlc.IGDB_TIMEOUT_SECONDS
    assert igdb_dlc.IGDB_TIMEOUT_SECONDS > 0


def _add_game_with_igdb(conn, title, igdb_id):
    conn.execute("INSERT INTO games (title, normalized_title, igdb_id) VALUES (?, ?, ?)",
                 (title, models.normalize_title(title), igdb_id))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def _genre_tags(conn, gid):
    return {r["name"] for r in conn.execute(
        "SELECT t.name FROM game_tags gt JOIN tags t ON t.id = gt.tag_id "
        "WHERE gt.game_id = ? AND t.category = 'genre'", (gid,))}


def test_enrich_game_stores_genres(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _add_game_with_igdb(conn, "Borderlands 4", 99)
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [
        {"id": 99, "name": "Borderlands 4",
         "genres": [{"name": "Shooter"}, {"name": "Role-playing (RPG)"}], "dlcs": []}])
    igdb_dlc.enrich_game(conn, gid, "c", "t")
    conn.commit()
    assert _genre_tags(conn, gid) == {"Shooter", "RPG"}
    conn.close()


def test_backfill_genres_covers_already_enriched_games(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _add_game_with_igdb(conn, "Tekken 8", 42)   # has igdb_id, no genre tags yet
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [
        {"id": 42, "name": "Tekken 8", "genres": [{"name": "Fighting"}]}])
    n = igdb_dlc.backfill_genres(conn, client_id="c", token="t")
    conn.commit()
    assert n >= 1 and _genre_tags(conn, gid) == {"Fighting"}
    # idempotent: a second pass finds nothing to do (already tagged)
    assert igdb_dlc.backfill_genres(conn, client_id="c", token="t") == 0
    conn.close()


def test_parse_flattens_dlcs_and_expansions_with_kind():
    payload = {
        "id": 1, "name": "Base Game",
        "dlcs": [{"id": 11, "name": "Pack A"}, {"id": 12, "name": "Pack B"}],
        "expansions": [{"id": 21, "name": "Big Expansion"}],
        "standalone_expansions": [{"id": 31, "name": "Standalone Ex"}],
    }
    out = igdb_dlc.parse_dlc_payload(payload)
    by_name = {d["name"]: d for d in out}
    assert by_name["Pack A"]["kind"] == "dlc" and by_name["Pack A"]["igdb_id"] == 11
    assert by_name["Big Expansion"]["kind"] == "expansion"
    assert by_name["Standalone Ex"]["kind"] == "expansion"
    assert len(out) == 4


def test_parse_drops_blanks_and_dedupes_by_name():
    payload = {
        "dlcs": [{"id": 1, "name": "Pack"}, {"id": 2, "name": "  "}, {"id": 3, "name": "Pack"}],
    }
    out = igdb_dlc.parse_dlc_payload(payload)
    assert [d["name"] for d in out] == ["Pack"]


def test_parse_empty_payload():
    assert igdb_dlc.parse_dlc_payload({"id": 1, "name": "x"}) == []


def test_slug_from_igdb_url():
    assert igdb_dlc.slug_from_igdb_url("https://www.igdb.com/games/elden-ring") == "elden-ring"
    assert igdb_dlc.slug_from_igdb_url("http://igdb.com/games/the-witcher-3") == "the-witcher-3"
    assert igdb_dlc.slug_from_igdb_url("https://www.igdb.com/games/elden-ring/dlc") == "elden-ring"


def test_slug_from_non_igdb_url_is_none():
    assert igdb_dlc.slug_from_igdb_url("https://images.igdb.com/igdb/co1.jpg") is None
    assert igdb_dlc.slug_from_igdb_url("https://example.com/cover.png") is None
    assert igdb_dlc.slug_from_igdb_url("") is None
    assert igdb_dlc.slug_from_igdb_url(None) is None


def test_format_cover_url():
    assert igdb_dlc.format_cover_url("//images.igdb.com/igdb/image/upload/t_thumb/co1.jpg") == \
        "https://images.igdb.com/igdb/image/upload/t_cover_big/co1.jpg"
    assert igdb_dlc.format_cover_url("https://x/t_thumb/co.jpg") == "https://x/t_cover_big/co.jpg"
    assert igdb_dlc.format_cover_url(None) is None
    assert igdb_dlc.format_cover_url("") is None


def _game(conn, title="Base"):
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(title)))
    return conn.execute("SELECT id FROM games WHERE title=?", (title,)).fetchone()[0]


def test_merge_inserts_then_is_idempotent_and_preserves_owned(temp_db):
    conn = models.get_db()
    gid = _game(conn)
    parsed = [{"name": "Pack A", "igdb_id": 11, "kind": "dlc"},
              {"name": "Expo", "igdb_id": 21, "kind": "expansion"}]
    r1 = igdb_dlc.merge_dlc(conn, gid, parsed)
    conn.commit()
    assert r1 == {"added": 2, "existing": 0}
    conn.execute("UPDATE dlc SET owned=1 WHERE game_id=? AND name='Pack A'", (gid,))
    conn.execute("INSERT INTO dlc (game_id, name, source) VALUES (?, 'My Manual', 'manual')", (gid,))
    conn.commit()
    r2 = igdb_dlc.merge_dlc(conn, gid, parsed + [{"name": "Pack B", "igdb_id": 12, "kind": "dlc"}])
    conn.commit()
    assert r2 == {"added": 1, "existing": 2}
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT name, owned, source FROM dlc WHERE game_id=?", (gid,))}
    assert rows["Pack A"][0] == 1
    assert "My Manual" in rows
    assert "Pack B" in rows
    conn.close()


def test_enrich_game_by_title_sets_igdb_id_cover_and_dlc(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _game(conn, "Base")
    conn.commit()
    payload = {"id": 999, "name": "Base", "slug": "base",
               "cover": {"url": "//img/t_thumb/co9.jpg"},
               "dlcs": [{"id": 11, "name": "Pack A"}]}
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [payload])
    rep = igdb_dlc.enrich_game(conn, gid, "cid", "tok")
    conn.commit()
    assert rep["matched"] and rep["added"] == 1
    g = conn.execute("SELECT igdb_id, cover_url FROM games WHERE id=?", (gid,)).fetchone()
    assert g["igdb_id"] == 999
    assert g["cover_url"] == "https://img/t_cover_big/co9.jpg"
    assert conn.execute("SELECT COUNT(*) FROM dlc WHERE game_id=?", (gid,)).fetchone()[0] == 1
    conn.close()


def test_enrich_game_no_match_returns_unmatched(temp_db, monkeypatch):
    conn = models.get_db()
    gid = _game(conn, "Nope")
    conn.commit()
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda q, c, t: [])
    rep = igdb_dlc.enrich_game(conn, gid, "cid", "tok")
    assert rep["matched"] is False and rep["added"] == 0
    conn.close()


def test_enrich_game_title_path_uses_resolver(temp_db, monkeypatch):
    import igdb_match
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title, collection_name) "
                 "VALUES ('Mega Man 2', 'mega man 2', 'Mega Man Legacy Collection 2')")
    conn.commit()
    gid = conn.execute("SELECT id FROM games WHERE title='Mega Man 2'").fetchone()[0]
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 1711, "name": "Mega Man 2",
                                         "cover_url": "https://x/t_cover_big/2.jpg",
                                         "source": "bundle"})
    monkeypatch.setattr(igdb_dlc, "fetch_game_by_id",
                        lambda iid, c, t: {"id": iid, "name": "Mega Man 2", "dlcs": []})
    report = igdb_dlc.enrich_game(conn, gid, "c", "t")
    assert report["matched"]
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?", (gid,)).fetchone()[0] == 1711
    conn.close()


def test_enrich_missing_is_incremental(temp_db, monkeypatch):
    conn = models.get_db()
    g1 = _game(conn, "One")
    g2 = _game(conn, "Two")
    conn.execute("UPDATE games SET igdb_id = 5 WHERE id = ?", (g2,))
    conn.commit()
    calls = []
    def fake_query(q, c, t):
        calls.append(q)
        return [{"id": 42, "name": "One", "dlcs": [{"id": 1, "name": "X"}]}]
    monkeypatch.setattr(igdb_dlc, "_igdb_query", fake_query)
    totals = igdb_dlc.enrich_missing(conn, client_id="cid", token="tok")
    assert totals["games"] == 1 and totals["matched"] == 1
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?", (g1,)).fetchone()[0] == 42
    conn.close()


def test_enrich_missing_skips_locked(temp_db, monkeypatch):
    import igdb_match
    conn = models.get_db()
    g_unlocked = _game(conn, "Unlocked Game")
    g_locked = _game(conn, "Locked Game")
    conn.execute("UPDATE games SET igdb_locked = 1 WHERE id = ?", (g_locked,))
    conn.commit()

    called_for = []
    def fake_resolve(title, *a, **k):
        called_for.append(title)
        return {"igdb_id": 77, "name": title, "cover_url": None, "source": "search"}
    monkeypatch.setattr(igdb_match, "resolve_identity", fake_resolve)
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: [{"id": 77, "name": "x", "dlcs": []}])

    igdb_dlc.enrich_missing(conn, client_id="cid", token="tok")

    assert conn.execute("SELECT igdb_id FROM games WHERE id=?",
                        (g_locked,)).fetchone()[0] is None
    assert conn.execute("SELECT igdb_id FROM games WHERE id=?",
                        (g_unlocked,)).fetchone()[0] == 77
    conn.close()


def test_enrich_game_title_path_handles_null_igdb_id(temp_db, monkeypatch):
    import igdb_match
    conn = models.get_db()
    gid = _game(conn, "Ghost Game")
    conn.commit()

    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": None, "name": "Ghost Game",
                                         "cover_url": None, "source": "search"})
    # _igdb_query should never be reached; make it raise to catch any accidental call
    monkeypatch.setattr(igdb_dlc, "_igdb_query",
                        lambda q, c, t: (_ for _ in ()).throw(AssertionError("unexpected query")))

    rep = igdb_dlc.enrich_game(conn, gid, "cid", "tok")
    assert rep["matched"] is False
    assert rep["added"] == 0
    conn.close()


def _rec():
    calls = []
    def cb(done, total=None, found=None):
        calls.append((done, total, found))
    cb.calls = calls
    return cb


def test_enrich_missing_progress_called_per_game(temp_db, monkeypatch):
    """progress fires once per game (including errors); done climbs; found tracks added."""
    conn = models.get_db()
    _game(conn, "Alpha")
    _game(conn, "Beta")
    conn.commit()

    def fake_enrich_game(c, gid, client_id, token, *, slug=None):
        return {"matched": True, "cover_set": False, "added": 1, "existing": 0}

    monkeypatch.setattr(igdb_dlc, "enrich_game", fake_enrich_game)
    rec = _rec()
    igdb_dlc.enrich_missing(conn, client_id="c", token="t", progress=rec)
    assert len(rec.calls) == 2
    dones = [c[0] for c in rec.calls]
    assert dones == [1, 2]
    totals_param = [c[1] for c in rec.calls]
    assert totals_param[0] == totals_param[1] == 2
    # found should equal running total of added (1 per game -> 1, then 2)
    founds = [c[2] for c in rec.calls]
    assert founds == [1, 2]
    conn.close()
