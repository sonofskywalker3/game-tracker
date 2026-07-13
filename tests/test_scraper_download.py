"""Personalized scraper downloads + public version endpoint."""
import io
import json
import zipfile
from pathlib import Path

import pytest

import app as app_module


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


def test_public_url_env_overrides_request_url_root(client, monkeypatch) -> None:
    monkeypatch.setenv("BACKLOGQUEST_PUBLIC_URL", "https://backlogquest.xyz")
    resp = client.get("/download/scraper?flavor=portable")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as z:
        names = [n for n in z.namelist() if n.endswith("backlogquest.json")]
        sidecar = json.loads(z.read(names[0]))
    assert sidecar["server_url"] == "https://backlogquest.xyz"
