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
                       get: Callable[..., requests.Response] = requests.get) -> Path:
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


def launch_installer(path: Path, spawn: Callable[..., object] = subprocess.Popen) -> None:
    """Spawn the installer detached so it survives the app exiting under it."""
    detached = (getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    spawn(installer_command(path), close_fds=True, creationflags=detached)
    logger.info("self-update: installer launched, app will now close")
