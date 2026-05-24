import igdb_dlc


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
