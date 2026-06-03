import json

import popular_seed


def test_fetch_top_games_parses_paginates_and_dedupes(monkeypatch):
    page0 = [
        {"id": 1, "name": "Alpha Quest", "genres": [{"name": "RPG"}],
         "summary": "An RPG.", "first_release_date": 1262304000, "total_rating_count": 900},
        {"id": 2, "name": "Alpha Quest", "total_rating_count": 800},  # dup normalized -> dropped
    ]
    page1 = [
        {"id": 3, "name": "Beta Blast", "genres": [{"name": "Shooter"}],
         "summary": None, "first_release_date": None, "total_rating_count": 700},
    ]
    pages = [page0, page1, []]
    calls = {"i": 0}

    def fake_query(q, c, t):
        i = calls["i"]
        calls["i"] += 1
        assert "sort total_rating_count desc" in q
        return pages[i] if i < len(pages) else []

    monkeypatch.setattr(popular_seed.igdb_dlc, "_igdb_query", fake_query)
    out = popular_seed.fetch_top_games(10, client_id="c", token="t", page_size=2)

    assert [g["normalized_title"] for g in out] == ["alpha quest", "beta blast"]
    assert out[0]["genres"] == ["RPG"]
    assert out[0]["year"] == 2010
    assert out[1]["year"] is None
    assert out[0]["name"] == "Alpha Quest"


def test_select_unseeded_drops_existing_and_intra_dups(tmp_path):
    catalog = tmp_path / "game_traits.default.json"
    catalog.write_text(json.dumps({"zelda": {"session_length": "long"}}, indent=2) + "\n",
                       encoding="utf-8")
    games = [
        {"normalized_title": "zelda", "name": "Zelda"},        # already in catalog -> drop
        {"normalized_title": "celeste", "name": "Celeste"},    # keep
        {"normalized_title": "celeste", "name": "Celeste DX"}, # intra-list dup -> drop
    ]
    out = popular_seed.select_unseeded(games, catalog_path=catalog)
    assert [g["normalized_title"] for g in out] == ["celeste"]


def test_merge_classifications_minimal_diff_skip_existing_and_unknown(tmp_path):
    catalog = tmp_path / "game_traits.default.json"
    original = json.dumps({"abzu": {"session_length": "short"}},
                          sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    catalog.write_text(original, encoding="utf-8")

    verdicts = [
        {"normalized_title": "celeste", "session_length": "short"},   # add
        {"normalized_title": "skyrim", "session_length": "long"},     # add
        {"normalized_title": "abzu", "session_length": "long"},       # existing -> skip (no overwrite)
        {"normalized_title": "mystery", "session_length": "unknown"}, # abstain -> skip
        {"normalized_title": "", "session_length": "short"},          # bad -> skip
    ]
    added, skipped = popular_seed.merge_classifications(verdicts, catalog_path=catalog)
    assert (added, skipped) == (2, 3)

    result = json.loads(catalog.read_text(encoding="utf-8"))
    assert result["abzu"] == {"session_length": "short"}  # untouched
    assert result["celeste"] == {"session_length": "short"}
    assert result["skyrim"] == {"session_length": "long"}
    assert "mystery" not in result

    # round-trips: sorted, 2-space indent, trailing newline preserved
    expected = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert catalog.read_text(encoding="utf-8") == expected
