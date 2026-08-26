"""Scheduler — automatic government source sync via APScheduler.

Runs periodic jobs to:
1. Sync all active government sources (change detection + rule compilation)
2. Configurable interval via GOVERNMENT_SYNC_INTERVAL_MINUTES env var

Overlap protection:
  A module-level threading.Lock (_sync_lock) prevents concurrent sync executions
  from both the scheduled job and manual triggers.  APScheduler's max_instances=1
  further ensures the scheduler never starts a second scheduled run while one is
  in flight, but _sync_now bypasses APScheduler entirely so the lock is the
  single source of truth.

Usage in FastAPI lifespan:
    from scheduler import start_scheduler, shutdown_scheduler
    start_scheduler(db_engine)
    yield
    shutdown_scheduler()
"""
from __future__ import annotations

import os
import threading
from typing import Any

from datetime import timezone as _tz

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

from logging_config import get_logger

logger = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None
_sync_lock = threading.Lock()

_DEFAULT_INTERVAL_MINUTES = 60


def _acquired_sync_lock() -> bool:
    """Try to acquire the sync lock.  Returns True if acquired."""
    return _sync_lock.acquire(blocking=False)


def _release_sync_lock() -> None:
    _sync_lock.release()


def _sync_job(engine) -> None:
    """Job function that runs the sync pipeline.

    Creates a new session per invocation for thread safety.
    Protected by _sync_lock to prevent overlap with other sync executions.
    """
    from db.connection import get_session
    from orchestration.government_sync import sync_all_sources

    if not _acquired_sync_lock():
        logger.warning("Scheduler: sync skipped — another sync is already running")
        return

    try:
        logger.info("Scheduler: starting government source sync")
        with get_session(engine) as session:
            results = sync_all_sources(session, dry_run=False, trigger_type="SCHEDULED")
            changed = sum(1 for r in results if r.status == "changed")
            errors = sum(1 for r in results if r.status == "error")
            unchanged = sum(1 for r in results if r.status == "unchanged")
            logger.info(
                "Scheduler: sync completed",
                extra={
                    "total_sources": len(results),
                    "changed": changed,
                    "unchanged": unchanged,
                    "errors": errors,
                },
            )
    except Exception as e:
        logger.error("Scheduler: sync job failed", extra={"error": str(e)})
    finally:
        _release_sync_lock()


def start_scheduler(engine, interval_minutes: int | None = None) -> None:
    """Start the background scheduler.

    Args:
        engine: SQLAlchemy engine for DB sessions.
        interval_minutes: Override from env or default (60 min).
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

    if interval_minutes is None:
        interval_minutes = int(
            os.getenv("GOVERNMENT_SYNC_INTERVAL_MINUTES", str(_DEFAULT_INTERVAL_MINUTES))
        )

    jobstores = {"default": MemoryJobStore()}
    executors = {"default": ThreadPoolExecutor(max_workers=1)}
    job_defaults = {"coalesce": True, "max_instances": 1}

    _scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone=_tz.utc,
    )

    _scheduler.add_job(
        _sync_job,
        "interval",
        minutes=interval_minutes,
        args=[engine],
        id="government_source_sync",
        name="Government Source Sync",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started",
        extra={"interval_minutes": interval_minutes, "timezone": "UTC"},
    )


def shutdown_scheduler() -> None:
    """Shut down the scheduler gracefully.

    Waits for in-flight jobs to finish so PipelineRun records are completed
    rather than left dangling.
    """
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Scheduler shut down")


def get_scheduler() -> BackgroundScheduler | None:
    """Get the current scheduler instance (for testing/introspection)."""
    return _scheduler


def trigger_sync_now(engine) -> dict[str, Any]:
    """Manually trigger an immediate sync (for admin endpoints / testing).

    Protected by the same _sync_lock as the scheduled job so two sync
    executions can never run concurrently.  Returns a summary dict.
    If a sync is already in progress, returns status='busy'.
    """
    from db.connection import get_session
    from orchestration.government_sync import sync_all_sources

    if not _acquired_sync_lock():
        logger.warning("Manual sync rejected — another sync is already running")
        return {"status": "busy", "detail": "A sync is already in progress"}

    try:
        logger.info("Manual sync triggered")
        with get_session(engine) as session:
            results = sync_all_sources(session, dry_run=False, trigger_type="MANUAL")
            return {
                "total_sources": len(results),
                "changed": sum(1 for r in results if r.status == "changed"),
                "unchanged": sum(1 for r in results if r.status == "unchanged"),
                "errors": sum(1 for r in results if r.status == "error"),
                "results": [
                    {"source_id": r.source_id, "status": r.status}
                    for r in results
                ],
            }
    finally:
        _release_sync_lock()
