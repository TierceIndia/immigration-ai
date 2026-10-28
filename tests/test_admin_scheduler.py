"""Tests for GET/PUT /api/v1/admin/scheduler (Part 1b).

The schedule is persisted (scheduler_settings, a single row) so a change
survives a restart, and applied live to the running APScheduler job via
reschedule_job/pause_job/resume_job when one is actually running.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
from db.connection import get_session_factory
from api.main import app
import scheduler as scheduler_mod


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def client(engine):
    app.state.db_engine = engine
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "scheduler-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(client.app.state.db_engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "scheduler-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "scheduler-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def non_admin_headers(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "scheduler-plain@test.com", "password": "pass123"},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "scheduler-plain@test.com", "password": "pass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestGetSchedulerSettings:
    def test_admin_gets_defaults_on_first_access(self, client, admin_headers):
        resp = client.get("/api/v1/admin/scheduler", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["mode"] == "daily"
        assert body["hour_utc"] == 3
        assert body["minute_utc"] == 0
        assert body["interval_minutes"] is None
        assert body["next_run_at"] is not None

    def test_non_admin_gets_403(self, client, non_admin_headers):
        resp = client.get("/api/v1/admin/scheduler", headers=non_admin_headers)
        assert resp.status_code == 403

    def test_no_token_gets_401(self, client):
        resp = client.get("/api/v1/admin/scheduler")
        assert resp.status_code == 401


class TestUpdateSchedulerSettingsValidation:
    @pytest.mark.parametrize("hour", [-1, 24, 100])
    def test_bad_hour_returns_422(self, client, admin_headers, hour):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "daily", "hour_utc": hour, "minute_utc": 0},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("minute", [-1, 60, 999])
    def test_bad_minute_returns_422(self, client, admin_headers, minute):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "daily", "hour_utc": 3, "minute_utc": minute},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("interval", [0, 1, 14])
    def test_interval_below_15_returns_422(self, client, admin_headers, interval):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "interval", "interval_minutes": interval},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_bad_mode_returns_422(self, client, admin_headers):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "weekly", "hour_utc": 3, "minute_utc": 0},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_daily_mode_without_hour_minute_returns_422(self, client, admin_headers):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "daily"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_interval_mode_without_interval_minutes_returns_422(self, client, admin_headers):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "interval"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_non_admin_gets_403(self, client, non_admin_headers):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "daily", "hour_utc": 5, "minute_utc": 30},
            headers=non_admin_headers,
        )
        assert resp.status_code == 403


class TestUpdateSchedulerSettingsPersistsAndApplies:
    def test_daily_change_persists_across_requests(self, client, admin_headers):
        put_resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "daily", "hour_utc": 14, "minute_utc": 45},
            headers=admin_headers,
        )
        assert put_resp.status_code == 200
        body = put_resp.json()
        assert body["mode"] == "daily"
        assert body["hour_utc"] == 14
        assert body["minute_utc"] == 45
        assert body["interval_minutes"] is None

        get_resp = client.get("/api/v1/admin/scheduler", headers=admin_headers)
        assert get_resp.json() == body

    def test_switching_to_interval_mode_clears_daily_fields(self, client, admin_headers):
        client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "daily", "hour_utc": 3, "minute_utc": 0},
            headers=admin_headers,
        )
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": True, "mode": "interval", "interval_minutes": 30},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "interval"
        assert body["interval_minutes"] == 30
        assert body["hour_utc"] is None
        assert body["minute_utc"] is None

    def test_disabled_schedule_has_no_next_run_at(self, client, admin_headers):
        resp = client.put(
            "/api/v1/admin/scheduler",
            json={"enabled": False, "mode": "daily", "hour_utc": 3, "minute_utc": 0},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["next_run_at"] is None

    def test_update_reschedules_the_live_job_when_scheduler_is_running(
        self, client, admin_headers, engine,
    ):
        """Start a real scheduler for this test only, then confirm PUT actually
        moves the live APScheduler job's trigger, not just the DB row."""
        scheduler_mod.start_scheduler(engine, hour_utc=3, minute_utc=0)
        try:
            resp = client.put(
                "/api/v1/admin/scheduler",
                json={"enabled": True, "mode": "daily", "hour_utc": 9, "minute_utc": 15},
                headers=admin_headers,
            )
            assert resp.status_code == 200
            job = scheduler_mod.get_scheduler().get_job("government_source_sync")
            assert job is not None
            from datetime import datetime, timezone
            next_fire = job.trigger.get_next_fire_time(None, datetime.now(timezone.utc))
            assert (next_fire.hour, next_fire.minute) == (9, 15)
        finally:
            scheduler_mod.shutdown_scheduler()
