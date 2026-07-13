"""Desktop scraper distribution: locates published artifacts and personalizes
the portable zip with a per-server backlogquest.json sidecar (server URL +
import token) so a downloaded copy talks to the right server out of the box.
"""
import io
import json
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

SCRAPER_DIR_ENV = "BACKLOGQUEST_SCRAPER_DIR"
PUBLIC_URL_ENV = "BACKLOGQUEST_PUBLIC_URL"
SCRAPER_DIR_DEFAULT = "/opt/backlogquest/scraper"
PORTABLE_ZIP = "backlogquest-scraper-portable.zip"
INSTALLER_EXE = "BacklogQuest-Scraper-Setup.exe"
VERSION_FILE = "version.txt"
FLAVOR_PORTABLE = "portable"
FLAVOR_INSTALLER = "installer"


def scraper_dir() -> Path:
    """Directory holding the published scraper artifacts (zip/exe/version)."""
    return Path(os.environ.get(SCRAPER_DIR_ENV) or SCRAPER_DIR_DEFAULT)


def common_zip_root(names: list[str]) -> str:
    """Common first-path-segment across ALL zip entries, or "" if there isn't one.

    A single top-level entry with no "/" (e.g. a stray README.txt) means the
    entries don't all share one app folder, so the correct root is "".

    Names are normalized to forward slashes before checking: Windows
    PowerShell 5.1's Compress-Archive writes BACKSLASH zip entry names
    (e.g. "BacklogQuest Scraper\\app.exe"), and CPython's zipfile only
    auto-fixes that to "/" on read when the reading host's os.sep is "\\"
    (i.e. never on the Linux server), so raw backslashes can reach here.
    """
    if not names:
        return ""
    normalized = [name.replace("\\", "/") for name in names]
    segments = {name.split("/")[0] for name in normalized if "/" in name}
    all_share_root = len(segments) == 1 and all("/" in name for name in normalized)
    if all_share_root:
        return next(iter(segments)) + "/"
    return ""


def public_server_url(fallback: str) -> str:
    """Public base URL for the sidecar's server_url.

    BACKLOGQUEST_PUBLIC_URL (deploy-time, set behind the Caddy proxy) wins
    verbatim; otherwise fall back to the caller-supplied request URL root
    (keeps local dev + tests working).
    """
    public_url = os.environ.get(PUBLIC_URL_ENV)
    if public_url:
        return public_url.rstrip("/")
    return fallback.rstrip("/")


# Windows caps a filename component at 255 chars; stay comfortably under so a
# browser's duplicate-download suffix (" (1)") can't push the name over.
MAX_DOWNLOAD_NAME_CHARS = 240
_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")
_HOST_CHARS = re.compile(r"^[A-Za-z0-9.-]+$")


def artifact_version() -> str:
    """Published version string from version.txt, or "" when unpublished."""
    path = scraper_dir() / VERSION_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _setup_base() -> str:
    """Installer base name, version-stamped when a version is published (so a
    re-download doesn't collide in the browser's Downloads folder)."""
    version = artifact_version()
    return f"BacklogQuest-Scraper-Setup-{version}" if version else "BacklogQuest-Scraper-Setup"


def installer_download_name(server_url: str, token: str) -> str:
    """Installer download filename with the sidecar config embedded as markers.

    `BacklogQuest-Scraper-Setup.h-<host>.c-<token>.exe` — the Inno installer
    parses its own filename at install time ([Code] in
    backlogquest-scraper.iss) and writes backlogquest.json into {app}, which
    the app's first-run sidecar seeding then consumes. The served binary is
    untouched (survives a future Authenticode signature). https is assumed.
    Falls back to the plain name (paste-the-token flow) when there is no
    token, a charset would be ambiguous to parse, or the name would overflow
    the filename budget.
    """
    base = _setup_base()
    if not token or not _TOKEN_CHARS.fullmatch(token):
        return f"{base}.exe"
    host = urlsplit(server_url).netloc
    if not host or not _HOST_CHARS.fullmatch(host) or ".c-" in f".h-{host}":
        return f"{base}.exe"
    name = f"{base}.h-{host}.c-{token}.exe"
    if len(name) > MAX_DOWNLOAD_NAME_CHARS:
        return f"{base}.exe"
    return name


def portable_download_name() -> str:
    """Portable zip download name, version-stamped when published."""
    version = artifact_version()
    return (f"backlogquest-scraper-portable-{version}.zip" if version
            else PORTABLE_ZIP)


def personalized_zip(src: Path, server_url: str, token: str) -> io.BytesIO:
    """Copy the portable zip, adding backlogquest.json beside the exe (zip root dir)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        names = zin.namelist()
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        root = common_zip_root(names)
        sidecar = json.dumps({"server_url": server_url, "token": token})
        zout.writestr(f"{root}backlogquest.json", sidecar)
    buf.seek(0)
    return buf
