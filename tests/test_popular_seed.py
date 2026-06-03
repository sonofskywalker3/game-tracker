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
