"""barcode.lookup_wikidata_gtin: parse, padded variants, miss/failure -> None."""
import requests

import barcode


class _Resp:
    def __init__(self, payload, exc=None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


def _bindings(label):
    return {"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q719176"},
         "itemLabel": {"value": label}}]}}


def test_parses_label_from_sparql(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["query"] = params["query"]
        return _Resp(_bindings("Crash Bandicoot"))
    monkeypatch.setattr(barcode.requests, "get", fake_get)
    assert barcode.lookup_wikidata_gtin("711719490029") == "Crash Bandicoot"
    # The query must include the zero-padded variants (Wikidata stores GTIN-13/14).
    assert "711719490029" in captured["query"]
    assert "0711719490029" in captured["query"]
    assert "00711719490029" in captured["query"]
    assert "P3962" in captured["query"] and "Q7889" in captured["query"]


def test_empty_bindings_returns_none(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp({"results": {"bindings": []}}))
    assert barcode.lookup_wikidata_gtin("000000000000") is None


def test_network_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(barcode.requests, "get", boom)
    assert barcode.lookup_wikidata_gtin("711719490029") is None


def test_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(barcode.requests, "get",
                        lambda *a, **k: _Resp(None, exc=ValueError("bad json")))
    assert barcode.lookup_wikidata_gtin("711719490029") is None
