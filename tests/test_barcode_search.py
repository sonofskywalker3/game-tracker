"""barcode.search_products_by_name: parse, failure->None, empty->[], rate-limit capture."""
import requests

import barcode


class _Resp:
    def __init__(self, payload, headers=None, exc=None):
        self._payload = payload
        self.headers = headers or {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


def test_parses_items_into_title_upc(monkeypatch):
    payload = {"code": "OK", "items": [
        {"title": "Mario Kart 8 Deluxe (Nintendo Switch)", "upc": "045496590475"},
        {"title": "No UPC here", "ean": "x"},  # skipped: no upc
        {"title": "Animal Crossing New Horizons", "upc": "045496596439"},
    ]}
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(payload, {"X-RateLimit-Remaining": "68"}))
    out = barcode.search_products_by_name("Mario Kart 8 Deluxe")
    assert out == [
        {"title": "Mario Kart 8 Deluxe (Nintendo Switch)", "upc": "045496590475"},
        {"title": "Animal Crossing New Horizons", "upc": "045496596439"},
    ]
    assert barcode.last_rate_remaining() == 68


def test_network_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.search_products_by_name("anything") is None


def test_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(None, exc=ValueError("bad json")))
    assert barcode.search_products_by_name("anything") is None


def test_empty_items_returns_empty_list(monkeypatch):
    """A 200 response with no items returns [] (genuinely empty, distinct from None)."""
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp({"items": []}, {"X-RateLimit-Remaining": "50"}))
    result = barcode.search_products_by_name("no results game")
    assert result == []


def test_rate_limit_header_captured_before_raise(monkeypatch):
    """X-RateLimit-Remaining is captured even when raise_for_status raises (e.g. 429)."""
    monkeypatch.setattr(barcode, "_last_rate_remaining", None)
    resp = _Resp(None, headers={"X-RateLimit-Remaining": "3"},
                 exc=requests.HTTPError("429"))
    monkeypatch.setattr(barcode.requests, "get", lambda *a, **k: resp)
    result = barcode.search_products_by_name("anything")
    assert result is None
    assert barcode.last_rate_remaining() == 3


def test_missing_header_leaves_remaining_none(monkeypatch):
    monkeypatch.setattr(barcode, "_last_rate_remaining", None)  # auto-reverts after test
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp({"items": []}, {}))
    barcode.search_products_by_name("q")
    assert barcode.last_rate_remaining() is None


def test_json_parse_error_returns_none(monkeypatch):
    """A 200 whose body fails to parse (json() raises ValueError) returns None,
    not [] — the json()-path is distinct from a raise_for_status failure."""
    class _BadJson(_Resp):
        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _BadJson({}, {"X-RateLimit-Remaining": "40"}))
    assert barcode.search_products_by_name("anything") is None
