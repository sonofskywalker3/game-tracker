"""ScrapeRunner state machine — fake browser + fake vendor modules, no Playwright."""
import threading
from contextlib import contextmanager
from pathlib import Path

from desktop.runner import ScrapeRunner


class _FakePage:
    def goto(self, url: str) -> None: ...
    def wait_for_timeout(self, ms: int) -> None: ...


class _FakeModule:
    VENDOR_URL = "https://vendor.example"
    SOURCE = "playstation"
    def __init__(self, games=None, boom: bool = False) -> None:
        self._games, self._boom = games or [], boom
    def collect(self, page, captured) -> list:
        if self._boom:
            raise RuntimeError("vendor page changed")
        captured.extend({"url": "x"} for _ in self._games)
        return self._games


@contextmanager
def _fake_browser(headless: bool = False, profile_dir: Path | None = None):
    yield _FakePage(), []


def _fake_write(source: str, games: list, out_dir: Path) -> Path:
    out = Path(out_dir) / f"{source}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}", encoding="utf-8")
    return out


def _run(runner: ScrapeRunner) -> list[dict]:
    events: list[dict] = []
    t = threading.Thread(target=runner.run)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()
    return events


def test_happy_path_emits_done_and_writes(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = ScrapeRunner(["playstation"], tmp_path, tmp_path / "prof", events.append,
                          modules={"playstation": _FakeModule(games=["g1", "g2"])},
                          browser_factory=_fake_browser, write=_fake_write)
    runner.continue_login()          # pre-set: no user in tests
    results = runner.run()
    types = [e["type"] for e in events]
    assert types == ["login", "collecting", "done", "finished"]
    assert events[2]["count"] == 2
    assert Path(results["playstation"]).exists()


def test_skip_moves_to_next_vendor(tmp_path: Path) -> None:
    events: list[dict] = []
    mods = {"playstation": _FakeModule(games=["g"]), "xbox": _FakeModule(games=["g", "g"])}
    runner = ScrapeRunner(["playstation", "xbox"], tmp_path, tmp_path / "p", events.append,
                          modules=mods, browser_factory=_fake_browser, write=_fake_write)
    runner.skip_vendor()             # pre-set: skips playstation login wait
    runner.continue_login()          # xbox proceeds
    results = runner.run()
    assert [e["type"] for e in events if e["type"] in ("skipped", "done")] == ["skipped", "done"]
    assert "playstation" not in results and "xbox" in results


def test_vendor_error_is_skipped_not_fatal(tmp_path: Path) -> None:
    events: list[dict] = []
    mods = {"playstation": _FakeModule(boom=True), "xbox": _FakeModule(games=["g"])}
    runner = ScrapeRunner(["playstation", "xbox"], tmp_path, tmp_path / "p", events.append,
                          modules=mods, browser_factory=_fake_browser, write=_fake_write)
    runner.continue_login()
    runner._auto_continue = True                   # continue every vendor (test helper attr)
    results = runner.run()
    skipped = [e for e in events if e["type"] == "skipped"]
    assert skipped and "vendor page changed" in skipped[0]["note"]
    assert list(results) == ["xbox"]
