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
    conn = models.get_db()
    models.migrate_dlc(conn)
    models.migrate_dlc_external_ids(conn)
    models.migrate_dlc_review_queue(conn)
    models.migrate_slots(conn)
    models.migrate_slot_history(conn)
    models.migrate_slot_dismissals(conn)
    models.migrate_game_signals(conn)
    models.migrate_game_traits(conn)
    models.migrate_collection_name(conn)
    models.migrate_series_source(conn)
    models.migrate_igdb_review(conn)
    models.migrate_igdb_review_reason(conn)
    models.migrate_psn_addons_synced_at(conn)
    models.migrate_barcode_registry(conn)
    models.seed_default_slots(conn)
    conn.close()
    return db_path


@pytest.fixture
def client(temp_db):
    """Flask test client backed by the temp DB."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()
