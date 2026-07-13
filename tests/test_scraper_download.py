"""Personalized scraper downloads + public version endpoint."""
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

import app as app_module


class _PosixSepOs:
    """Proxies the real `os` module but reports sep="/" like Linux.

    zipfile's ZipInfo constructor normalizes backslash-joined names to
    forward slashes only when the running interpreter's `os.sep` is "\\"
    (Windows). The production server is Linux, where that auto-fixup never
    fires. Swapping zipfile's module-level `os` binding for this proxy lets
    a Windows dev/test machine reproduce the real Linux-server read
    behavior for a zip built with raw backslash entry names (as
    PowerShell 5.1's Compress-Archive writes them), without touching the
    real `os` module used by the rest of the process.
    """

    sep = "/"

    def __getattr__(self, name):
        return getattr(os, name)


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch) -> Path:
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("BacklogQuest Scraper/app.exe", b"fake-exe")
    (tmp_path / "backlogquest-scraper-portable.zip").write_bytes(src.getvalue())
    (tmp_path / "BacklogQuest-Scraper-Setup.exe").write_bytes(b"fake-installer")
    (tmp_path / "version.txt").write_text("0.1.0", encoding="utf-8")
    monkeypatch.setenv("BACKLOGQUEST_SCRAPER_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLOGQUEST_IMPORT_TOKEN", "sekrit")
    return tmp_path


@pytest.fixture
def client(artifacts):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_portable_zip_gets_sidecar_with_token(client) -> None:
    resp = client.get("/download/scraper?flavor=portable")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
        names = z.namelist()
        assert "BacklogQuest Scraper/app.exe" in names
        sidecar = json.loads(z.read("BacklogQuest Scraper/backlogquest.json"))
    assert sidecar["token"] == "sekrit"
    assert sidecar["server_url"].startswith("http")


def test_installer_served_as_is(client) -> None:
    resp = client.get("/download/scraper?flavor=installer")
    assert resp.status_code == 200 and resp.data == b"fake-installer"


def _config_from_name(name: str) -> dict:
    """Parse the host/token markers embedded in an installer download name
    (mirrors the Inno [Code] parser in installer/backlogquest-scraper.iss)."""
    host = name.split(".h-", 1)[1].split(".c-", 1)[0]
    tail = name.split(".c-", 1)[1]
    token = ""
    for ch in tail:
        if not (ch.isalnum() or ch in "_-"):
            break
        token += ch
    return {"server_url": f"https://{host}", "token": token}


def test_installer_download_name_embeds_config() -> None:
    from scraper_dist import installer_download_name
    name = installer_download_name("https://backlogquest.xyz", "a" * 64)
    assert name.startswith("BacklogQuest-Scraper-Setup.h-") and name.endswith(".exe")
    cfg = _config_from_name(name)
    assert cfg == {"server_url": "https://backlogquest.xyz", "token": "a" * 64}


def test_installer_download_name_survives_browser_duplicate_suffix() -> None:
    from scraper_dist import installer_download_name
    name = installer_download_name("https://backlogquest.xyz", "abc123")
    renamed = name.replace(".exe", " (1).exe")   # Chrome duplicate-download rename
    assert _config_from_name(renamed)["token"] == "abc123"


def test_installer_download_name_falls_back_plain() -> None:
    from scraper_dist import INSTALLER_EXE, installer_download_name
    assert installer_download_name("https://s", "") == INSTALLER_EXE            # no token
    assert installer_download_name("https://" + "x" * 250, "a" * 64) == INSTALLER_EXE  # too long
    assert installer_download_name("https://evil/path", "tok en") == INSTALLER_EXE     # bad token charset
    assert installer_download_name("https://x.c-y.com", "abc") == INSTALLER_EXE        # ambiguous host


def test_installer_route_personalizes_filename(client) -> None:
    resp = client.get("/download/scraper?flavor=installer")
    disposition = resp.headers["Content-Disposition"]
    assert ".c-" in disposition
    name = disposition.split("filename=")[-1].strip('"')
    cfg = _config_from_name(name)
    assert cfg["token"] == "sekrit"
    assert cfg["server_url"].startswith("http")


def test_unknown_flavor_400_and_missing_artifact_404(client, artifacts: Path) -> None:
    assert client.get("/download/scraper?flavor=weird").status_code == 400
    (artifacts / "backlogquest-scraper-portable.zip").unlink()
    assert client.get("/download/scraper?flavor=portable").status_code == 404


def test_version_endpoint_public(client, monkeypatch) -> None:
    # Auth ON: /api/scraper/version must still answer (it's in _PUBLIC_PATHS).
    monkeypatch.setenv("BACKLOGQUEST_PASSWORD_HASH", "pbkdf2:sha256:x$y$z")
    assert client.get("/api/scraper/version").get_json() == {"version": "0.1.0"}
    # ...while the downloads are gated.
    assert client.get("/download/scraper?flavor=portable").status_code in (302, 401)


def test_sidecar_lands_in_root_folder_even_with_leading_toplevel_file(client, artifacts: Path) -> None:
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("README.txt", b"readme first")           # top-level entry FIRST
        z.writestr("BacklogQuest Scraper/app.exe", b"exe")
    (artifacts / "backlogquest-scraper-portable.zip").write_bytes(src.getvalue())
    resp = client.get("/download/scraper?flavor=portable")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
        assert "backlogquest.json" in z.namelist()           # mixed roots -> zip root is correct
    # And the pure-rooted case still nests it:
    src2 = io.BytesIO()
    with zipfile.ZipFile(src2, "w") as z:
        z.writestr("BacklogQuest Scraper/app.exe", b"exe")
        z.writestr("BacklogQuest Scraper/ui/index.html", b"<html>")
    (artifacts / "backlogquest-scraper-portable.zip").write_bytes(src2.getvalue())
    resp2 = client.get("/download/scraper?flavor=portable")
    with zipfile.ZipFile(io.BytesIO(resp2.data)) as z:
        assert "BacklogQuest Scraper/backlogquest.json" in z.namelist()


def test_sidecar_lands_correctly_with_windows_backslash_entries(
    client, artifacts: Path, monkeypatch
) -> None:
    # PowerShell 5.1's Compress-Archive writes BACKSLASH zip entry names
    # (e.g. "BacklogQuest Scraper\\app.exe"); see _PosixSepOs above for why
    # this must run with zipfile's os.sep spoofed to "/" to reproduce the
    # real (Linux) server condition.
    monkeypatch.setattr(zipfile, "os", _PosixSepOs())
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("BacklogQuest Scraper\\app.exe", b"exe")
        z.writestr("BacklogQuest Scraper\\ui\\index.html", b"<html>")
    (artifacts / "backlogquest-scraper-portable.zip").write_bytes(src.getvalue())
    resp = client.get("/download/scraper?flavor=portable")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
        names = z.namelist()
    normalized = [n.replace("\\", "/") for n in names]
    assert "BacklogQuest Scraper/backlogquest.json" in normalized


def test_public_url_env_overrides_request_url_root(client, monkeypatch) -> None:
    monkeypatch.setenv("BACKLOGQUEST_PUBLIC_URL", "https://backlogquest.xyz")
    resp = client.get("/download/scraper?flavor=portable")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
        names = [n for n in z.namelist() if n.endswith("backlogquest.json")]
        sidecar = json.loads(z.read(names[0]))
    assert sidecar["server_url"] == "https://backlogquest.xyz"


def test_settings_page_offers_both_downloads(client) -> None:
    html = client.get("/settings").data.decode()
    assert "Get the scraper" in html
    assert "/download/scraper?flavor=installer" in html
    assert "/download/scraper?flavor=portable" in html


def test_download_names_carry_published_version(client) -> None:
    disp = client.get("/download/scraper?flavor=installer").headers["Content-Disposition"]
    assert "BacklogQuest-Scraper-Setup-0.1.0.h-" in disp
    disp2 = client.get("/download/scraper?flavor=portable").headers["Content-Disposition"]
    assert "backlogquest-scraper-portable-0.1.0.zip" in disp2
