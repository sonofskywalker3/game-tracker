"""Api bridge logic with an injected fake runner factory (no webview, no Playwright)."""
from pathlib import Path

from desktop.bridge import Api


class _FakeRunner:
    def __init__(self) -> None:
        self.continued = self.skipped = False
    def continue_login(self) -> None:
        self.continued = True
    def skip_vendor(self) -> None:
        self.skipped = True
    def captured_count(self) -> int:
        return 7
    def run(self) -> dict:
        return {}


def _api(tmp_path: Path) -> tuple[Api, _FakeRunner]:
    fake = _FakeRunner()
    api = Api(data_dir=tmp_path, exe_dir=tmp_path / "exe",
              runner_factory=lambda vendors, sink: fake)
    return api, fake


def test_get_state_reports_config_and_vendors(tmp_path: Path) -> None:
    api, _ = _api(tmp_path)
    state = api.get_state()
    assert state["server_url"] == "https://backlogquest.xyz"
    assert state["vendors"] == ["playstation", "xbox", "nintendo", "steam"]
    assert state["has_token"] is False


def test_save_settings_persists(tmp_path: Path) -> None:
    api, _ = _api(tmp_path)
    api.save_settings("https://s.example", "tok")
    assert Api(data_dir=tmp_path, exe_dir=tmp_path / "e",
               runner_factory=lambda v, s: None).get_state()["has_token"] is True


def test_start_scrape_wires_controls_and_events(tmp_path: Path) -> None:
    api, fake = _api(tmp_path)
    api.start_scrape(["playstation"])
    api._thread.join(timeout=5)
    api.continue_login()
    api.skip_vendor()
    assert fake.continued and fake.skipped
    polled = api.poll()
    assert polled["captured"] == 7
    assert isinstance(polled["events"], list)


def test_start_scrape_ignores_reentrant_calls(tmp_path: Path) -> None:
    import threading

    release = threading.Event()

    class _BlockingRunner(_FakeRunner):
        def run(self) -> dict:
            release.wait(timeout=5)
            return {}

    runners: list[_BlockingRunner] = []

    def factory(vendors, sink):
        r = _BlockingRunner()
        runners.append(r)
        return r

    api = Api(data_dir=tmp_path, exe_dir=tmp_path / "exe", runner_factory=factory)
    api.start_scrape(["playstation"])
    api.start_scrape(["playstation"])   # second click while first is running
    release.set()
    api._thread.join(timeout=5)
    assert len(runners) == 1
