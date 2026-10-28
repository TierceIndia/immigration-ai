"""Item 6: stored_new is surfaced on GET /api/v1/admin/runs/{run_id} -
the only pipeline-run-reading endpoint the frontend currently polls (see
pipeline_runs.py's own module docstring).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base, Country, ImmigrationProgram, GovernmentSource, PipelineRun
from db.connection import get_session_factory
from api.main import app


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
def admin_headers(client, engine):
    client.post(
        "/api/v1/auth/register",
        json={"email": "stored-new-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "stored-new-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "stored-new-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_get_run_surfaces_stored_new_alongside_requirements_extracted(client, admin_headers, engine):
    with get_session_factory(engine)() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        s.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        s.merge(GovernmentSource(
            source_id="SRC-AU-STORED-NEW", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov.au/stored-new", source_type="HTML",
            priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE",
        ))
        run = PipelineRun(
            source_id="SRC-AU-STORED-NEW", trigger_type="MANUAL", status="COMPLETED",
            started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
            requirements_extracted=5, stored_new=2,
        )
        s.add(run)
        s.commit()
        run_id = run.run_id

    resp = client.get(f"/api/v1/admin/runs/{run_id}", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["requirements_extracted"] == 5
    assert body["stored_new"] == 2
