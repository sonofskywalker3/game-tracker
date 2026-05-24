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
