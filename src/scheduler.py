"""Scheduler — automatic government source sync via APScheduler.

Runs periodic jobs to sync all active government sources (change detection +
rule compilation).

Trigger selection (single source of truth):
  * ``GOVERNMENT_SYNC_HOUR_UTC`` set       -> daily cron at that hour
    (``GOVERNMENT_SYNC_MINUTE_UTC`` optional, default 0). This is the production
    mode: one fixed daily run, aligned to the clock, the whole 24h cycle covered
    by a predictable schedule.
  * ``GOVERNMENT_SYNC_INTERVAL_MINUTES`` set -> interval every N minutes (legacy
    mode, kept for the standalone pipeline scripts / tests).
  * Neither set -> daily cron at the default fixed time
    (03:00 UTC / 00 minute).

Overlap protection:
  A module-level threading.Lock (_sync_lock) prevents concurrent sync executions
  from both the scheduled job and manual triggers.  APScheduler's max_instances=1
  further ensures the scheduler never starts a second scheduled run while one is
  in flight, but trigger_sync_now bypasses APScheduler entirely so the lock is the
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
import uuid
from typing import Any

from datetime import datetime, timezone as _tz

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from logging_config import get_logger

logger = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None
_sync_lock = threading.Lock()

_DEFAULT_INTERVAL_MINUTES = 60
_DEFAULT_DAILY_HOUR_UTC = 3
_DEFAULT_DAILY_MINUTE_UTC = 0


def _parse_int_env(name: str) -> int | None:
    """Parse a non-negative int env var; warn + return None on garbage."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"Invalid integer for {name}={raw!r}, ignoring")
        return None


def _build_trigger(
    interval_minutes: int | None,
    hour_utc: int | None,
    minute_utc: int | None,
) -> tuple[Any, str]:
    """Return (trigger, mode_label) honoring the selection rules above."""
    interval_env = _parse_int_env("GOVERNMENT_SYNC_INTERVAL_MINUTES")
    hour_env = _parse_int_env("GOVERNMENT_SYNC_HOUR_UTC")
    minute_env = _parse_int_env("GOVERNMENT_SYNC_MINUTE_UTC")

    daily = False
    if interval_minutes is None and hour_utc is None:
        if hour_env is not None:
            hour_utc, minute_utc = hour_env, minute_env
            daily = True
        elif interval_env is not None:
            interval_minutes = interval_env
        else:
            hour_utc, minute_utc = (
                _DEFAULT_DAILY_HOUR_UTC,
                _DEFAULT_DAILY_MINUTE_UTC,
            )
            daily = True
    elif interval_minutes is None and hour_utc is not None:
        daily = True

    if daily:
        hour = hour_utc if hour_utc is not None else _DEFAULT_DAILY_HOUR_UTC
        minute = minute_utc if minute_utc is not None else _DEFAULT_DAILY_MINUTE_UTC
        if not 0 <= hour <= 23:
            raise ValueError(f"GOVERNMENT_SYNC_HOUR_UTC must be 0-23, got {hour}")
        if not 0 <= minute <= 59:
            raise ValueError(f"GOVERNMENT_SYNC_MINUTE_UTC must be 0-59, got {minute}")
        return (
            CronTrigger(hour=hour, minute=minute, timezone=_tz.utc),
            f"daily cron at {hour:02d}:{minute:02d} UTC",
        )

    minutes = interval_minutes if interval_minutes is not None else _DEFAULT_INTERVAL_MINUTES
    if minutes < 1:
        raise ValueError(f"Interval must be >= 1 minute, got {minutes}")
    return (
        IntervalTrigger(minutes=minutes, timezone=_tz.utc),
        f"interval every {minutes} minutes",
    )


def _acquired_sync_lock() -> bool:
    """Try to acquire the sync lock.  Returns True if acquired."""
    return _sync_lock.acquire(blocking=False)


def _release_sync_lock() -> None:
    _sync_lock.release()


# ---------------------------------------------------------------------------
# Background sync jobs (POST /api/v1/admin/sync) — in-memory, single-process.
#
# sync_all_sources() creates one PipelineRun row PER SOURCE (its own run_id),
# not one row for the whole batch — so a batch-level "how is my sync going"
# handle needs its own id, tracked here rather than in the DB. This matches
# _sync_lock's existing single-process-only scope (no Redis/shared state yet;
# see PLAN.md), so it does not survive a process restart or multiple workers.
# ---------------------------------------------------------------------------
_sync_jobs: dict[str, dict[str, Any]] = {}
_sync_jobs_lock = threading.Lock()


def _set_job(run_id: str, **fields: Any) -> None:
    with _sync_jobs_lock:
        job = _sync_jobs.setdefault(run_id, {"run_id": run_id})
        job.update(fields)


def get_sync_job(run_id: str) -> dict[str, Any] | None:
    """Look up a background sync job started via start_sync_background().

    Returns a shallow copy (never the live dict) so callers can't mutate
    internal state. Returns None if run_id is not a known background job
    (e.g. it's a per-source PipelineRun id instead, or unknown).
    """
    with _sync_jobs_lock:
        job = _sync_jobs.get(run_id)
        return dict(job) if job is not None else None


def _run_sync_job(run_id: str, engine, trigger_type: str) -> None:
    """Background-thread body: run the sync, record progress, release the lock.

    Mirrors trigger_sync_now's sync_all_sources call exactly — the only
    difference is where the result goes (the in-memory job registry instead of
    a direct function return) and that the _sync_lock release happens here,
    at the end of the actual work, not when the HTTP request returns.
    """
    from db.connection import get_session
    from orchestration.government_sync import sync_all_sources

    _set_job(run_id, status="RUNNING", started_at=datetime.now(_tz.utc).isoformat())

    summary: dict[str, Any] | None = None
    error: str | None = None
    try:
        with get_session(engine) as session:
            results = sync_all_sources(session, dry_run=False, trigger_type=trigger_type)
            summary = {
                "total_sources": len(results),
                "changed": sum(1 for r in results if r.status == "changed"),
                "unchanged": sum(1 for r in results if r.status == "unchanged"),
                "errors": sum(1 for r in results if r.status == "error"),
                "results": [
                    {"source_id": r.source_id, "status": r.status} for r in results
                ],
            }
    except Exception as e:
        error = str(e)
    finally:
        # Release the lock EXACTLY ONCE, on every path, and always BEFORE the
        # terminal status is published. Two properties matter here:
        #
        # 1. Ordering — a caller polling GET /admin/runs/{run_id} that observes
        #    COMPLETED/FAILED must never still see the lock held, or its
        #    follow-up POST /admin/sync would get a spurious 409 for a sync that
        #    has externally already finished.
        # 2. Exactly-once — the release lives in `finally` and nowhere else, so a
        #    failure while recording or logging the result (below, OUTSIDE this
        #    try) cannot fall back into a second release of a free lock, which
        #    would raise "release unlocked lock" and leave the job unfinalised.
        _release_sync_lock()

    try:
        if error is None and summary is not None:
            _set_job(
                run_id, status="COMPLETED", result=summary,
                completed_at=datetime.now(_tz.utc).isoformat(),
            )
            logger.info(
                "Background sync completed",
                extra={"run_id": run_id, "total_sources": summary["total_sources"],
                       "changed": summary["changed"], "errors": summary["errors"]},
            )
        else:
            _set_job(
                run_id, status="FAILED", error=error or "Background sync produced no result",
                completed_at=datetime.now(_tz.utc).isoformat(),
            )
            logger.error(
                "Background sync failed",
                extra={"run_id": run_id, "error": error or "Background sync produced no result"},
            )
    except Exception:
        # The lock is already free at this point, so a failure to record the
        # terminal state must not leave the process wedged — log and move on.
        logger.exception("Failed to record terminal state for background sync %s", run_id)


def _run_single_source_sync_job(run_id: str, engine, source_id: str, trigger_type: str) -> None:
    """Background-thread body for a single-source sync. Mirrors _run_sync_job
    exactly, but calls sync_source() for one source_id instead of
    sync_all_sources() for every active source."""
    from db.connection import get_session
    from orchestration.government_sync import sync_source

    _set_job(run_id, status="RUNNING", started_at=datetime.now(_tz.utc).isoformat())

    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        with get_session(engine) as session:
            sync_result = sync_source(session, source_id, dry_run=False, trigger_type=trigger_type)
            result = {"source_id": sync_result.source_id, "status": sync_result.status}
    except Exception as e:
        error = str(e)
    finally:
        # Same ordering/exactly-once guarantee as _run_sync_job: release before
        # the terminal status is published, and only here, never twice.
        _release_sync_lock()

    try:
        if error is None and result is not None:
            _set_job(
                run_id, status="COMPLETED", result=result,
                completed_at=datetime.now(_tz.utc).isoformat(),
            )
            logger.info(
                "Background single-source sync completed",
                extra={"run_id": run_id, "source_id": source_id, "status": result["status"]},
            )
        else:
            _set_job(
                run_id, status="FAILED", error=error or "Background sync produced no result",
                completed_at=datetime.now(_tz.utc).isoformat(),
            )
            logger.error(
                "Background single-source sync failed",
                extra={"run_id": run_id, "source_id": source_id,
                       "error": error or "Background sync produced no result"},
            )
    except Exception:
        logger.exception(
            "Failed to record terminal state for background single-source sync %s", run_id
        )


def start_single_source_sync_background(
    engine, source_id: str, trigger_type: str = "MANUAL"
) -> dict[str, Any]:
    """Start a sync for exactly one government source in a background thread.

    Reuses the same _sync_lock as start_sync_background/the scheduled job, so
    a per-source sync and a global "sync all" (or another per-source sync) can
    never run concurrently either - at most one sync of any kind runs at a
    time. Same immediate-rejection semantics: the lock is acquired
    synchronously before the thread is spawned, so a concurrent call is
    rejected right away, never blocks.

    Returns {"run_id": <uuid>, "status": "STARTED"} on success, or
    {"status": "busy", "detail": ...} if a sync is already running.
    Progress/result is then readable via get_sync_job(run_id) - the same
    registry and the same GET /admin/runs/{run_id} route used for the global
    sync, with zero changes needed there.
    """
    if not _acquired_sync_lock():
        logger.warning(
            "Single-source background sync rejected — another sync is already running",
            extra={"source_id": source_id},
        )
        return {"status": "busy", "detail": "A sync is already in progress"}

    run_id = str(uuid.uuid4())
    _set_job(
        run_id,
        status="STARTED", trigger_type=trigger_type, source_id=source_id,
        started_at=None, completed_at=None, result=None, error=None,
    )
    thread = threading.Thread(
        target=_run_single_source_sync_job, args=(run_id, engine, source_id, trigger_type),
        name=f"sync-job-{run_id}", daemon=True,
    )
    thread.start()
    logger.info(
        "Single-source background sync started",
        extra={"run_id": run_id, "source_id": source_id, "trigger_type": trigger_type},
    )
    return {"run_id": run_id, "status": "STARTED"}


def start_sync_background(engine, trigger_type: str = "MANUAL") -> dict[str, Any]:
    """Start a government source sync in a background thread and return immediately.

    Reuses the same _sync_lock as the scheduled job and trigger_sync_now, so at
    most one sync ever runs at a time regardless of how it was triggered. The
    lock is acquired synchronously (in the caller's thread) before the background
    thread is spawned, so a concurrent call that arrives while a sync is already
    running is rejected immediately — never blocks, never starts a second thread.

    Returns {"run_id": <uuid>, "status": "STARTED"} on success, or
    {"status": "busy", "detail": ...} if a sync is already running (the caller,
    e.g. the HTTP route, is expected to translate that into 409).

    Progress/result is then readable via get_sync_job(run_id).
    """
    if not _acquired_sync_lock():
        logger.warning("Background sync rejected — another sync is already running")
        return {"status": "busy", "detail": "A sync is already in progress"}

    run_id = str(uuid.uuid4())
    _set_job(
        run_id,
        status="STARTED", trigger_type=trigger_type,
        started_at=None, completed_at=None, result=None, error=None,
    )
    thread = threading.Thread(
        target=_run_sync_job, args=(run_id, engine, trigger_type),
        name=f"sync-job-{run_id}", daemon=True,
    )
    thread.start()
    logger.info("Background sync started", extra={"run_id": run_id, "trigger_type": trigger_type})
    return {"run_id": run_id, "status": "STARTED"}


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


def start_scheduler(
    engine,
    interval_minutes: int | None = None,
    hour_utc: int | None = None,
    minute_utc: int | None = None,
) -> None:
    """Start the background scheduler.

    Args:
        engine: SQLAlchemy engine for DB sessions.
        interval_minutes: Explicit interval mode (legacy). If None, the trigger
            is chosen from env vars (GOVERNMENT_SYNC_HOUR_UTC for daily cron,
            GOVERNMENT_SYNC_INTERVAL_MINUTES for legacy interval) or the daily
            cron default.
        hour_utc / minute_utc: Explicit daily-cron time override (UTC).
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

    trigger, mode_label = _build_trigger(interval_minutes, hour_utc, minute_utc)

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
        trigger,
        args=[engine],
        id="government_source_sync",
        name="Government Source Sync",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started", extra={"trigger": mode_label, "timezone": "UTC"})


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
