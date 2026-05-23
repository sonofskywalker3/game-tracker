from fetch_covers import (
    clean_search_title,
    cover_host,
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
