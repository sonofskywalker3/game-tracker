import igdb_match
import models


def _cand(name, plats, *, cover=True, rating=0, year=2000, igdb_id=1):
    return {"id": igdb_id, "name": name, "platforms": list(plats),
            "cover": {"url": "//x/t_thumb/a.jpg"} if cover else None,
            "total_rating_count": rating, "first_release_date": year}


def test_score_prefers_console_over_mobile_for_retro_constituent():
    # game is a Switch bundle constituent; IGDB has the canonical NES entry (no
    # Switch) and a mobile-only port. NES must win because mobile is penalized.
    NES, IOS, SWITCH = 18, igdb_match.IOS_ID, 130
    cands = [
        _cand("Mega Man 2", [IOS], igdb_id=999),       # mobile-only port
        _cand("Mega Man 2", [NES], rating=80, igdb_id=1711),  # canonical
    ]
    ranked = igdb_match.score_candidates(cands, game_platform_ids={SWITCH})
    assert ranked[0]["id"] == 1711
    assert ranked[0]["_mobile_only"] is False


def test_score_uses_platform_overlap_for_standalone():
    PS5 = 167
    cands = [
        _cand("Returnal", [18], rating=10, igdb_id=1),       # wrong platform
        _cand("Returnal", [PS5], rating=10, igdb_id=2),      # overlaps game
    ]
    ranked = igdb_match.score_candidates(cands, game_platform_ids={PS5})
    assert ranked[0]["id"] == 2


def test_score_drops_non_title_matches():
    cands = [_cand("Totally Different Game", [18], igdb_id=9)]
    assert igdb_match.score_candidates(cands, game_platform_ids=set(),
                                       title="Celeste") == []


def test_fetch_candidates_queries_with_platforms(monkeypatch):
    seen = {}
    def fake(query, cid, tok):
        seen["q"] = query
        return [{"id": 1, "name": "Celeste", "platforms": [6],
                 "cover": {"url": "//x/t_thumb/a.jpg"}}]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    out = igdb_match.fetch_candidates("Celeste", "c", "t")
    assert "search \"Celeste\"" in seen["q"]
    assert "platforms" in seen["q"]
    assert out[0]["name"] == "Celeste"


def test_cover_url_of_normalizes_to_big_https():
    c = {"cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/abc.jpg"}}
    assert igdb_match.cover_url_of(c) == \
        "https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg"
    assert igdb_match.cover_url_of({"cover": None}) is None


def test_resolve_bundle_picks_game_type_3_platform_preferred(monkeypatch):
    def fake(query, cid, tok):
        assert 'search "Mega Man Legacy Collection 2"' in query
        return [
            {"id": 1, "name": "Mega Man Legacy Collection 1 + 2",
             "game_type": 3, "platforms": [130]},     # wrong product
            {"id": 28323, "name": "Mega Man Legacy Collection 2",
             "game_type": 3, "platforms": [48, 6, 49, 130]},  # exact + Switch
            {"id": 7, "name": "Mega Man Legacy Collection 2 OST",
             "game_type": 0, "platforms": [130]},     # not a bundle
        ]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    bid = igdb_match.resolve_bundle("Mega Man Legacy Collection 2", {130}, "c", "t")
    assert bid == 28323


def test_bundle_constituents_reverse_lookup(monkeypatch):
    def fake(query, cid, tok):
        assert "where bundles = (28323)" in query
        return [
            {"id": 1720, "name": "Mega Man 7", "platforms": [19],
             "cover": {"url": "//x/t_thumb/7.jpg"}},
            {"id": 1721, "name": "Mega Man 8", "platforms": [7],
             "cover": {"url": "//x/t_thumb/8.jpg"}},
        ]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    cons = igdb_match.bundle_constituents(28323, "c", "t")
    assert {c["normalized_title"] for c in cons} == {"mega man 7", "mega man 8"}
    assert cons[0]["cover_url"].endswith("t_cover_big/7.jpg")


def test_resolve_identity_bundle_first(monkeypatch):
    calls = []
    def fake(query, cid, tok):
        calls.append(query)
        if "game_type" in query and "search" in query:           # resolve_bundle
            return [{"id": 28323, "name": "Mega Man Legacy Collection 2",
                     "game_type": 3, "platforms": [130]}]
        if "where bundles = (28323)" in query:                   # constituents
            return [{"id": 1711, "name": "Mega Man 2", "platforms": [18],
                     "cover": {"url": "//x/t_thumb/2.jpg"}}]
        # fetch_candidates IS called; it returns the same candidate so dedup
        # suppresses it, and the bundle result still wins.
        return [{"id": 1711, "name": "Mega Man 2", "platforms": [18],
                 "cover": {"url": "//x/t_thumb/2.jpg"}}]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity(
        "Mega Man 2", {130}, "Mega Man Legacy Collection 2", "c", "t")
    assert got["igdb_id"] == 1711
    assert got["source"] == "bundle"
    assert got["cover_url"].endswith("t_cover_big/2.jpg")
    # Confirm dedup: candidates_for returns exactly ONE entry (the bundle one)
    # because the duplicate search result is filtered out by the seen-set.
    cands = igdb_match.candidates_for(
        "Mega Man 2", {130}, "Mega Man Legacy Collection 2", "c", "t")
    assert len(cands) == 1


def test_resolve_identity_falls_back_to_scorer(monkeypatch):
    def fake(query, cid, tok):
        if "search \"Celeste\"" in query and "cover.url" in query:  # fetch_candidates only
            return [{"id": 5, "name": "Celeste", "platforms": [6],
                     "cover": {"url": "//x/t_thumb/c.jpg"},
                     "total_rating_count": 50}]
        return []
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity("Celeste", {6}, None, "c", "t")
    assert got["igdb_id"] == 5 and got["source"] == "search"


def test_migrate_igdb_review_adds_columns(temp_db):
    conn = models.get_db()
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)")]
    assert "igdb_locked" in cols and "needs_igdb_review" in cols
    conn.close()



def test_cover_stem_ignores_size_and_extension():
    base = "https://images.igdb.com/igdb/image/upload/"
    assert igdb_match._cover_stem(base + "t_thumb/co1zyu.jpg") == "co1zyu"
    assert igdb_match._cover_stem(base + "t_cover_big/co1zyu.webp") == "co1zyu"
    assert igdb_match._cover_stem("//x/t_cover_big/co1zyu.png") == "co1zyu"
    assert igdb_match._cover_stem(None) is None
    assert igdb_match._cover_stem("") is None


def test_resolve_identity_bundle_resolves_but_no_constituent_match_falls_through(monkeypatch):
    # The bundle is found and has constituents, but none match the game title.
    # resolve_identity must fall through to the search scorer and return its result.
    def fake(query, cid, tok):
        # fetch_candidates uniquely has both "cover.url" AND "search"; bundle_constituents
        # has "cover.url" but no "search"; resolve_bundle has "search" but no "cover.url".
        if "cover.url" in query and "search" in query:           # fetch_candidates
            return [{"id": 777, "name": "Returnal", "platforms": [167],
                     "cover": {"url": "//x/t_thumb/r.jpg"},
                     "total_rating_count": 100, "first_release_date": 2021}]
        if "game_type" in query and "search" in query:           # resolve_bundle
            return [{"id": 99, "name": "Some Bundle Pack",
                     "game_type": 3, "platforms": [167]}]
        if "where bundles = (99)" in query:                      # constituents — no title match
            return [{"id": 555, "name": "Totally Different Game", "platforms": [167],
                     "cover": {"url": "//x/t_thumb/x.jpg"}}]
        return []
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity("Returnal", {167}, "Some Bundle Pack", "c", "t")
    assert got is not None
    assert got["igdb_id"] == 777
    assert got["source"] == "search"


def test_fetch_entry_queries_by_id(monkeypatch):
    seen = {}
    def fake(query, cid, tok):
        seen["q"] = query
        return [{"id": 1711, "name": "Mega Man 2", "platforms": [18],
                 "cover": {"url": "//x/t_thumb/2.jpg"}, "total_rating_count": 80}]
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    entry = igdb_match.fetch_entry(1711, "c", "t")
    assert "where id = 1711" in seen["q"]
    assert "limit 1" in seen["q"]
    assert "platforms" in seen["q"]
    assert entry["name"] == "Mega Man 2"


def test_fetch_entry_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", lambda *a, **k: [])
    assert igdb_match.fetch_entry(999, "c", "t") is None


def test_migrate_igdb_review_reason_adds_column(temp_db):
    conn = models.get_db()
    cols = [c[1] for c in conn.execute("PRAGMA table_info(games)")]
    conn.close()
    assert "igdb_review_reason" in cols


# --- rewritten audit: score-delta + reasons --------------------------------

_BASE = "https://images.igdb.com/igdb/image/upload/"


def _add_platform(conn, game_id, short_name):
    """Link a game to a platform by short_name (platform_ids_for maps short_name ->
    IGDB id via igdb_match.IGDB_PLATFORM_IDS, so no IGDB id is stored here).
    platforms.name is NOT NULL UNIQUE, so supply it too."""
    conn.execute("INSERT OR IGNORE INTO platforms (name, short_name) VALUES (?, ?)",
                 (short_name, short_name))
    pid = conn.execute("SELECT id FROM platforms WHERE short_name=?", (short_name,)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO game_platforms (game_id, platform_id) VALUES (?, ?)",
                 (game_id, pid))
    conn.commit()


def test_audit_skips_cosmetic_webp_jpg(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (1,'Hades','hades',?,100)", (_BASE + "t_cover_big/co1zyu.webp",))
    conn.commit()
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 100, "name": "Hades", "cover_url": _BASE + "t_cover_big/co1zyu.jpg",
         "platforms": [6], "source": "search", "score": 110}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 100, "name": "Hades", "platforms": [6], "cover": {"url": _BASE + "t_thumb/co1zyu.jpg"}})
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []
    assert conn.execute("SELECT needs_igdb_review FROM games WHERE id=1").fetchone()[0] == 0
    conn.close()


def test_audit_flags_mobile_to_console(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (2,'Y','y',?,200)", (_BASE + "t_cover_big/mob.jpg",))
    conn.commit()
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 201, "name": "Y", "cover_url": _BASE + "t_cover_big/con.jpg",
         "platforms": [48], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 200, "name": "Y", "platforms": [igdb_match.IOS_ID],
        "cover": {"url": _BASE + "t_thumb/mob.jpg"}})
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]
    assert flagged == [2]
    row = conn.execute("SELECT needs_igdb_review, igdb_review_reason FROM games WHERE id=2").fetchone()
    assert row[0] == 1 and row[1] == "mobile->console"
    assert conn.execute("SELECT cover_url FROM games WHERE id=2").fetchone()[0].endswith("mob.jpg")
    conn.close()


def test_audit_not_flagged_when_stored_scores_higher(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (3,'Portal','portal',?,300)", (_BASE + "t_cover_big/switch.jpg",))
    conn.commit()
    _add_platform(conn, 3, "Switch")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 71, "name": "Portal", "cover_url": _BASE + "t_cover_big/pcorig.jpg",
         "platforms": [6], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 300, "name": "Portal", "platforms": [130],
        "cover": {"url": _BASE + "t_thumb/switch.jpg"}})
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []
    conn.close()


def test_audit_applies_bundle_authoritative_not_flag(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,collection_name,igdb_id) "
                 "VALUES (4,'Mega Man X','mega man x',?,'MM X LC',NULL)",
                 (_BASE + "t_cover_big/wrong.jpg",))
    conn.commit()
    _add_platform(conn, 4, "Switch")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 1741, "name": "Mega Man X", "cover_url": _BASE + "t_cover_big/right.jpg",
         "platforms": [18], "source": "bundle"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: None)
    result = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")
    assert result["applied"] == [4]
    assert result["flagged"] == []
    row = conn.execute("SELECT igdb_id, cover_url, COALESCE(igdb_locked,0), "
                       "COALESCE(needs_igdb_review,0), igdb_review_reason "
                       "FROM games WHERE id=4").fetchone()
    assert row[0] == 1741                      # id applied
    assert row[1].endswith("right.jpg")        # cover applied
    assert row[2] == 1                          # locked
    assert row[3] == 0 and row[4] is None       # review cleared
    conn.close()


def test_audit_unmatched_strong_candidate_flags(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (5,'Celeste','celeste',?,NULL)", (_BASE + "t_cover_big/old.jpg",))
    conn.commit()
    _add_platform(conn, 5, "Steam")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 26226, "name": "Celeste", "cover_url": _BASE + "t_cover_big/celeste.jpg",
         "platforms": [6], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no stored id")))
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]
    assert flagged == [5]
    assert conn.execute("SELECT igdb_review_reason FROM games WHERE id=5").fetchone()[0] == "unmatched->match"
    conn.close()


def test_audit_unmatched_weak_candidate_not_flagged(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (6,'Celeste','celeste',?,NULL)", (_BASE + "t_cover_big/old.jpg",))
    conn.commit()
    _add_platform(conn, 6, "Switch")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 26226, "name": "Celeste", "cover_url": _BASE + "t_cover_big/celeste.jpg",
         "platforms": [6], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: None)
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []
    conn.close()


def test_audit_skips_locked(temp_db, monkeypatch):
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id,igdb_locked) "
                 "VALUES (7,'Locked','locked',?,700,1)", (_BASE + "t_cover_big/x.jpg",))
    conn.commit()
    monkeypatch.setattr(igdb_match, "candidates_for",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("locked must be skipped")))
    assert igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"] == []
    conn.close()


def test_audit_flags_better_platform_match(temp_db, monkeypatch):
    # stored entry is PC-only (no overlap with owned Switch); best is the Switch
    # edition (overlap). Both non-mobile, both exact title -> reason is platform.
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (8,'Celeste','celeste',?,800)", (_BASE + "t_cover_big/pc.jpg",))
    conn.commit()
    _add_platform(conn, 8, "Switch")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 801, "name": "Celeste", "cover_url": _BASE + "t_cover_big/switch.jpg",
         "platforms": [130], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 800, "name": "Celeste", "platforms": [6],
        "cover": {"url": _BASE + "t_thumb/pc.jpg"}})
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]
    assert flagged == [8]
    assert conn.execute("SELECT igdb_review_reason FROM games WHERE id=8").fetchone()[0] == "better platform match"
    conn.close()


def test_audit_flags_stronger_match_on_title(temp_db, monkeypatch):
    # both entries overlap the owned platform and are non-mobile, but the stored
    # entry's name only CONTAINS the title (score 40) while the best is an EXACT
    # match (score 100) -> positive delta, reason falls through to 'stronger match'.
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,igdb_id) "
                 "VALUES (9,'Portal','portal',?,900)", (_BASE + "t_cover_big/coll.jpg",))
    conn.commit()
    _add_platform(conn, 9, "Steam")
    monkeypatch.setattr(igdb_match, "candidates_for", lambda *a, **k: [
        {"igdb_id": 901, "name": "Portal", "cover_url": _BASE + "t_cover_big/portal.jpg",
         "platforms": [6], "source": "search"}])
    monkeypatch.setattr(igdb_match, "fetch_entry", lambda *a, **k: {
        "id": 900, "name": "Portal Companion Collection", "platforms": [6],
        "cover": {"url": _BASE + "t_thumb/coll.jpg"}})
    flagged = igdb_match.audit_igdb_matches(conn, client_id="c", token="t")["flagged"]
    assert flagged == [9]
    assert conn.execute("SELECT igdb_review_reason FROM games WHERE id=9").fetchone()[0] == "stronger match"
    conn.close()
