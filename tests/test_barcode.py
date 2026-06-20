import requests

import barcode


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_lookup_returns_first_item_title(monkeypatch):
    def fake_get(url, params, timeout):
        assert params["upc"] == "711719541028"
        return _FakeResp({"items": [{"title": "Marvel's Spider-Man 2 - PS5"}]})

    monkeypatch.setattr(barcode.requests, "get", fake_get)
    assert barcode.lookup_product_title("711719541028") == "Marvel's Spider-Man 2 - PS5"


def test_lookup_returns_none_when_no_items(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get", lambda url, params, timeout: _FakeResp({"items": []}))
    assert barcode.lookup_product_title("000") is None


def test_lookup_degrades_to_none_on_network_error(monkeypatch):
    def boom(url, params, timeout):
        raise requests.Timeout("slow")

    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.lookup_product_title("000") is None
