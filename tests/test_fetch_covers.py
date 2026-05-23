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


# --- pick_canonical_name: strict IGDB-name adoption (Part B, pure) ----------

def test_pick_canonical_name_adopts_official_casing_on_exact_match():
    results = [{"name": "AI: The Somnium Files"}]
    assert pick_canonical_name("Ai: the Somnium Files", results) == "AI: The Somnium Files"


def test_pick_canonical_name_none_when_no_confident_match():
    # Never rename to a wrong game.
    assert pick_canonical_name("Hollow Knight", [{"name": "Celeste"}]) is None


def test_pick_canonical_name_prefers_exact_over_containment():
    # "Portal" must never be renamed to "Portal 2".
    results = [{"name": "Portal 2"}, {"name": "Portal"}]
    assert pick_canonical_name("Portal", results) == "Portal"


def test_pick_canonical_name_accepts_official_subtitle_via_containment():
    results = [{"name": "The Witcher 3: Wild Hunt"}]
    assert pick_canonical_name("The Witcher 3", results) == "The Witcher 3: Wild Hunt"


def test_pick_canonical_name_skips_results_without_name():
    results = [{"cover": {"url": "x"}}, {"name": "Celeste"}]
    assert pick_canonical_name("Celeste", results) == "Celeste"


def test_pick_canonical_name_none_on_empty_results():
    assert pick_canonical_name("Anything", []) is None


def test_pick_canonical_name_none_when_search_normalizes_empty():
    # A title that normalizes to "" must not containment-match every result.
    assert pick_canonical_name("---", [{"name": "Real Game"}]) is None
