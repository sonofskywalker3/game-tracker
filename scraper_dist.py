"""Desktop scraper distribution: locates published artifacts and personalizes
the portable zip with a per-server backlogquest.json sidecar (server URL +
import token) so a downloaded copy talks to the right server out of the box.
"""
import io
import json
import os
import zipfile
from pathlib import Path

SCRAPER_DIR_ENV = "BACKLOGQUEST_SCRAPER_DIR"
SCRAPER_DIR_DEFAULT = "/opt/backlogquest/scraper"
PORTABLE_ZIP = "backlogquest-scraper-portable.zip"
INSTALLER_EXE = "BacklogQuest-Scraper-Setup.exe"
VERSION_FILE = "version.txt"
FLAVOR_PORTABLE = "portable"
FLAVOR_INSTALLER = "installer"


def scraper_dir() -> Path:
    """Directory holding the published scraper artifacts (zip/exe/version)."""
    return Path(os.environ.get(SCRAPER_DIR_ENV) or SCRAPER_DIR_DEFAULT)


def personalized_zip(src: Path, server_url: str, token: str) -> io.BytesIO:
    """Copy the portable zip, adding backlogquest.json beside the exe (zip root dir)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        names = zin.namelist()
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        root = names[0].split("/")[0] + "/" if names and "/" in names[0] else ""
        sidecar = json.dumps({"server_url": server_url, "token": token})
        zout.writestr(f"{root}backlogquest.json", sidecar)
    buf.seek(0)
    return buf
