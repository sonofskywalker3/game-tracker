# Stub Installer + One-Click Self-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Browser downloads shrink to a ~2MB stub that fetches and runs the full installer itself, and the installed app updates itself from the version banner with one click; ship as v0.1.5.

**Architecture:** The full installer stays the single payload artifact. Two new consumers download it directly from a new public `/download/scraper/payload` route: an Inno stub (saves the payload under its own marker-carrying filename so the existing sidecar/token flow works unchanged) and a new `desktop/selfupdate.py` (plain filename, `/VERYSILENT` install, installer relaunches the app).

**Tech Stack:** Inno Setup 6 Pascal scripting (`CreateDownloadPage`), Flask routes, `requests` streaming, pywebview js_api bridge, vanilla JS UI.

**Spec:** `docs/superpowers/specs/2026-07-14-stub-installer-self-update-design.md` (approved @ 439072e).

## Global Constraints

- Tests: `uv run python -m pytest -n auto -q` — NEVER plain `pytest`.
- Lint: `ruff check` only — NEVER `ruff format`.
- Commit directly to `main` and push; no branches.
- Never touch the real `games.db` or the running web app; server-route tests use the Flask test client only.
- `Api._window` stays underscore-private (pywebview#1815; `test_no_public_window_attribute` must stay green).
- Self-update banner copy: download state `Downloading — X.X / Y.Y MB`, then `Installing — the app will restart itself…`, failure `Update failed: <err> — you can keep using this version.`
- Stub download-page copy: `Downloading BacklogQuest Scraper — this skips your browser's slow scan of large files.`
- Installer silent flags, verbatim: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCHAPP=1`.
- `/download/scraper/payload` MUST be publicly reachable (in `_PUBLIC_PATHS`): the stub and self-updater have no auth session. It serves the generic binary only (no token anywhere in it).
- Publish (Task 6) with Inno on PATH: `$env:PATH = "$env:LOCALAPPDATA\Programs\Inno Setup 6;$env:PATH"`. The release script's own ssh/scp publish is authorized; any other droplet command needs owner approval.

---

### Task 1: Payload route + stub-aware installer serving (`scraper_dist.py` + `app.py`)

**Files:**
- Modify: `scraper_dist.py` (add `STUB_EXE`, `installer_artifact()`)
- Modify: `app.py:2450-2486` (installer flavor serves stub when present; new `/download/scraper/payload` route; `_PUBLIC_PATHS` at `app.py:69`)
- Test: `tests/test_scraper_download.py` (extend)

**Interfaces:**
- Consumes: existing `scraper_dist.scraper_dir()`, `INSTALLER_EXE`, `installer_download_name(server_url, token)`.
- Produces: `scraper_dist.STUB_EXE = "BacklogQuest-Scraper-Stub.exe"`; `scraper_dist.installer_artifact() -> Path | None` (stub path when it exists, else full-exe path when it exists, else `None`); route `GET /download/scraper/payload` → full installer bytes, plain `BacklogQuest-Scraper-Setup.exe` download name, 404 when unpublished, public under auth.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scraper_download.py`:

```python
# --- stub + payload distribution (stub installer feature) -----------------

def test_installer_flavor_serves_stub_when_published(client, artifacts: Path) -> None:
    (artifacts / "BacklogQuest-Scraper-Stub.exe").write_bytes(b"tiny-stub")
    resp = client.get("/download/scraper?flavor=installer")
    assert resp.status_code == 200 and resp.data == b"tiny-stub"
    # Download NAME is unchanged (markers + version) — only the bytes shrink.
    name = resp.headers["Content-Disposition"].split("filename=")[-1].strip('"')
    assert name.startswith("BacklogQuest-Scraper-Setup-0.1.0.h-")
    assert _config_from_name(name)["token"] == "sekrit"


def test_installer_flavor_falls_back_to_full_exe_without_stub(client) -> None:
    resp = client.get("/download/scraper?flavor=installer")
    assert resp.status_code == 200 and resp.data == b"fake-installer"


def test_payload_route_serves_full_installer_plain(client, artifacts: Path) -> None:
    (artifacts / "BacklogQuest-Scraper-Stub.exe").write_bytes(b"tiny-stub")
    resp = client.get("/download/scraper/payload")
    assert resp.status_code == 200 and resp.data == b"fake-installer"   # never the stub
    disposition = resp.headers["Content-Disposition"]
    assert "BacklogQuest-Scraper-Setup.exe" in disposition
    assert ".h-" not in disposition and ".c-" not in disposition        # no markers


def test_payload_route_404_when_unpublished(client, artifacts: Path) -> None:
    (artifacts / "BacklogQuest-Scraper-Setup.exe").unlink()
    assert client.get("/download/scraper/payload").status_code == 404


def test_payload_route_public_under_auth(client, monkeypatch) -> None:
    # The stub runs on a fresh machine and the self-updater has no browser
    # session — the payload route must bypass the auth gate.
    monkeypatch.setenv("BACKLOGQUEST_PASSWORD_HASH", "pbkdf2:sha256:x$y$z")
    assert client.get("/download/scraper/payload").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_scraper_download.py -q`
Expected: the five new tests FAIL (`test_installer_flavor_serves_stub_when_published` gets `b"fake-installer"`; payload-route tests get 404s from Flask's routing); all pre-existing tests still pass.

- [ ] **Step 3: Implement**

In `scraper_dist.py`, add below `INSTALLER_EXE`:

```python
STUB_EXE = "BacklogQuest-Scraper-Stub.exe"
```

Add after `artifact_version()`:

```python
def installer_artifact() -> Path | None:
    """Bytes to serve for the installer download: the ~2MB stub when
    published (browsers deep-scan large exes), else the full installer
    (deploy-safe fallback while the stub isn't uploaded yet), else None."""
    for name in (STUB_EXE, INSTALLER_EXE):
        path = scraper_dir() / name
        if path.exists():
            return path
    return None
```

In `app.py`, replace the installer branch of `download_scraper()` (the
`if flavor == scraper_dist.FLAVOR_INSTALLER:` block):

```python
    if flavor == scraper_dist.FLAVOR_INSTALLER:
        path = scraper_dist.installer_artifact()
        if path is None:
            return jsonify({"error": "installer not available"}), 404
        return send_file(path, as_attachment=True,
                         download_name=scraper_dist.installer_download_name(server_url, token))
```

Add after the `download_scraper` route:

```python
@app.route("/download/scraper/payload")
def download_scraper_payload():
    """The full installer, served plain (no marker filename). Fetched by the
    stub installer and the desktop app's self-updater — machines without a
    browser session, hence public. The binary is generic (no token inside)."""
    path = scraper_dist.scraper_dir() / scraper_dist.INSTALLER_EXE
    if not path.exists():
        return jsonify({"error": "installer not available"}), 404
    return send_file(path, as_attachment=True,
                     download_name=scraper_dist.INSTALLER_EXE)
```

In `app.py:69`, extend `_PUBLIC_PATHS`:

```python
_PUBLIC_PATHS = frozenset({"/login", "/logout", "/healthz", "/api/scraper/version",
                           "/download/scraper/payload"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_scraper_download.py -q`
Expected: all pass (existing 13 + new 5)

- [ ] **Step 5: Lint and commit**

```powershell
uv run ruff check scraper_dist.py app.py tests/test_scraper_download.py
git add scraper_dist.py app.py tests/test_scraper_download.py
git commit -m "feat(dist): public payload route + stub-aware installer serving"
```

---

### Task 2: `desktop/selfupdate.py` (download, command, launch)

**Files:**
- Create: `desktop/selfupdate.py`
- Test: `tests/test_desktop_selfupdate.py` (create)

**Interfaces:**
- Consumes: route `GET <server_url>/download/scraper/payload` from Task 1.
- Produces: `download_installer(server_url: str, version: str, dest_dir: Path | None = None, progress: Callable[[int, int], None] | None = None, get=requests.get) -> Path`; `installer_command(path: Path) -> list[str]`; `launch_installer(path: Path, spawn=subprocess.Popen) -> None`; constant `PAYLOAD_ROUTE = "/download/scraper/payload"`. Task 3's bridge calls `download_installer` and `launch_installer`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_desktop_selfupdate.py`:

```python
"""Self-update: streamed installer download with progress + exact silent command."""
from pathlib import Path

import pytest

from desktop import selfupdate


class _FakeStream:
    def __init__(self, chunks: list[bytes], status: int = 200, total: int | None = None):
        self._chunks, self._status = chunks, status
        size = total if total is not None else sum(len(c) for c in chunks)
        self.headers = {"Content-Length": str(size)} if size else {}

    def raise_for_status(self):
        if self._status != 200:
            raise RuntimeError(f"HTTP {self._status}")

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_streams_to_plain_versioned_name_with_progress(tmp_path: Path):
    seen, urls = [], []

    def fake_get(url, stream=None, timeout=None):
        urls.append(url)
        return _FakeStream([b"aa", b"bb", b"c"])

    dest = selfupdate.download_installer("https://backlogquest.xyz/", "0.1.5",
                                         dest_dir=tmp_path, progress=lambda d, t: seen.append((d, t)),
                                         get=fake_get)
    assert urls == ["https://backlogquest.xyz/download/scraper/payload"]
    assert dest == tmp_path / "BacklogQuest-Scraper-Setup-0.1.5.exe"
    assert dest.read_bytes() == b"aabbc"
    assert seen == [(2, 5), (4, 5), (5, 5)]
    assert ".h-" not in dest.name and ".c-" not in dest.name   # plain: sidecar untouched


def test_download_overwrites_previous_attempt(tmp_path: Path):
    (tmp_path / "BacklogQuest-Scraper-Setup-0.1.5.exe").write_bytes(b"old-half-download")
    dest = selfupdate.download_installer("https://s", "0.1.5", dest_dir=tmp_path,
                                         get=lambda url, stream=None, timeout=None: _FakeStream([b"new"]))
    assert dest.read_bytes() == b"new"


def test_download_http_error_raises_and_leaves_no_file(tmp_path: Path):
    with pytest.raises(RuntimeError, match="HTTP 404"):
        selfupdate.download_installer("https://s", "0.1.5", dest_dir=tmp_path,
                                      get=lambda url, stream=None, timeout=None: _FakeStream([], status=404))
    assert list(tmp_path.iterdir()) == []


def test_installer_command_exact_flags(tmp_path: Path):
    exe = tmp_path / "setup.exe"
    assert selfupdate.installer_command(exe) == [
        str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCHAPP=1"]


def test_launch_installer_spawns_detached(tmp_path: Path):
    calls = {}

    def fake_spawn(cmd, **kwargs):
        calls["cmd"], calls["kwargs"] = cmd, kwargs

    selfupdate.launch_installer(tmp_path / "setup.exe", spawn=fake_spawn)
    assert calls["cmd"] == selfupdate.installer_command(tmp_path / "setup.exe")
    assert calls["kwargs"]["close_fds"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_desktop_selfupdate.py -q`
Expected: ERROR — `ModuleNotFoundError: No module named 'desktop.selfupdate'`

- [ ] **Step 3: Implement**

Create `desktop/selfupdate.py`:

```python
"""One-click self-update: stream the full installer from the server, run it
silently, let the installer relaunch the app (/RELAUNCHAPP=1). Downloading
here (not the browser) skips Chrome's minute-long deep scan of large exes."""
from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

PAYLOAD_ROUTE = "/download/scraper/payload"
INSTALLER_NAME = "BacklogQuest-Scraper-Setup-{version}.exe"
# Plain filename on purpose: a marker-suffixed name would make the installer
# rewrite the backlogquest.json sidecar; an updating install already has config.
SILENT_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCHAPP=1")
_CHUNK_BYTES = 65536
_TIMEOUT_S = 30

Progress = Callable[[int, int], None]   # (bytes_done, bytes_total; total 0 if unknown)


def download_installer(server_url: str, version: str, dest_dir: Path | None = None,
                       progress: Progress | None = None,
                       get=requests.get) -> Path:
    """Stream the payload to %TEMP% (overwriting any previous attempt);
    returns the installer path. Raises on HTTP/network errors — the caller
    reports and the running app stays untouched."""
    url = server_url.rstrip("/") + PAYLOAD_ROUTE
    dest = (dest_dir or Path(tempfile.gettempdir())) / INSTALLER_NAME.format(version=version)
    resp = get(url, stream=True, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length") or 0)
    done = 0
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(_CHUNK_BYTES):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    logger.info("self-update: downloaded %s (%d bytes)", dest.name, done)
    return dest


def installer_command(path: Path) -> list[str]:
    """The exact silent-install invocation (pure; asserted by tests)."""
    return [str(path), *SILENT_FLAGS]


def launch_installer(path: Path, spawn=subprocess.Popen) -> None:
    """Spawn the installer detached so it survives the app exiting under it."""
    detached = (getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    spawn(installer_command(path), close_fds=True, creationflags=detached)
    logger.info("self-update: installer launched, app will now close")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_desktop_selfupdate.py -q`
Expected: 5 passed

Note: `test_download_http_error_raises_and_leaves_no_file` requires that
`raise_for_status()` runs BEFORE the file is opened — if it fails because an
empty file exists, the open call is ordered wrong.

- [ ] **Step 5: Lint and commit**

```powershell
uv run ruff check desktop/selfupdate.py tests/test_desktop_selfupdate.py
git add desktop/selfupdate.py tests/test_desktop_selfupdate.py
git commit -m "feat(desktop): selfupdate module — streamed payload download + silent install command"
```

---

### Task 3: Bridge `start_update()` + banner UI

**Files:**
- Modify: `desktop/bridge.py` (import selfupdate, `_update_thread`, `start_update`, `_do_update`)
- Modify: `desktop/ui/index.html:15-16` (banner gains button + status span)
- Modify: `desktop/ui/app.js` (update events in `handleEvent`, `stopPolling` helper, `update-now` handler)
- Test: `tests/test_desktop_bridge.py` (extend)

**Interfaces:**
- Consumes: `selfupdate.download_installer(server_url, version, dest_dir=None, progress=None, get=...)`, `selfupdate.launch_installer(path)` from Task 2; existing `check_for_update`, `Api._events` queue, `Api._window`.
- Produces: js_api method `start_update() -> None`; poll events `{"type": "update_progress", "done": int, "total": int}`, `{"type": "update_installing"}`, `{"type": "update_failed", "error": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_desktop_bridge.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_desktop_bridge.py -q`
Expected: 3 new tests FAIL with `AttributeError: ... has no attribute 'start_update'` (or missing `selfupdate` attr on the module); the 8 existing tests still pass.

- [ ] **Step 3: Implement the bridge**

In `desktop/bridge.py`, add to the imports:

```python
from desktop import selfupdate
```

In `Api.__init__`, after `self._sync_thread: threading.Thread | None = None`:

```python
        self._update_thread: threading.Thread | None = None
```

Add after the `sync()` method:

```python
    # -- self-update --------------------------------------------------------------
    def start_update(self) -> None:
        # Same double-click guard rationale as start_scrape.
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        self._update_thread = threading.Thread(target=self._do_update, daemon=True)
        self._update_thread.start()

    def _do_update(self) -> None:
        try:
            version = check_for_update(self._config.server_url) or APP_VERSION
            path = selfupdate.download_installer(
                self._config.server_url, version,
                progress=lambda done, total: self._events.put(
                    {"type": "update_progress", "done": done, "total": total}))
            self._events.put({"type": "update_installing"})
            selfupdate.launch_installer(path)
            if self._window is not None:
                self._window.destroy()   # installer takes over and relaunches us
        except Exception as exc:   # any failure must leave the running app usable
            logger.exception("self-update failed")
            self._events.put({"type": "update_failed", "error": str(exc)})
```

- [ ] **Step 4: Run bridge tests to verify they pass**

Run: `uv run python -m pytest tests/test_desktop_bridge.py -q`
Expected: 11 passed (including `test_no_public_window_attribute`, untouched)

- [ ] **Step 5: Update the banner HTML**

In `desktop/ui/index.html`, replace lines 15-16:

```html
<div id="update-banner" class="hidden">Update available — <span id="update-version"></span>
  <button id="update-now">Update now</button> <span id="update-status"></span></div>
```

- [ ] **Step 6: Update app.js**

Replace the two bare `clearInterval(pollTimer)` calls (in `handleEvent`'s
`finished` and `synced` branches) with `stopPolling()`, and add the helper +
re-entrancy guard so the update button can share the poll loop:

```js
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }
```

At the top of `startPolling()` add:

```js
  if (pollTimer) return;
```

Extend `handleEvent` — insert before the closing brace of the `if/else` chain:

```js
  } else if (e.type === "update_progress") {
    const mb = (n) => (n / 1048576).toFixed(1);
    $("update-status").textContent = e.total
      ? `Downloading — ${mb(e.done)} / ${mb(e.total)} MB`
      : `Downloading — ${mb(e.done)} MB`;
  } else if (e.type === "update_installing") {
    $("update-status").textContent = "Installing — the app will restart itself…";
  } else if (e.type === "update_failed") {
    stopPolling();
    $("update-now").disabled = false;
    $("update-status").textContent =
      "Update failed: " + e.error + " — you can keep using this version.";
  }
```

Add the click handler next to the other `onclick` bindings:

```js
$("update-now").onclick = () => {
  $("update-now").disabled = true;
  $("update-status").textContent = "Starting download…";
  window.pywebview.api.start_update();
  startPolling();
};
```

(`update_failed` uses `textContent`, so the error string needs no escaping.)

- [ ] **Step 7: Full suite, lint, commit**

Run: `uv run python -m pytest -n auto -q` then `uv run ruff check .`
Expected: green / clean

```powershell
git add desktop/bridge.py desktop/ui/index.html desktop/ui/app.js tests/test_desktop_bridge.py
git commit -m "feat(desktop): one-click self-update from the version banner"
```

---

### Task 4: Full-installer additions (close-app + relaunch)

**Files:**
- Modify: `installer/backlogquest-scraper.iss` (`[Setup]` directives + `[Code]` relaunch)

No pytest coverage possible (Pascal); verified by compiling in Task 5 and the live smoke in Task 6.

- [ ] **Step 1: Add the Setup directives**

In `installer/backlogquest-scraper.iss` `[Setup]`, after `PrivilegesRequired=lowest`:

```ini
; Self-update installs run /VERYSILENT while the app is still tearing down —
; let Restart Manager close it instead of dying on a locked exe.
CloseApplications=force
RestartApplications=no
```

- [ ] **Step 2: Add the relaunch code**

In the `[Code]` section, add above `CurStepChanged`:

```pascal
{ The self-updater passes /RELAUNCHAPP=1: relaunch the app when the silent
  install finishes so the update feels like a restart, not an exit. }
function CmdLineParamExists(const Value: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Value) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;
```

Extend the existing `CurStepChanged` procedure — add a `ResultCode` variable
and an `ssDone` branch so it reads:

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
var
  Cfg: string;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Cfg := DecodeConfigFromFileName(ExpandConstant('{srcexe}'));
    if Cfg <> '' then
      SaveStringToFile(ExpandConstant('{app}\backlogquest.json'), Cfg, False);
  end;
  if (CurStep = ssDone) and CmdLineParamExists('/RELAUNCHAPP=1') then
    Exec(ExpandConstant('{app}\BacklogQuest Scraper.exe'), '', '', SW_SHOWNORMAL,
         ewNoWait, ResultCode);
end;
```

- [ ] **Step 3: Commit**

```powershell
git add installer/backlogquest-scraper.iss
git commit -m "feat(installer): CloseApplications=force + /RELAUNCHAPP=1 for silent self-update"
```

---

### Task 5: Stub installer script + release pipeline

**Files:**
- Create: `installer/backlogquest-scraper-stub.iss`
- Modify: `release_scraper.ps1:29-38` (compile stub, publish it)

- [ ] **Step 1: Write the stub script**

Create `installer/backlogquest-scraper-stub.iss`:

```pascal
; installer/backlogquest-scraper-stub.iss — ~2MB bootstrap the BROWSER downloads
; (small enough that Chrome's deep scan is instant). It fetches the full
; installer from the server and runs it. Compile with:
;   iscc /DAppVersion=<version> installer\backlogquest-scraper-stub.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=BacklogQuest Scraper
AppVersion={#AppVersion}
AppPublisher=BacklogQuest
CreateAppDir=no
Uninstallable=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=BacklogQuest-Scraper-Stub
OutputDir=..\dist
SetupIconFile=..\desktop\assets\backlogquest.ico

[Messages]
ReadyLabel1=Ready to download and install BacklogQuest Scraper.
ReadyLabel2a=Setup will download the app from your BacklogQuest server (this skips your browser's slow scan of large files), then install it.

[Code]
const
  DefaultHost = 'backlogquest.xyz';

var
  DownloadPage: TDownloadWizardPage;
  PayloadExitCode: Integer;

{ Same marker convention as the full installer: the download server names this
  stub "...h-<host>.c-<token>.exe". The stub only needs <host> (to know where
  to download from) but preserves the WHOLE name onto the payload so the full
  installer's own filename-decoding writes the sidecar unchanged. }
function HostFromFileName(const SetupPath: string): string;
var
  Name: string;
  H, C, I: Integer;
begin
  Result := DefaultHost;
  Name := ExtractFileName(SetupPath);
  H := Pos('.h-', Name);
  C := Pos('.c-', Name);
  if (H = 0) or (C = 0) or (C <= H) then Exit;
  Result := Copy(Name, H + 3, C - (H + 3));
  if Result = '' then Result := DefaultHost;
end;

{ Payload keeps the stub's own filename so markers survive; strip a browser's
  " (1)" duplicate suffix is unnecessary — the token parser in the full
  installer already stops at the first out-of-charset character. }
function PayloadFileName: string;
begin
  Result := ExtractFileName(ExpandConstant('{srcexe}'));
end;

function OnDownloadProgress(const Url, FileName: string; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing),
    'Downloading BacklogQuest Scraper — this skips your browser''s slow scan of large files.',
    @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Host: string;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    Host := HostFromFileName(ExpandConstant('{srcexe}'));
    DownloadPage.Clear;
    DownloadPage.Add('https://' + Host + '/download/scraper/payload', PayloadFileName, '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        SuppressibleMsgBox('Download failed from https://' + Host +
          '/download/scraper/payload' + #13#10 + GetExceptionMessage,
          mbCriticalError, MB_OK, IDOK);
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Args: string;
begin
  if CurStep = ssPostInstall then
  begin
    { Forward silent mode so a scripted "stub /VERYSILENT" stays silent
      end-to-end; interactive runs get the full installer's normal wizard. }
    Args := '';
    if WizardSilent then
      Args := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART';
    if not Exec(ExpandConstant('{tmp}\') + PayloadFileName, Args, '',
                SW_SHOWNORMAL, ewWaitUntilTerminated, PayloadExitCode) then
      SuppressibleMsgBox('Could not start the downloaded installer.',
        mbCriticalError, MB_OK, IDOK);
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := PayloadExitCode;   { 0 on success; surfaces the inner install's failure }
end;
```

- [ ] **Step 2: Extend the release script**

In `release_scraper.ps1`, after the existing `iscc` call (line 29-30), add:

```powershell
& iscc /DAppVersion=$version installer\backlogquest-scraper-stub.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed on the stub (is iscc on PATH?)" }
```

And add the stub to the scp line (line 38):

```powershell
  scp $portable "dist/BacklogQuest-Scraper-Setup.exe" "dist/BacklogQuest-Scraper-Stub.exe" dist/version.txt gametracker:/opt/backlogquest/scraper/
```

- [ ] **Step 3: Compile-verify both installers locally (no publish)**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Inno Setup 6;$env:PATH"
& iscc /DAppVersion=0.0.0-test installer\backlogquest-scraper-stub.iss
Get-Item dist/BacklogQuest-Scraper-Stub.exe | Select-Object Name, Length
```

Expected: compile succeeds; stub is roughly 1.5–3 MB. (The full installer's
Pascal compiles in Task 6's release build; a syntax error would stop it there,
so also compile it here to fail fast:)

```powershell
& iscc /DAppVersion=0.0.0-test installer\backlogquest-scraper.iss
```

Expected: `Successful compile`.

- [ ] **Step 4: Commit**

```powershell
git add installer/backlogquest-scraper-stub.iss release_scraper.ps1
git commit -m "feat(installer): 2MB download stub that fetches and runs the full installer"
```

---

### Task 6: v0.1.5 — bump, publish, live verification

**Files:**
- Modify: `desktop/versioncheck.py:12` (`APP_VERSION = "0.1.4"` → `"0.1.5"`)

- [ ] **Step 1: Bump, full suite, lint, push**

```python
APP_VERSION = "0.1.5"
```

Run: `uv run python -m pytest -n auto -q` then `uv run ruff check .`
Expected: green / clean

```powershell
git add desktop/versioncheck.py
git commit -m "chore(desktop): v0.1.5 -- stub installer + one-click self-update"
git push
```

- [ ] **Step 2: Build + publish**

```powershell
$env:PATH = "$env:LOCALAPPDATA\Programs\Inno Setup 6;$env:PATH"
./release_scraper.ps1
```

Expected: both installers compile; `Published v0.1.5 to the droplet.`

- [ ] **Step 3: OWNER-GATED — deploy the new routes**

The payload route and stub-serving logic are in `app.py`, which runs on the
droplet: they only go live after a droplet `git pull` + service restart.
**Ask the owner before running any droplet command.** Until then, published
artifacts sit on disk but `/download/scraper/payload` 404s (route absent) —
the stub can't work yet. Suggested (owner runs or approves):

```
ssh gametracker "cd /opt/backlogquest/app && git pull && systemctl restart backlogquest"
```

- [ ] **Step 4: Live verification (read-only, after deploy)**

```powershell
Invoke-RestMethod https://backlogquest.xyz/api/scraper/version           # -> 0.1.5
Invoke-WebRequest -Method Head https://backlogquest.xyz/download/scraper/payload |
  Select-Object -ExpandProperty Headers                                   # 200, ~41MB Content-Length
Invoke-WebRequest -Method Head "https://backlogquest.xyz/download/scraper?flavor=installer"
```

Expected: version 0.1.5; payload HEAD 200 with a ~41MB length; installer
flavor 200 with a ~2MB length (the stub) and the marker-stamped filename in
Content-Disposition.

- [ ] **Step 5: Live smoke — stub install (updates this PC's installed app)**

Download the stub the way a user would (personalized name), then run it
silently; it must download the payload and silently install 0.1.5:

```powershell
$dl = Invoke-WebRequest "https://backlogquest.xyz/download/scraper?flavor=installer" -OutFile "$env:TEMP\stub-download.exe" -PassThru
$name = ($dl.Headers["Content-Disposition"] -split "filename=")[-1].Trim('"')
Move-Item "$env:TEMP\stub-download.exe" "$env:TEMP\$name" -Force
& "$env:TEMP\$name" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Wait for the process to exit, then verify the installed version:

```powershell
Get-Content "$env:LOCALAPPDATA\Programs\BacklogQuest Scraper\_internal\desktop\ui\index.html" | Out-Null  # exists
```

and confirm the app launches as v0.1.5 (title banner) — this one's for the
owner to eyeball, or check the exe's VersionInfo:

```powershell
(Get-Item "$env:LOCALAPPDATA\Programs\BacklogQuest Scraper\BacklogQuest Scraper.exe").VersionInfo.ProductVersion
```

- [ ] **Step 6: Live smoke — one-click updater plumbing (from source, no GUI)**

The installed 0.1.4→0.1.5 jump used the stub; the in-app updater can't be
end-to-end tested until 0.1.6 exists. Verify its plumbing from source against
the live server instead — this downloads the real payload and CHECKS the
command without running the installer:

```python
# <scratchpad>/selfupdate_smoke.py
from pathlib import Path
from desktop import selfupdate
ticks = []
path = selfupdate.download_installer("https://backlogquest.xyz", "0.1.5",
                                     progress=lambda d, t: ticks.append((d, t)))
assert path.exists() and path.stat().st_size > 30_000_000, path.stat().st_size
assert ticks and ticks[-1][0] == ticks[-1][1] != 0
print("SELFUPDATE SMOKE OK:", path, f"{path.stat().st_size/1048576:.1f} MB, {len(ticks)} progress ticks")
```

Run: `$env:PYTHONPATH = "C:\Users\Jeff\Documents\Projects\Game Tracker"; uv run python <scratchpad>/selfupdate_smoke.py`
Expected: `SELFUPDATE SMOKE OK: ... 41.x MB, N progress ticks`

---

## Self-Review (done at plan time)

- **Spec coverage:** stub .iss ✓ (Task 5), server distribution + fallback + public payload route ✓ (Task 1), release script ✓ (Task 5), selfupdate module ✓ (Task 2), banner UI + bridge ✓ (Task 3), CloseApplications/RELAUNCHAPP ✓ (Task 4), error handling ✓ (tests in Tasks 1-3 + stub msgbox), live smokes ✓ (Task 6), rollout note honored (stub smoke updates the installed 0.1.4).
- **Placeholder scan:** none — full code in every step.
- **Type consistency:** `download_installer(server_url, version, dest_dir=None, progress=None, get=...)` identical in Tasks 2/3/6; `installer_command`/`SILENT_FLAGS` match Task 4's `/RELAUNCHAPP=1` `CmdLineParamExists` check; event types `update_progress`/`update_installing`/`update_failed` match between bridge (Task 3 step 3) and app.js (Task 3 step 6); `STUB_EXE` filename matches the .iss `OutputBaseFilename` + release-script scp.
