import igdb_match


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
        raise AssertionError("should not fall back to search scorer")
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity(
        "Mega Man 2", {130}, "Mega Man Legacy Collection 2", "c", "t")
    assert got["igdb_id"] == 1711
    assert got["source"] == "bundle"
    assert got["cover_url"].endswith("t_cover_big/2.jpg")


def test_resolve_identity_falls_back_to_scorer(monkeypatch):
    def fake(query, cid, tok):
        if "search \"Celeste\"" in query and "game_type" in query:
            return [{"id": 5, "name": "Celeste", "platforms": [6],
                     "cover": {"url": "//x/t_thumb/c.jpg"},
                     "total_rating_count": 50}]
        return []
    monkeypatch.setattr(igdb_match.igdb_dlc, "_igdb_query", fake)
    got = igdb_match.resolve_identity("Celeste", {6}, None, "c", "t")
    assert got["igdb_id"] == 5 and got["source"] == "search"
