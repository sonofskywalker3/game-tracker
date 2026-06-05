import json

from scrapers import playstation


class FakePage:
    """Records goto() calls; appends a canned GraphQL body to `captured` per URL."""
    def __init__(self, captured, bodies_by_pid):
        self.captured = captured
        self.bodies_by_pid = bodies_by_pid
        self.visited = []

    def goto(self, url):
        self.visited.append(url)
        pid = url.rsplit("/", 1)[-1]
        for body in self.bodies_by_pid.get(pid, []):
            self.captured.append({"url": url, "body": json.dumps(body)})

    def wait_for_timeout(self, ms):
        pass


def _owned_body(addon_pid, name="Pack", price="Purchased"):
    return {"data": {"items": [
        {"id": addon_pid, "name": name, "storeDisplayClassification": "ITEM",
         "platforms": ["PS5"], "price": {"basePrice": price}}]}}


def test_collect_addons_visits_targets_and_returns_owned(monkeypatch):
    monkeypatch.setattr(playstation, "scroll_until_idle", lambda *a, **k: None)
    base1 = "UP0082-PPSA10664_00-FF16SIEA00000002"
    base2 = "UP4497-PPSA03974_00-0000000000000CP1"
    captured = []
    page = FakePage(captured, {
        base1: [_owned_body("UP0082-PPSA10664_00-ADDCONT000000300", "FF16 DLC")],
        base2: [_owned_body("UP4497-PPSA03974_00-EXPANSION1000000", "PL", price="$29.99")],
    })
    addons, completed = playstation.collect_addons(page, [base1, base2], captured)
    assert page.visited == [
        "https://store.playstation.com/en-us/product/" + base1,
        "https://store.playstation.com/en-us/product/" + base2,
    ]
    assert [a.external_id for a in addons] == ["UP0082-PPSA10664_00-ADDCONT000000300"]
    assert completed == [base1, base2]


def test_collect_addons_skips_non_product_ids_and_survives_errors(monkeypatch):
    monkeypatch.setattr(playstation, "scroll_until_idle", lambda *a, **k: None)

    class Boom(FakePage):
        def goto(self, url):
            raise RuntimeError("nav failed")

    captured = []
    page = Boom(captured, {})
    addons, completed = playstation.collect_addons(page, ["TITLEID_ONLY",
                                                          "UP0082-PPSA10664_00-FF16SIEA00000002"],
                                                   captured)
    assert addons == []
    assert completed == []


def test_collect_addons_only_marks_successfully_loaded_games(monkeypatch):
    monkeypatch.setattr(playstation, "scroll_until_idle", lambda *a, **k: None)
    good = "UP0082-PPSA10664_00-FF16SIEA00000002"
    bad = "UP4497-PPSA03974_00-0000000000000CP1"

    class PartialPage(FakePage):
        def goto(self, url):
            if url.endswith(bad):
                raise RuntimeError("nav failed")
            super().goto(url)

    captured = []
    page = PartialPage(captured, {
        good: [_owned_body("UP0082-PPSA10664_00-ADDCONT000000300", "FF16 DLC")],
    })
    addons, completed = playstation.collect_addons(page, [good, bad], captured)
    assert completed == [good]                       # bad page not marked synced
    assert [a.external_id for a in addons] == ["UP0082-PPSA10664_00-ADDCONT000000300"]
