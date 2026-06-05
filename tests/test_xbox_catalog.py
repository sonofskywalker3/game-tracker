"""xbox_catalog: resolve an add-on's parent GAME via Microsoft displaycatalog."""
from __future__ import annotations

import json
from pathlib import Path

import requests

from scrapers import xbox_catalog
from addon_parent import ParentRef


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return _FakeResp(self._payload)


class _BoomSession:
    def get(self, *a, **k):
        raise AssertionError("network should not be called")


class _ConnErrSession:
    def get(self, *a, **k):
        raise requests.ConnectionError("down")


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


def test_fetch_products_caches_and_reads_back(tmp_path):
    payload = {"Products": [
        _product("A", "Game", "Alpha"),
        _product("B", "Durable", "Bee", parent_id="A"),
    ]}
    fake = _FakeSession(payload)
    out = xbox_catalog._fetch_products(["A", "B"], cache_dir=tmp_path, session=fake, delay_s=0)
    assert out["A"] == _product("A", "Game", "Alpha")
    assert out["B"] == _product("B", "Durable", "Bee", parent_id="A")
    assert (tmp_path / "A.json").exists()
    assert (tmp_path / "B.json").exists()
    assert fake.calls == 1

    # Second call must come from cache, never touching the network.
    cached = xbox_catalog._fetch_products(
        ["A", "B"], cache_dir=tmp_path, session=_BoomSession(), delay_s=0)
    assert cached["A"] == _product("A", "Game", "Alpha")
    assert cached["B"] == _product("B", "Durable", "Bee", parent_id="A")


def test_fetch_products_does_not_cache_on_error(tmp_path):
    out = xbox_catalog._fetch_products(
        ["X"], cache_dir=tmp_path, session=_ConnErrSession(), delay_s=0)
    assert out == {"X": None}
    assert not (tmp_path / "X.json").exists()  # uncached -> a later run retries


def test_resolve_against_real_shaped_fixture():
    body = json.loads(
        (Path(__file__).parent / "fixtures" / "xbox_displaycatalog_sample.json")
        .read_text(encoding="utf-8"))
    by_id = {p["ProductId"]: p for p in body["Products"]}

    def fake_fetch(ids):
        return {i: by_id.get(i) for i in ids}

    out = xbox_catalog.resolve_addon_parents(["9MZKJPZXTVGM"], fetch=fake_fetch)
    assert out["9MZKJPZXTVGM"].product_id == "9MX6HKF5647G"
    assert out["9MZKJPZXTVGM"].name.startswith("Borderlands")
