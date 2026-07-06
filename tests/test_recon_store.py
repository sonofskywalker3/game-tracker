"""Tests for the shared store-recon engine (recon_store) and its vendor wrappers."""
import contextlib
import json

import pytest

import recon_nintendo_store
import recon_psn_store
import recon_store
from recon_store import StoreRecon


def _cfg(**overrides) -> StoreRecon:
    base = dict(file_prefix="test_store",
                start_url="https://store.example.com/product/start",
                host_marker="store.example.com",
                product_markers=("/product/",),
                settle_ms=2000,
                instructions=("browse to a product page",),
                max_seconds=10)
    base.update(overrides)
    return StoreRecon(**base)


class FakeClock:
    """Monotonic stand-in advanced only by the page's waits."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakePage:
    """Page whose waits advance the fake clock; parked on one product URL."""

    def __init__(self, clock, url="https://store.example.com/product/p1",
                 html="<html/>"):
        self._clock = clock
        self._url = url
        self._html = html
        self.waits: list[int] = []

    @property
    def url(self) -> str:
        return self._url

    def goto(self, url) -> None:
        pass

    def wait_for_timeout(self, ms) -> None:
        self.waits.append(ms)
        self._clock.advance(ms / 1000)

    def content(self) -> str:
        return self._html


class NavigatingPage(FakePage):
    """URL changes on every read, so every settle aborts mid-way (continue path)."""

    def __init__(self, clock):
        super().__init__(clock)
        self.reads = 0

    @property
    def url(self) -> str:
        self.reads += 1
        return f"https://store.example.com/product/p{self.reads}"


class ClosedPage(FakePage):
    """Simulates the user closing the browser window."""

    @property
    def url(self) -> str:
        raise RuntimeError("window closed")


def _factory(page, captured=None):
    @contextlib.contextmanager
    def factory(headless=False):
        yield page, (captured if captured is not None else [])
    return factory


def test_budget_tracks_wall_clock_when_sitting_on_captured_page(tmp_path, monkeypatch):
    """Sitting on an already-captured page must burn budget at poll speed (0.7s per
    iteration), not settle+poll (2.7s) — the old accounting exhausted the budget
    roughly 4x too fast."""
    monkeypatch.setattr(recon_store, "RECON_DIR", tmp_path)
    clock = FakeClock()
    page = FakePage(clock)
    cfg = _cfg(max_seconds=10)
    recon_store.run(cfg, browser_factory=_factory(page), now=clock)
    total_wait_s = sum(page.waits) / 1000
    assert total_wait_s >= cfg.max_seconds       # ran the full wall-clock budget
    poll_iters = sum(1 for w in page.waits if w == recon_store.POLL_MS)
    # correct accounting: ~(10 - 2.7) / 0.7 ≈ 11 poll iterations; buggy: ~4 total
    assert poll_iters >= 10


def test_mid_settle_navigation_still_consumes_budget(tmp_path, monkeypatch):
    """The mid-settle retry path waited settle_ms but accrued nothing, letting the
    budget overrun; wall-clock accounting must terminate the loop on time."""
    monkeypatch.setattr(recon_store, "RECON_DIR", tmp_path)
    clock = FakeClock()
    page = NavigatingPage(clock)
    cfg = _cfg(max_seconds=10)
    recon_store.run(cfg, browser_factory=_factory(page), now=clock)
    assert clock.t >= cfg.max_seconds
    # ...and by no more than one extra settle+poll past the budget
    slack = (cfg.settle_ms + recon_store.POLL_MS) / 1000
    assert clock.t <= cfg.max_seconds + slack


def test_capture_writes_prefixed_files_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(recon_store, "RECON_DIR", tmp_path)
    clock = FakeClock()
    captured = [{"url": "https://api.example.com/x", "body": {"ok": 1}}]
    page = FakePage(clock, url="https://store.example.com/product/great-game",
                    html="<html>hi</html>")
    cfg = _cfg(max_seconds=5)
    recon_store.run(cfg, browser_factory=_factory(page, captured), now=clock)
    html_path = tmp_path / "test_store_01_great-game.html"
    json_path = tmp_path / "test_store_01_great-game.json"
    index_path = tmp_path / "test_store_index.json"
    assert html_path.read_text(encoding="utf-8") == "<html>hi</html>"
    assert json.loads(json_path.read_text(encoding="utf-8")) == captured
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["url"].endswith("/great-game")
    assert entries[0]["responses"] == 1
    assert entries[0]["html"] == html_path.name


def test_closed_browser_wraps_up_and_writes_index(tmp_path, monkeypatch):
    monkeypatch.setattr(recon_store, "RECON_DIR", tmp_path)
    clock = FakeClock()
    cfg = _cfg()
    recon_store.run(cfg, browser_factory=_factory(ClosedPage(clock)), now=clock)
    index_path = tmp_path / "test_store_index.json"
    assert json.loads(index_path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("mod,prefix,host", [
    (recon_psn_store, "psn_store", "store.playstation.com"),
    (recon_nintendo_store, "nintendo_store", "nintendo.com"),
])
def test_vendor_wrappers_delegate_to_shared_engine(monkeypatch, mod, prefix, host):
    """Both vendor modules keep their main() CLI entry point but drive the ONE
    shared engine with declarative per-vendor config."""
    calls: list[StoreRecon] = []
    monkeypatch.setattr(mod, "run", lambda cfg: calls.append(cfg))
    mod.main()
    (cfg,) = calls
    assert cfg.file_prefix == prefix
    assert cfg.host_marker == host
    assert cfg.start_url == mod.START_URL
    assert cfg.settle_ms == mod.SETTLE_MS
    assert cfg.product_markers


def test_vendor_product_url_detection():
    psn = recon_psn_store.CONFIG
    assert recon_store._is_product(psn, "https://store.playstation.com/en-us/product/UP0082-X")
    assert recon_store._is_product(psn, "https://store.playstation.com/en-us/concept/10000714")
    assert not recon_store._is_product(psn, "https://store.playstation.com/en-us/pages/latest")
    assert not recon_store._is_product(psn, "https://example.com/product/x")
    nin = recon_nintendo_store.CONFIG
    assert recon_store._is_product(nin, "https://www.nintendo.com/us/store/products/some-game-switch/")
    assert not recon_store._is_product(nin, "https://www.nintendo.com/us/store/games/")
