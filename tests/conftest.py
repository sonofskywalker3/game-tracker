"""Shared pytest fixtures: isolated temp DB + Flask test client."""
import pytest

import models
import app as app_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point models at a throwaway DB and initialize the schema."""
    db_path = tmp_path / "test_games.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    models.init_db()
    return db_path


@pytest.fixture
def client(temp_db):
    """Flask test client backed by the temp DB."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()
