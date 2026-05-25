from pathlib import Path

import pytest

import models
import scrape_service


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the global scrape state around every test (it is module-level)."""
    scrape_service._reset()
    yield
    scrape_service._continue.set()
    scrape_service._cancel.set()
    scrape_service._reset()


def test_status_initial_shape():
    st = scrape_service.status()
    assert st["phase"] == "idle"
    assert st["vendor"] is None
    assert st["summary"] == {}


def test_vendors_constant():
    assert scrape_service.VENDORS == ("playstation", "xbox", "nintendo")


def test_backup_db_copies_when_present(temp_db):
    path = scrape_service.backup_db()
    assert path is not None
    assert Path(path).exists()
    assert ".bak-" in Path(path).name


def test_backup_db_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "DB_PATH", tmp_path / "nope.db")
    assert scrape_service.backup_db() is None
