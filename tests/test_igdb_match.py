import igdb_dlc
import igdb_match
import models


def _cand(name, plats, *, cover=True, rating=0, year=2000, igdb_id=1):
    return {"id": igdb_id, "name": name, "platforms": list(plats),
            "cover": {"url": "//x/t_thumb/a.jpg"} if cover else None,
            "total_rating_count": rating, "first_release_date": year}


def test_title_score_tolerates_missing_interior_article():
    # A UPC product title drops the leading "A" that IGDB keeps; the scan must
    # still match. (Real case: "...Link Between Worlds" vs "...A Link Between Worlds".)
    s = igdb_match._title_score
    assert s("The Legend of Zelda: A Link Between Worlds",
             "The Legend of Zelda: Link Between Worlds") == igdb_match._TITLE_CONTAINS
    # exact stays exact; genuinely different titles stay unmatched
    assert s("Halo", "Halo") == igdb_match._TITLE_EXACT
    assert s("Mario Party", "Mario Tennis") is None
    # a single shared word is NOT enough (avoids spurious matches)
    assert s("Mario Kart 8", "Mario Tennis Aces") is None
    # the article rule must NOT degrade into token-subset: a numbered game must
    # not match a different collection that merely contains all its words.
    assert s("Mega Man Legacy Collection 2", "Mega Man 2") is None


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


def test_modal_candidates_drops_junk_and_dedupes():
    cands = [
        {"igdb_id": 1, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 110},
        {"igdb_id": 2, "name": "Aria of Sorrow", "cover_url": "https://x/co_a.jpg",
         "source": "search", "score": 100},                       # duplicate art -> collapsed
        {"igdb_id": 3, "name": "Aria of Sorrow Alter", "cover_url": "https://x/co_b.jpg",
         "source": "search", "score": 50},                        # title mismatch (mod) -> dropped
        {"igdb_id": 4, "name": "Anything", "cover_url": "https://x/co_c.jpg",
         "source": "bundle"},                                     # bundle -> kept regardless of title
        {"igdb_id": 5, "name": "Aria of Sorrow", "cover_url": None,
         "source": "search"},                                     # no cover -> dropped
    ]
    out = igdb_match.modal_candidates(cands, "Aria of Sorrow")
    assert [c["igdb_id"] for c in out] == [1, 4]


def test_modal_candidates_fallback_when_no_title_match():
    cands = [
        {"igdb_id": 7, "name": "Totally Different Name", "cover_url": "https://x/co_z.jpg",
         "source": "search", "score": 40},
    ]
    out = igdb_match.modal_candidates(cands, "Aria of Sorrow")
    assert [c["igdb_id"] for c in out] == [7]   # no title match -> fall back to all-with-cover


def test_modal_candidates_empty_input():
    assert igdb_match.modal_candidates([], "Whatever") == []


def test_platform_labels_known_ids_ordered():
    assert igdb_match.platform_labels([169, 48, 6, 167, 130]) == ["PS5", "PS4", "Switch", "Xbox", "PC"]


def test_platform_labels_mobile_and_unknown():
    assert igdb_match.platform_labels([39]) == ["iOS"]
    assert igdb_match.platform_labels([99999]) == []
    assert igdb_match.platform_labels([]) == []


def test_modal_candidates_drops_candidate_matching_current_cover():
    cands = [
        {"igdb_id": 10, "name": "Bugsnax", "cover_url": "https://x/co_same.jpg",
         "source": "search", "score": 30},
        {"igdb_id": 11, "name": "Bugsnax", "cover_url": "https://x/co_full.jpg",
         "source": "search", "score": 160},
    ]
    out = igdb_match.modal_candidates(cands, "Bugsnax", current_cover="https://x/co_same.jpg")
    assert [c["igdb_id"] for c in out] == [11]   # the candidate equal to current is dropped


def test_candidates_for_drops_fan_types_and_wrong_platform(monkeypatch):
    SWITCH = 130
    rows = [
        {"id": 1, "name": "Paper Mario: TTYD", "platforms": [130], "game_type": 8,
         "cover": {"url": "//x/t_thumb/a.jpg"}, "total_rating_count": 50},   # remake, Switch
        {"id": 2, "name": "Paper Mario: TTYD", "platforms": [21], "game_type": 0,
         "cover": {"url": "//x/t_thumb/b.jpg"}, "total_rating_count": 80},   # GameCube original
        {"id": 3, "name": "Paper Mario: TTYD", "platforms": [130], "game_type": 5,
         "cover": {"url": "//x/t_thumb/c.jpg"}, "total_rating_count": 10},   # mod/ROM hack
    ]
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda *a, **k: rows)
    out = igdb_match.candidates_for(
        "Paper Mario: TTYD", {SWITCH}, None, "cid", "tok",
        drop_fan_types=True, restrict_to_platform=True)
    ids = [c["igdb_id"] for c in out]
    assert ids == [1]   # only the Switch remake survives


def test_candidates_for_returns_game_type(monkeypatch):
    import igdb_dlc
    import igdb_match
    rows = [{"id": 1, "name": "X Collection", "platforms": [130], "game_type": 3,
             "cover": {"url": "//x/t_thumb/a.jpg"}, "total_rating_count": 50}]
    monkeypatch.setattr(igdb_dlc, "_igdb_query", lambda *a, **k: rows)
    out = igdb_match.candidates_for("X Collection", {130}, None, "cid", "tok")
    assert out[0]["game_type"] == 3
