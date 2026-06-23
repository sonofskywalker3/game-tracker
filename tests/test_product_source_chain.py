"""barcode product-source chain: order, fall-through, all-miss."""
import barcode


def test_first_source_hit_wins(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: "From UPCitemdb", lambda u: "From Wikidata"))
    assert barcode._product_via_sources("123") == "From UPCitemdb"


def test_falls_through_to_second_source(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: None, lambda u: "From Wikidata"))
    assert barcode._product_via_sources("123") == "From Wikidata"


def test_all_miss_returns_none(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: None, lambda u: None))
    assert barcode._product_via_sources("123") is None


def test_empty_string_is_treated_as_miss(monkeypatch):
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: "", lambda u: "Real Title"))
    assert barcode._product_via_sources("123") == "Real Title"


def test_resolve_uses_the_chain(monkeypatch, tmp_path):
    import models
    models.DB_PATH = tmp_path / "g.db"
    models.init_db()
    models.migrate_db()
    conn = models.get_db()
    # UPCitemdb miss, Wikidata hit -> resolve must reach the upc_api branch, not 'none'.
    monkeypatch.setattr(barcode, "PRODUCT_SOURCES",
                        (lambda u: None, lambda u: "Crash Bandicoot"))
    # No IGDB creds -> no candidates, but product was found via the chain.
    res = barcode.resolve(conn, "711719490029")
    assert res["source"] == "upc_api"
    assert res["product_title"] == "Crash Bandicoot"
    conn.close()
