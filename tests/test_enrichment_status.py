"""background_tasks enrichment launcher + status + drip gate."""
import time

import background_tasks
import enrichment
import models


def test_should_run_today_gate():
    assert background_tasks.should_run_today(None, "2026-06-23") is True
    assert background_tasks.should_run_today("2026-06-22", "2026-06-23") is True
    assert background_tasks.should_run_today("2026-06-23", "2026-06-23") is False


def test_status_idle_before_any_run():
    # Fresh task id never created -> idle
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)
    assert background_tasks.get_enrichment_status() == {"status": "idle"}


def test_run_enrichment_background_runs_and_reports(temp_db, monkeypatch):
    # Deterministic batch: no network. Patch run_batch to a quick canned result.
    def fake_batch(conn, *, budget, search_fn=None, remaining_fn=None, progress=None):
        if progress:
            progress(1, 1, 1, 0, 0)
        return {"found": 1, "queued": 0, "no_match": 0, "calls_used": 1, "total": 1}
    monkeypatch.setattr(enrichment, "run_batch", fake_batch)
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)

    ok, msg = background_tasks.run_enrichment_background(90, db_factory=models.get_db)
    assert ok is True
    # Wait briefly for the daemon thread to finish.
    for _ in range(50):
        st = background_tasks.get_enrichment_status()
        if st["status"] in ("complete", "error"):
            break
        time.sleep(0.02)
    st = background_tasks.get_enrichment_status()
    assert st["status"] == "complete"
    assert st["found"] == 1


def test_enrichment_conn_closed_even_on_error(temp_db, monkeypatch):
    """A failure inside the batch must still close the DB connection (no leak)
    and mark the task errored."""
    class _ConnSpy:
        def __init__(self, real):
            self._real = real
            self.closed = False

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            self.closed = True
            self._real.close()

    spies = []

    def factory():  # built inside the daemon thread (sqlite is thread-bound)
        spy = _ConnSpy(models.get_db())
        spies.append(spy)
        return spy

    def boom(*a, **k):
        raise RuntimeError("batch exploded")
    monkeypatch.setattr(enrichment, "run_batch", boom)
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)

    ok, _ = background_tasks.run_enrichment_background(90, db_factory=factory)
    assert ok is True
    for _ in range(50):
        if background_tasks.get_enrichment_status()["status"] in ("complete", "error"):
            break
        time.sleep(0.02)
    st = background_tasks.get_enrichment_status()
    assert st["status"] == "error"
    assert spies and spies[0].closed is True, "connection leaked on the error path"


def test_double_start_guarded(monkeypatch):
    background_tasks.task_manager._tasks.pop(background_tasks.ENRICH_TASK_ID, None)
    task = background_tasks.task_manager.create_task(background_tasks.ENRICH_TASK_ID)
    task.status = "running"
    ok, msg = background_tasks.run_enrichment_background(90, db_factory=models.get_db)
    assert ok is False and "progress" in msg.lower()
