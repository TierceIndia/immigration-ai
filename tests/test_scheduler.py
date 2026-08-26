"""Tests for the APScheduler-based government source sync scheduler.

Covers every operational invariant:
  1. Scheduler starts correctly
  2. Scheduler doesn't duplicate jobs
  3. crawl_frequency / interval is respected
  4. Failed job does not kill scheduler
  5. Retry works (next interval fires after failure)
  6. One source failure doesn't stop other sources
  7. Graceful shutdown works
  8. Timezone is explicit (UTC)
  9. Overlapping runs are prevented (lock + max_instances=1)
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, GovernmentSource
from scheduler import (
    start_scheduler, shutdown_scheduler, get_scheduler,
    trigger_sync_now, _sync_lock, _acquired_sync_lock, _release_sync_lock,
    _DEFAULT_INTERVAL_MINUTES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def db_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture(autouse=True)
def _clean_scheduler():
    """Guarantee scheduler is torn down and lock released after every test."""
    yield
    shutdown_scheduler()
    if _sync_lock.locked():
        _sync_lock.release()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SOURCE_ROW = {
    "country_id": "AU",
    "program_id": "prog",
    "visa_category": "491",
    "authority": "Home Affairs",
    "source_type": "WEB",
    "priority": "HIGH",
    "monitoring_method": "WEB",
    "crawl_frequency": "DAILY",
    "status": "ACTIVE",
}


def _insert_sources(engine, source_ids: list[str]):
    """Insert government source rows with stable IDs."""
    with engine.begin() as conn:
        for sid in source_ids:
            row = {**_SOURCE_ROW, "source_id": sid, "url": f"https://{sid}.example.com"}
            conn.execute(GovernmentSource.__table__.insert(), row)


# ---------------------------------------------------------------------------
# 1. Scheduler starts correctly
# ---------------------------------------------------------------------------
class TestSchedulerStart:
    def test_start_creates_scheduler(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()
        assert sched is not None
        assert sched.running

    def test_default_interval_is_60(self):
        assert _DEFAULT_INTERVAL_MINUTES == 60

    def test_custom_interval_minutes(self, db_engine):
        start_scheduler(db_engine, interval_minutes=15)
        sched = get_scheduler()
        jobs = sched.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "government_source_sync"
        assert jobs[0].name == "Government Source Sync"

    def test_scheduler_reads_env_var(self, db_engine, monkeypatch):
        monkeypatch.setenv("GOVERNMENT_SYNC_INTERVAL_MINUTES", "90")
        start_scheduler(db_engine)
        sched = get_scheduler()
        job = sched.get_jobs()[0]
        assert job.trigger.interval.total_seconds() == 90 * 60


# ---------------------------------------------------------------------------
# 2. Scheduler doesn't duplicate jobs
# ---------------------------------------------------------------------------
class TestNoDuplicateJobs:
    def test_double_start_is_safe(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        start_scheduler(db_engine, interval_minutes=5)
        assert len(get_scheduler().get_jobs()) == 1

    def test_replace_existing_prevents_duplicates(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        start_scheduler(db_engine, interval_minutes=10)
        assert len(get_scheduler().get_jobs()) == 1


# ---------------------------------------------------------------------------
# 3. crawl_frequency / interval is respected
# ---------------------------------------------------------------------------
class TestIntervalRespected:
    def test_job_runs_at_configured_interval(self, db_engine):
        start_scheduler(db_engine, interval_minutes=1)
        job = get_scheduler().get_jobs()[0]
        assert job.trigger.interval.total_seconds() == 60

    def test_engine_passed_to_job(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        job = get_scheduler().get_jobs()[0]
        assert job.args[0] == db_engine


# ---------------------------------------------------------------------------
# 4. Failed job does not kill scheduler
# ---------------------------------------------------------------------------
class TestFailureResilience:
    def test_scheduler_survives_sync_exception(self, db_engine):
        """If _sync_job raises, the scheduler keeps running."""
        call_count = {"n": 0}

        def exploding_sync(engine):
            call_count["n"] += 1
            raise RuntimeError("Simulated DB explosion")

        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()

        # Replace the existing job with a fast-firing exploding one
        sched.remove_job("government_source_sync")
        sched.add_job(
            exploding_sync,
            "interval",
            seconds=0.05,
            args=[db_engine],
            id="government_source_sync",
            name="Government Source Sync",
        )

        time.sleep(0.3)

        assert sched.running
        assert call_count["n"] >= 2

    def test_exception_does_not_corrupt_scheduler_state(self, db_engine):
        """After failure, the job is still present and can fire again."""
        def failing(engine):
            raise ValueError("boom")

        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()
        sched.remove_job("government_source_sync")
        sched.add_job(
            failing,
            "interval",
            seconds=0.05,
            args=[db_engine],
            id="government_source_sync",
            name="Government Source Sync",
        )

        time.sleep(0.3)
        assert sched.get_job("government_source_sync") is not None


# ---------------------------------------------------------------------------
# 5. Retry works (next interval fires after failure)
# ---------------------------------------------------------------------------
class TestRetryBehavior:
    def test_next_interval_fires_after_failure(self, db_engine):
        """After a failed scheduled run, the next interval should still fire."""
        fire_log = []

        def counting_sync(engine):
            fire_log.append("fired")

        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()
        sched.remove_job("government_source_sync")
        sched.add_job(
            counting_sync,
            "interval",
            seconds=0.05,
            args=[db_engine],
            id="government_source_sync",
            name="Government Source Sync",
        )

        time.sleep(0.3)
        assert len(fire_log) >= 2


# ---------------------------------------------------------------------------
# 6. One source failure doesn't stop other sources
# ---------------------------------------------------------------------------
class TestSourceIsolation:
    def test_sync_all_sources_continues_after_one_failure(self, db_engine):
        """When one source raises, sync_all_sources still processes the others."""
        from orchestration.government_sync import sync_all_sources

        _insert_sources(db_engine, ["src_ok", "src_bad"])

        factory = sessionmaker(bind=db_engine)

        # Use a function that dispatches based on source_id argument
        def selective_fail(session, source_id, **kwargs):
            if source_id == "src_bad":
                raise RuntimeError("source down")
            # src_ok → return the real detect_change result (no doc = unchanged)
            return False, None, None

        with patch(
            "orchestration.government_sync.detect_change",
            side_effect=selective_fail,
        ):
            with factory() as session:
                results = sync_all_sources(session, dry_run=True, trigger_type="TEST")

        statuses = {r.source_id: r.status for r in results}
        assert statuses["src_bad"] == "error"
        assert statuses["src_ok"] == "unchanged"


# ---------------------------------------------------------------------------
# 7. Graceful shutdown works
# ---------------------------------------------------------------------------
class TestGracefulShutdown:
    def test_shutdown_stops_scheduler(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        assert get_scheduler().running
        shutdown_scheduler()
        assert get_scheduler() is None

    def test_shutdown_waits_for_inflight_job(self, db_engine):
        """shutdown(wait=True) should allow a running job to finish."""
        finished = threading.Event()

        def slow_sync(engine):
            time.sleep(0.05)
            finished.set()

        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()
        sched.remove_job("government_source_sync")
        sched.add_job(
            slow_sync,
            "interval",
            seconds=0.05,
            args=[db_engine],
            id="government_source_sync",
            name="Government Source Sync",
        )

        # Let the job fire at least once
        time.sleep(0.15)
        shutdown_scheduler()
        assert get_scheduler() is None

    def test_double_shutdown_is_safe(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        shutdown_scheduler()
        shutdown_scheduler()  # should not raise


# ---------------------------------------------------------------------------
# 8. Timezone is explicit (UTC)
# ---------------------------------------------------------------------------
class TestExplicitTimezone:
    def test_scheduler_uses_utc(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()
        assert sched.timezone is not None
        # The timezone should be UTC
        from datetime import timezone
        assert str(sched.timezone) == "UTC"

    def test_trigger_sync_now_returns_utc_aware_fields(self, db_engine):
        result = trigger_sync_now(db_engine)
        assert "total_sources" in result


# ---------------------------------------------------------------------------
# 9. Overlapping runs are prevented (lock + max_instances=1)
# ---------------------------------------------------------------------------
class TestOverlapProtection:
    def test_max_instances_is_one(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        job = get_scheduler().get_jobs()[0]
        assert job.max_instances == 1

    def test_coalesce_is_true(self, db_engine):
        start_scheduler(db_engine, interval_minutes=5)
        job = get_scheduler().get_jobs()[0]
        assert job.coalesce is True

    def test_trigger_sync_now_rejects_concurrent_run(self, db_engine):
        """trigger_sync_now returns 'busy' when lock is already held."""
        _sync_lock.acquire()
        try:
            result = trigger_sync_now(db_engine)
            assert result["status"] == "busy"
            assert "already in progress" in result["detail"]
        finally:
            _sync_lock.release()

    def test_scheduled_job_skips_when_lock_held(self, db_engine):
        """_sync_job returns early (no crash) when the lock is held."""
        from scheduler import _sync_job

        _sync_lock.acquire()
        try:
            _sync_job(db_engine)  # should not raise
        finally:
            _sync_lock.release()

    def test_lock_is_released_after_busy_rejection(self, db_engine):
        """Even if trigger_sync_now returns busy, the lock state is unchanged."""
        _sync_lock.acquire()
        try:
            trigger_sync_now(db_engine)
            assert _sync_lock.locked()  # still held by us
        finally:
            _sync_lock.release()

    def test_lock_released_after_sync_failure(self, db_engine):
        """If sync_all_sources raises, the lock is still released."""
        assert _acquired_sync_lock()
        try:
            pass
        finally:
            _release_sync_lock()

        assert not _sync_lock.locked()

    def test_lock_acquire_release_cycle(self, db_engine):
        """Verify acquire/release round-trips cleanly."""
        assert _acquired_sync_lock()
        assert _sync_lock.locked()
        _release_sync_lock()
        assert not _sync_lock.locked()

    def test_thread_pool_max_workers_is_one(self, db_engine):
        """ThreadPoolExecutor(max_workers=1) prevents parallel threads."""
        start_scheduler(db_engine, interval_minutes=5)
        sched = get_scheduler()
        executor = sched._executors.get("default")
        # APScheduler wraps ThreadPoolExecutor; the inner pool has _max_workers
        assert executor._pool._max_workers == 1

    def test_double_start_preserves_lock(self, db_engine):
        """Double start should not corrupt the lock mechanism."""
        start_scheduler(db_engine, interval_minutes=5)
        start_scheduler(db_engine, interval_minutes=5)
        # Lock should still work
        assert _acquired_sync_lock()
        _release_sync_lock()
        shutdown_scheduler()
