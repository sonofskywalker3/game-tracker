"""SP-C background runner + /api/traits/ai endpoints."""
import time

import pytest

import app as app_module
import background_tasks
import models
import traits_ai


@pytest.fixture(autouse=True)
def _fresh_task():
    background_tasks.task_manager._tasks.pop(background_tasks.TRAITS_AI_TASK_ID, None)
    yield
    background_tasks.task_manager._tasks.pop(background_tasks.TRAITS_AI_TASK_ID, None)


def _add_unclassified(title="Mystery Game"):
    conn = models.get_db()
    conn.execute("INSERT INTO games (title, normalized_title) VALUES (?, ?)",
                 (title, models.normalize_title(models.clean_title(title))))
    conn.commit()
    conn.close()


def _wait_done():
    for _ in range(50):
        st = background_tasks.get_traits_ai_status()
        if st["status"] in ("complete", "error"):
            return st
        time.sleep(0.02)
    return background_tasks.get_traits_ai_status()


# --- background runner ---------------------------------------------------------

def test_runner_completes_and_reports(temp_db, monkeypatch):
    _add_unclassified()

    def fake_classify(conn, *, client=None, model=None, progress=None):
        if progress:
            progress(1, 1, 1)
        return {"total": 1, "classified": 1, "unknown": 0}
    monkeypatch.setattr(traits_ai, "classify_unclassified", fake_classify)

    ok, _ = background_tasks.run_traits_ai_background(db_factory=models.get_db)
    assert ok is True
    st = _wait_done()
    assert st["status"] == "complete"
    assert st["found"] == 1
    assert st["total"] == 1


def test_runner_marks_error_on_failure(temp_db, monkeypatch):
    _add_unclassified()

    def boom(*a, **k):
        raise RuntimeError("api exploded")
    monkeypatch.setattr(traits_ai, "classify_unclassified", boom)
    ok, _ = background_tasks.run_traits_ai_background(db_factory=models.get_db)
    assert ok is True
    st = _wait_done()
    assert st["status"] == "error"


def test_runner_double_start_guarded(temp_db):
    task = background_tasks.task_manager.create_task(background_tasks.TRAITS_AI_TASK_ID)
    task.status = "running"
    ok, msg = background_tasks.run_traits_ai_background(db_factory=models.get_db)
    assert ok is False and "progress" in msg.lower()


# --- API endpoints --------------------------------------------------------------

def test_status_endpoint_reports_count_and_key(client, monkeypatch):
    _add_unclassified()
    monkeypatch.setattr(app_module, "get_anthropic_config",
                        lambda: ("sk-test", "some-model"), raising=False)
    res = client.get('/api/traits/ai/status')
    assert res.status_code == 200
    data = res.get_json()
    assert data["unclassified"] == 1
    assert data["has_api_key"] is True
    assert data["status"] == "idle"


def test_status_endpoint_without_key(client, monkeypatch):
    monkeypatch.setattr(app_module, "get_anthropic_config",
                        lambda: (None, "some-model"), raising=False)
    data = client.get('/api/traits/ai/status').get_json()
    assert data["has_api_key"] is False
    assert data["unclassified"] == 0


def test_run_endpoint_requires_key(client, monkeypatch):
    _add_unclassified()
    monkeypatch.setattr(app_module, "get_anthropic_config",
                        lambda: (None, "some-model"), raising=False)
    res = client.post('/api/traits/ai/run')
    assert res.status_code == 400


def test_run_endpoint_starts_classification(client, monkeypatch):
    _add_unclassified()
    monkeypatch.setattr(app_module, "get_anthropic_config",
                        lambda: ("sk-test", "some-model"), raising=False)

    def fake_classify(conn, *, client=None, model=None, progress=None):
        return {"total": 1, "classified": 1, "unknown": 0}
    monkeypatch.setattr(traits_ai, "classify_unclassified", fake_classify)
    res = client.post('/api/traits/ai/run')
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    st = _wait_done()
    assert st["status"] == "complete"
