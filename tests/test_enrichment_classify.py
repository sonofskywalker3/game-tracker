"""enrichment.classify_match: confident / uncertain / no_match (pure, no I/O)."""
import enrichment
import models


def _nt(title):
    return models.normalize_title(title)


def test_confident_exact_title_and_platform():
    res = enrichment.classify_match(
        _nt("Mario Kart 8 Deluxe"), "Switch",
        [{"title": "Mario Kart 8 Deluxe (Nintendo Switch)", "upc": "045496590475"}])
    assert res["status"] == enrichment.CONFIDENT
    assert res["upc"] == "045496590475"


def test_confident_when_product_names_no_platform():
    res = enrichment.classify_match(
        _nt("Hades"), "Switch",
        [{"title": "Hades", "upc": "810017710003"}])
    assert res["status"] == enrichment.CONFIDENT
    assert res["upc"] == "810017710003"


def test_confident_picks_first_of_multiple_exact():
    res = enrichment.classify_match(
        _nt("Celeste"), "Switch",
        [{"title": "Celeste (Nintendo Switch)", "upc": "AAA"},
         {"title": "Celeste Nintendo Switch", "upc": "BBB"}])
    assert res["status"] == enrichment.CONFIDENT
    assert res["upc"] == "AAA"


def test_uncertain_exact_title_wrong_platform():
    res = enrichment.classify_match(
        _nt("Doom Eternal"), "Switch",
        [{"title": "Doom Eternal (PlayStation 5)", "upc": "CCC"}])
    assert res["status"] == enrichment.UNCERTAIN
    assert res["upc"] == "CCC"
    assert "platform" in (res["reason"] or "").lower()


def test_uncertain_partial_title_containment():
    res = enrichment.classify_match(
        _nt("Zelda Tears of the Kingdom"), "Switch",
        [{"title": "The Legend of Zelda Tears of the Kingdom Collector Edition", "upc": "DDD"}])
    assert res["status"] == enrichment.UNCERTAIN
    assert res["upc"] == "DDD"


def test_no_match_when_nothing_close():
    res = enrichment.classify_match(
        _nt("Stardew Valley"), "Switch",
        [{"title": "USB-C Charging Cable 3-pack", "upc": "EEE"}])
    assert res["status"] == enrichment.NO_MATCH
    assert res["upc"] is None


def test_no_match_on_empty_products():
    res = enrichment.classify_match(_nt("Anything"), "PS5", [])
    assert res["status"] == enrichment.NO_MATCH
