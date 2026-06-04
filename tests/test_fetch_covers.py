from fetch_covers import (
    classify_rename,
    clean_search_title,
    cover_host,
    is_questionable_rename,
    needs_cover,
    pick_canonical_name,
    should_null_on_miss,
)


def test_clean_search_title_strips_quotes_that_break_igdb():
    # Embedded double-quotes break IGDB's `search "..."` query (HTTP 400).
    assert '"' not in clean_search_title('"Edna & Harvey" Bundle')
    assert clean_search_title('"Edna & Harvey" Bundle') == "Edna & Harvey Bundle"


def test_clean_search_title_strips_suffix_paren_and_tm():
    assert clean_search_title("Foo (Switch)") == "Foo"
    assert clean_search_title("Foo™") == "Foo"
    assert clean_search_title("Bar - Deluxe Edition") == "Bar"

IGDB = "https://images.igdb.com/igdb/image/upload/t_cover_big/co9uqw.jpg"
NINTENDO = "https://assets.nintendo.com/image/upload/store/software/switch/700/x"
PSN = "https://image.api.playstation.com/gs2-sec/appkgo/prod/CUSA1/5/i_x"


def test_cover_host_parses_http_urls():
    assert cover_host(IGDB) == "images.igdb.com"
    assert cover_host(NINTENDO) == "assets.nintendo.com"


def test_cover_host_none_for_empty():
    assert cover_host(None) is None
    assert cover_host("") is None


def test_needs_cover_when_missing_regardless_of_mode():
    assert needs_cover(None, upgrade=False) is True
    assert needs_cover("", upgrade=True) is True


def test_needs_cover_skips_igdb():
    assert needs_cover(IGDB, upgrade=False) is False
    assert needs_cover(IGDB, upgrade=True) is False


def test_needs_cover_non_igdb_only_in_upgrade_mode():
    assert needs_cover(NINTENDO, upgrade=False) is False
    assert needs_cover(NINTENDO, upgrade=True) is True
    assert needs_cover(PSN, upgrade=True) is True


def test_should_null_on_miss_only_for_wide_art():
    assert should_null_on_miss(NINTENDO) is True   # broken wide art -> null it
    assert should_null_on_miss(PSN) is False        # acceptable -> keep on miss
    assert should_null_on_miss(IGDB) is False


# --- pick_canonical_name: adopt IGDB casing only on exact-normalized identity ---
# Matching is equality-only by design. Containment was tried and rejected after a
# live run renamed unrelated/bundle/edition titles; these are regression tests for
# the exact failures observed.

def test_pick_canonical_name_adopts_official_casing_on_exact_match():
    results = [{"name": "AI: The Somnium Files"}]
    assert pick_canonical_name("Ai: the Somnium Files", results) == "AI: The Somnium Files"


def test_pick_canonical_name_adopts_punctuation_only_fix():
    results = [{"name": "Akuto: Showdown"}]
    assert pick_canonical_name("Akuto Showdown", results) == "Akuto: Showdown"


def test_pick_canonical_name_none_when_no_confident_match():
    assert pick_canonical_name("Hollow Knight", [{"name": "Celeste"}]) is None


def test_pick_canonical_name_returns_the_exact_match_not_a_relative():
    # "Portal" must never be renamed to "Portal 2".
    results = [{"name": "Portal 2"}, {"name": "Portal"}]
    assert pick_canonical_name("Portal", results) == "Portal"


def test_pick_canonical_name_rejects_substring_of_a_different_game():
    # Regression: ".cat" (normalizes to "cat") must NOT match "Hungry Cat".
    assert pick_canonical_name(".cat", [{"name": "Hungry Cat"}]) is None


def test_pick_canonical_name_rejects_bundle_superset():
    # Regression: base game must NOT match a bundle whose name contains it.
    results = [{"name": "Assassin's Creed Bundle: Valhalla, Odyssey, and Origins"}]
    assert pick_canonical_name("Assassin's Creed", results) is None


def test_pick_canonical_name_does_not_strip_edition_to_base_game():
    # Regression: don't change which edition the title is.
    results = [{"name": "Assassin's Creed Odyssey"}]
    assert pick_canonical_name("Assassin's Creed Odyssey - Ultimate Edition", results) is None


def test_pick_canonical_name_rejects_subtitle_expansion():
    # Adding an official subtitle is the same risky mechanism as the bundle match.
    results = [{"name": "The Witcher 3: Wild Hunt"}]
    assert pick_canonical_name("The Witcher 3", results) is None


def test_pick_canonical_name_skips_results_without_name():
    results = [{"cover": {"url": "x"}}, {"name": "Celeste"}]
    assert pick_canonical_name("Celeste", results) == "Celeste"


def test_pick_canonical_name_none_on_empty_results():
    assert pick_canonical_name("Anything", []) is None


def test_pick_canonical_name_none_when_search_normalizes_empty():
    assert pick_canonical_name("---", [{"name": "Real Game"}]) is None


# --- is_questionable_rename / classify_rename: surface dubious renames -------

def test_questionable_false_for_casing_only():
    assert is_questionable_rename("Bioshock", "BioShock") is False


def test_questionable_false_for_separator_insertion():
    assert is_questionable_rename("Akuto Showdown", "Akuto: Showdown") is False


def test_questionable_false_for_added_apostrophe():
    # Adding the correct apostrophe is a good fix, not questionable.
    assert is_questionable_rename("Assassins Creed III", "Assassin's Creed III") is False


def test_questionable_true_for_added_content_symbol():
    assert is_questionable_rename("Dicey Dungeons", "Dicey Dungeons+") is True
    assert is_questionable_rename("Rekt", "Rekt!") is True


def test_questionable_true_for_removed_content_symbol():
    assert is_questionable_rename("Jamjam!", "Jamjam") is True


def test_questionable_true_for_dropped_apostrophe():
    assert is_questionable_rename("Wreckin' Ball Adventure", "Wreckin Ball Adventure") is True


def test_questionable_true_for_dropped_clause():
    assert is_questionable_rename("Doom II (classic)", "Doom II") is True


def test_classify_rename_skips_curated_judgment_calls():
    # The heuristic can't see these as questionable, so they live in the table.
    assert classify_rename("moon", "Moon") == "skip"
    assert classify_rename("Chocobo Gp", "Chocobo GP'") == "skip"


def test_classify_rename_reviews_questionable():
    assert classify_rename("Dicey Dungeons", "Dicey Dungeons+") == "review"


def test_classify_rename_applies_clean_casing_fix():
    assert classify_rename("Bioshock", "BioShock") == "apply"


def test_search_game_returns_authoritative_identity(monkeypatch):
    import fetch_covers
    import igdb_match
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 1, "name": "Mega Man 2",
                                         "cover_url": "https://x/t_cover_big/2.jpg",
                                         "source": "bundle"})
    got = fetch_covers.search_game("Mega Man 2", "c", "t",
                                   platform_ids={130}, collection_name="Mega Man Legacy Collection 2")
    assert got["cover_url"] == "https://x/t_cover_big/2.jpg"
    assert got["igdb_id"] == 1
    assert got["authoritative"] is True


def test_search_game_exact_title_is_authoritative_even_when_search_source(monkeypatch):
    import fetch_covers
    import igdb_match
    monkeypatch.setattr(igdb_match, "resolve_identity",
                        lambda *a, **k: {"igdb_id": 5, "name": "Celeste",
                                         "cover_url": "https://x/t_cover_big/c.jpg",
                                         "source": "search"})
    got = fetch_covers.search_game("Celeste", "c", "t")
    assert got["authoritative"] is True
    assert got["igdb_id"] == 5


def test_search_game_strict_rejects_loose_match(monkeypatch):
    import fetch_covers
    import igdb_match
    loose_identity = {"source": "search", "name": "Some Other Game",
                      "cover_url": "https://x/t_cover_big/y.jpg", "igdb_id": 9}
    monkeypatch.setattr(igdb_match, "resolve_identity", lambda *a, **k: loose_identity)

    # strict=True: name mismatch and source != "bundle" -> reject entirely
    assert fetch_covers.search_game("Celeste", "c", "t", strict=True) is None

    # strict=False: loose match returns a non-authoritative identity (cover only)
    got = fetch_covers.search_game("Celeste", "c", "t", strict=False)
    assert got["cover_url"] == "https://x/t_cover_big/y.jpg"
    assert got["authoritative"] is False


def _run_cover_gen(monkeypatch, identity):
    """Drive fetch_covers_generator over a single inserted game, with auth and
    search_game stubbed. Returns the games row dict after the run."""
    import fetch_covers
    import models
    monkeypatch.setattr(fetch_covers, "get_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(fetch_covers, "search_game", lambda *a, **k: identity)
    list(fetch_covers.fetch_covers_generator("c", "s", skip_existing=False))
    conn = models.get_db()
    row = conn.execute(
        "SELECT igdb_id, cover_url, COALESCE(igdb_locked,0) AS locked, "
        "COALESCE(needs_igdb_review,0) AS review FROM games WHERE id=1").fetchone()
    conn.close()
    return row


def test_cover_gen_authoritative_match_persists_id_and_locks(temp_db, monkeypatch):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url,needs_igdb_review,igdb_review_reason) "
                 "VALUES (1,'Castlevania: Aria of Sorrow','castlevania aria of sorrow',"
                 "'https://images.igdb.com/igdb/image/upload/t_cover_big/co687k.jpg',1,'bundle')")
    conn.commit()
    conn.close()
    row = _run_cover_gen(monkeypatch, {
        "igdb_id": 222412, "name": "Castlevania: Aria of Sorrow",
        "cover_url": "https://images.igdb.com/igdb/image/upload/t_cover_big/cob949.jpg",
        "source": "bundle", "authoritative": True})
    assert row["igdb_id"] == 222412
    assert row["cover_url"].endswith("cob949.jpg")
    assert row["locked"] == 1
    assert row["review"] == 0  # applying the authoritative match clears the flag


def test_cover_gen_nonauthoritative_match_writes_cover_only(temp_db, monkeypatch):
    import models
    conn = models.get_db()
    conn.execute("INSERT INTO games (id,title,normalized_title,cover_url) "
                 "VALUES (1,'Some Game','some game',NULL)")
    conn.commit()
    conn.close()
    row = _run_cover_gen(monkeypatch, {
        "igdb_id": 9, "name": "A Loosely Related Game",
        "cover_url": "https://x/t_cover_big/loose.jpg",
        "source": "search", "authoritative": False})
    assert row["cover_url"].endswith("loose.jpg")
    assert row["igdb_id"] is None  # no id persisted on a loose cover fill
    assert row["locked"] == 0
