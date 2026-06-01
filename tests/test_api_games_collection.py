import models


def _make_collection_game(client, title="Mega Man", collection="Mega Man Legacy Collection"):
    gid = client.post("/api/games", json={"title": title}).get_json()["game_id"]
    conn = models.get_db()
    conn.execute("UPDATE games SET collection_name = ? WHERE id = ?", (collection, gid))
    conn.commit()
    conn.close()
    return gid


def test_api_games_list_includes_collection_name(client):
    _make_collection_game(client)
    rows = client.get("/api/games").get_json()
    mm = next(r for r in rows if r["title"] == "Mega Man")
    assert mm["collection_name"] == "Mega Man Legacy Collection"


def test_api_games_search_includes_collection_name(client):
    _make_collection_game(client, title="Mega Man X")
    rows = client.get("/api/games/search?q=mega").get_json()
    mm = next(r for r in rows if r["title"] == "Mega Man X")
    assert mm["collection_name"] == "Mega Man Legacy Collection"


def test_api_game_detail_includes_collection_name(client):
    gid = _make_collection_game(client)
    g = client.get(f"/api/games/{gid}").get_json()
    assert g["collection_name"] == "Mega Man Legacy Collection"


def test_tile_and_modal_have_collection_markup():
    with open("templates/base.html", encoding="utf-8") as f:
        html = f.read()
    assert "collection-badge" in html          # tile badge
    assert "game.collection_name" in html       # gated on the field
    assert "Part of" in html                     # modal launch cue

    with open("templates/index.html", encoding="utf-8") as f:
        index_html = f.read()
    assert "collection-badge" in index_html       # library tiles render the badge too
    assert "game.collection_name" in index_html
