"""
Background task manager for long-running operations.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class TaskProgress:
    task_id: str
    status: str = "pending"  # pending, running, complete, error
    current: int = 0
    total: int = 0
    current_item: str = ""
    found: int = 0
    not_found: list = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    queued: int = 0
    no_match: int = 0
    last_run_date: Optional[str] = None
    remaining_eligible: int = 0


class TaskManager:
    def __init__(self):
        self._tasks: dict[str, TaskProgress] = {}
        self._lock = threading.Lock()

    def create_task(self, task_id: str) -> TaskProgress:
        with self._lock:
            task = TaskProgress(task_id=task_id)
            self._tasks[task_id] = task
            return task

    def get_task(self, task_id: str) -> Optional[TaskProgress]:
        with self._lock:
            return self._tasks.get(task_id)

    def update_task(self, task_id: str, **kwargs):
        with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                for key, value in kwargs.items():
                    if hasattr(task, key):
                        setattr(task, key, value)

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return task is not None and task.status == "running"


# Global task manager
task_manager = TaskManager()


def run_cover_fetch_background(client_id: str, client_secret: str):
    """Run cover fetch in background thread."""
    from fetch_covers import fetch_covers_generator

    task_id = "cover_fetch"

    # Check if already running
    if task_manager.is_running(task_id):
        return False, "Fetch already in progress"

    task = task_manager.create_task(task_id)
    task.status = "running"
    task.started_at = datetime.now()

    def do_fetch():
        try:
            for progress in fetch_covers_generator(client_id, client_secret):
                if 'error' in progress:
                    task_manager.update_task(task_id,
                        status="error",
                        error=progress['error'],
                        completed_at=datetime.now()
                    )
                    return

                task_manager.update_task(task_id,
                    current=progress.get('current', 0),
                    total=progress.get('total', 0),
                    current_item=progress.get('title', ''),
                    found=progress.get('found', 0),
                    not_found=progress.get('not_found', [])
                )

                if progress.get('status') == 'complete':
                    task_manager.update_task(task_id,
                        status="complete",
                        completed_at=datetime.now()
                    )
                    return

        except Exception as e:
            task_manager.update_task(task_id,
                status="error",
                error=str(e),
                completed_at=datetime.now()
            )

    thread = threading.Thread(target=do_fetch, daemon=True)
    thread.start()

    return True, "Fetch started"


def get_cover_fetch_status() -> dict:
    """Get current status of cover fetch task."""
    task = task_manager.get_task("cover_fetch")
    if not task:
        return {"status": "idle"}

    return {
        "status": task.status,
        "current": task.current,
        "total": task.total,
        "current_item": task.current_item,
        "found": task.found,
        "not_found": task.not_found,
        "error": task.error,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


ENRICH_TASK_ID = "upc_enrichment"
DRIP_SLEEP_SECONDS = 3 * 60 * 60   # re-check the daily gate every 3 hours


def should_run_today(last_run_date: str | None, today: str) -> bool:
    """The drip runs at most one batch per UTC calendar day."""
    return last_run_date != today


def get_enrichment_status() -> dict:
    """Current enrichment task status (mirrors get_cover_fetch_status)."""
    task = task_manager.get_task(ENRICH_TASK_ID)
    if not task:
        return {"status": "idle"}
    return {
        "status": task.status,
        "current": task.current,
        "total": task.total,
        "found": task.found,
        "queued": task.queued,
        "no_match": task.no_match,
        "error": task.error,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def run_enrichment_background(budget: int, *, db_factory=None) -> tuple[bool, str]:
    """Start one enrichment batch in a daemon thread. Mirrors cover-fetch."""
    import enrichment
    import models
    db_factory = db_factory or models.get_db

    if task_manager.is_running(ENRICH_TASK_ID):
        return False, "Enrichment already in progress"
    if budget <= 0:
        return False, "Daily quota exhausted"

    task = task_manager.create_task(ENRICH_TASK_ID)
    task.status = "running"
    task.started_at = datetime.now()

    def do_run():
        try:
            conn = db_factory()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            state = enrichment.get_enrichment_state(conn)
            already = state["last_run_count"] if state["last_run_date"] == today else 0

            def progress(done, total, found, queued, no_match):
                task_manager.update_task(ENRICH_TASK_ID, current=done, total=total,
                                         found=found, queued=queued, no_match=no_match)

            result = enrichment.run_batch(conn, budget=budget, progress=progress)
            enrichment.set_enrichment_state(
                conn, last_run_date=today, last_run_count=already + result["calls_used"])
            task_manager.update_task(
                ENRICH_TASK_ID, status="complete", completed_at=datetime.now(),
                found=result["found"], queued=result["queued"], no_match=result["no_match"],
                current=result["calls_used"], total=result["total"])
            conn.close()
        except Exception as exc:  # daemon must never crash the app; logged + isolated
            log.exception("UPC enrichment batch failed")
            task_manager.update_task(ENRICH_TASK_ID, status="error", error=str(exc),
                                     completed_at=datetime.now())

    threading.Thread(target=do_run, daemon=True).start()
    return True, "Enrichment started"


def start_enrichment_drip(*, db_factory=None, sleep_fn=time.sleep) -> threading.Thread:
    """Daemon: at most one batch per UTC day, re-checking every few hours."""
    import enrichment
    import models
    db_factory = db_factory or models.get_db

    def loop():
        while True:
            try:
                conn = db_factory()
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                state = enrichment.get_enrichment_state(conn)
                conn.close()
                if should_run_today(state["last_run_date"], today):
                    from enrichment import UPC_ENRICH_DAILY_BUDGET
                    run_enrichment_background(UPC_ENRICH_DAILY_BUDGET, db_factory=db_factory)
            except Exception:
                log.exception("UPC enrichment drip tick failed")
            sleep_fn(DRIP_SLEEP_SECONDS)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
