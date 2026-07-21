"""Shared pytest fixtures: isolated temp DB + Flask test client."""
import pytest

import models
import app as app_module

# Re-export the multi-user temp-DB fixture so tests can request `mu_db` by name
# without importing it (helpers_multiuser owns the definition; Task 9 reuses it).
from tests.helpers_multiuser import mu_db  # noqa: F401


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point models at a throwaway DB and build the FULL schema exactly the way
    the app does on startup (init_db + migrate_db). No hand-maintained migration
    list: a third parallel schema definition is what let the fresh-install schema
    drift unnoticed — new migrations are now picked up here automatically."""
    db_path = tmp_path / "test_games.db"
    monkeypatch.setattr(models, "DB_PATH", db_path)
    models.init_db()
    models.migrate_db()
    return db_path


@pytest.fixture
def client(temp_db):
    """Flask test client backed by the temp DB."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()
