"""xbox_catalog: resolve an add-on's parent GAME via Microsoft displaycatalog."""
from __future__ import annotations

from scrapers import xbox_catalog
from addon_parent import ParentRef


def _product(pid, ptype, title, *, parent_id=None):
    rel = ([{"RelatedProductId": parent_id, "RelationshipType": "addOnParent"}]
           if parent_id else [])
    return {
        "ProductId": pid, "ProductType": ptype,
        "LocalizedProperties": [{"ProductTitle": title}],
        "MarketProperties": [{"RelatedProducts": rel}],
    }


def test_parse_addon_parent_id():
    addon = _product("ADDON1", "Durable", "Gilded Glory Pack", parent_id="PARENT1")
    assert xbox_catalog._parent_id_of(addon) == "PARENT1"


def test_parse_no_parent_returns_none():
    game = _product("GAME1", "Game", "Borderlands 4")  # no addOnParent
    assert xbox_catalog._parent_id_of(game) is None


def test_resolve_uses_fetch_and_accepts_game_parent():
    catalogue = {
        "ADDON1": _product("ADDON1", "Durable", "Gilded Glory Pack", parent_id="PARENT1"),
        "PARENT1": _product("PARENT1", "Game", "Borderlands 4"),
    }
    def fake_fetch(ids):
        return {i: catalogue.get(i) for i in ids}
    out = xbox_catalog.resolve_addon_parents(["ADDON1"], fetch=fake_fetch)
    assert out["ADDON1"] == ParentRef(product_id="PARENT1", name="Borderlands 4")


def test_resolve_rejects_non_game_parent():
    catalogue = {
        "ADDON1": _product("ADDON1", "Durable", "Some Pass Perk", parent_id="PASS1"),
        "PASS1": _product("PASS1", "Pass", "A Subscription"),  # not a Game
    }
    def fake_fetch(ids):
        return {i: catalogue.get(i) for i in ids}
    out = xbox_catalog.resolve_addon_parents(["ADDON1"], fetch=fake_fetch)
    assert out["ADDON1"] is None


def test_resolve_missing_addon_is_none():
    def fake_fetch(ids):
        return {i: None for i in ids}
    out = xbox_catalog.resolve_addon_parents(["NOPE"], fetch=fake_fetch)
    assert out["NOPE"] is None
