"""barcode.search_products_by_name: parse, failure->[], rate-limit capture."""
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


def test_network_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.search_products_by_name("anything") == []


def test_bad_json_returns_empty(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(None, exc=ValueError("bad json")))
    assert barcode.search_products_by_name("anything") == []


def test_missing_header_leaves_remaining_none(monkeypatch):
    barcode._last_rate_remaining = None  # reset module state for the assertion
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp({"items": []}, {}))
    barcode.search_products_by_name("q")
    assert barcode.last_rate_remaining() is None
