"""Api bridge logic with an injected fake runner factory (no webview, no Playwright)."""
import json
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


def test_save_settings_keeps_existing_token_when_submitted_blank(tmp_path: Path) -> None:
    # The UI never round-trips the real token back into the token field, so a
    # blank submission must be treated as "unchanged", not "clear it" -- else
    # editing just the server URL silently wipes a portable user's token.
    api, _ = _api(tmp_path)
    api.save_settings("https://a", "tok1")
    api.save_settings("https://b", "")
    assert api.get_state()["has_token"] is True
    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert persisted["token"] == "tok1"


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


def test_finished_event_auto_syncs_when_token_configured(tmp_path: Path, monkeypatch) -> None:
    api, _ = _api(tmp_path)
    api.save_settings("https://s.example", "tok")
    payload = tmp_path / "playstation.json"
    payload.write_text(json.dumps({"source": "playstation", "games": []}), encoding="utf-8")
    monkeypatch.setattr(Api, "sync", lambda self: [
        {"source": "playstation", "ok": True, "summary": "added 1", "retryable": False}])
    api._on_event({"type": "finished", "results": {"playstation": str(payload)}})
    api._sync_thread.join(timeout=5)
    types = [e["type"] for e in api.poll()["events"]]
    assert types == ["finished", "syncing", "synced"]


def test_finished_event_skips_sync_without_token(tmp_path: Path) -> None:
    api, _ = _api(tmp_path)
    api._on_event({"type": "finished", "results": {}})
    assert [e["type"] for e in api.poll()["events"]] == ["finished"]
    assert api._sync_thread is None


def test_no_public_window_attribute(tmp_path: Path) -> None:
    # pywebview serializes the js_api's PUBLIC attributes; a Window attr recurses
    # through window.native.AccessibilityObject.Bounds.Empty... and wedges the UI
    # thread when an accessibility client probes the window (pywebview#1815).
    api, _ = _api(tmp_path)
    assert not hasattr(api, "window")
    assert hasattr(api, "_window")


def _drain(api):
    return api.poll()["events"]


def test_start_update_downloads_installs_and_closes_window(tmp_path: Path, monkeypatch) -> None:
    from desktop import bridge as bridge_mod

    api, _ = _api(tmp_path)

    class _FakeWindow:
        destroyed = False
        def destroy(self):
            self.destroyed = True

    api._window = _FakeWindow()
    monkeypatch.setattr(bridge_mod, "check_for_update", lambda url: "9.9.9")
    launched = []

    def fake_download(server_url, version, dest_dir=None, progress=None, get=None):
        assert version == "9.9.9"
        progress(1024, 2048)
        return Path(tmp_path / "setup.exe")

    monkeypatch.setattr(bridge_mod.selfupdate, "download_installer", fake_download)
    monkeypatch.setattr(bridge_mod.selfupdate, "launch_installer",
                        lambda path: launched.append(path))
    api.start_update()
    api._update_thread.join(timeout=5)
    assert launched == [tmp_path / "setup.exe"]
    assert api._window.destroyed is True
    types = [e["type"] for e in _drain(api)]
    assert types == ["update_progress", "update_installing"]


def test_start_update_failure_reports_and_leaves_app_running(tmp_path: Path, monkeypatch) -> None:
    from desktop import bridge as bridge_mod

    api, _ = _api(tmp_path)
    api._window = None
    monkeypatch.setattr(bridge_mod, "check_for_update", lambda url: "9.9.9")
    def boom(*args, **kwargs):
        raise RuntimeError("HTTP 404")
    monkeypatch.setattr(bridge_mod.selfupdate, "download_installer", boom)
    api.start_update()
    api._update_thread.join(timeout=5)
    events = _drain(api)
    assert events == [{"type": "update_failed", "error": "HTTP 404"}]


def test_start_update_ignores_reentrant_clicks(tmp_path: Path, monkeypatch) -> None:
    import threading

    from desktop import bridge as bridge_mod

    api, _ = _api(tmp_path)
    api._window = None
    release = threading.Event()
    starts = []
    monkeypatch.setattr(bridge_mod, "check_for_update", lambda url: "9.9.9")

    def slow_download(server_url, version, dest_dir=None, progress=None, get=None):
        starts.append(1)
        release.wait(timeout=5)
        raise RuntimeError("cancelled")

    monkeypatch.setattr(bridge_mod.selfupdate, "download_installer", slow_download)
    api.start_update()
    api.start_update()          # double-click while downloading
    release.set()
    api._update_thread.join(timeout=5)
    assert starts == [1]
