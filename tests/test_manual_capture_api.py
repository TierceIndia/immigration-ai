"""API tests for the manual-capture safety net endpoint.

POST /api/v1/admin/sources/{source_id}/manual-capture lets an admin resolve a source
stuck in NEEDS_MANUAL_CAPTURE by ingesting raw content captured by hand. Covers
admin gating (401/403), a happy path (creates document+version, resets source to
ACTIVE, tags retrieval_method=MANUAL_CAPTURE), and a 404 path.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import (
    ApiUser, Base, Country, DocumentVersion, GovernmentDocument,
    GovernmentSource, ImmigrationProgram,
)
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
def session(engine):
    factory = get_session_factory(engine)
    s = factory()
    yield s
    s.close()


@pytest.fixture()
def client(engine):
    app.state.db_engine = engine
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client, engine):
    client.post(
        "/api/v1/auth/register",
        json={"email": "mc-admin@test.com", "password": "admin123", "full_name": "Admin"},
    )
    with get_session_factory(engine)() as s:
        user = s.query(ApiUser).filter(ApiUser.email == "mc-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "mc-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def seed_lookup_rows(engine):
    with get_session_factory(engine)() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        s.merge(ImmigrationProgram(
            program_id="WORK_482", country_id="AU", visa_category="WORK",
            subclass_code="482", name="Temporary Skill Shortage", status="ACTIVE",
        ))
        s.commit()


def _seed_blocked_source(session, status="NEEDS_MANUAL_CAPTURE"):
    src = GovernmentSource(
        source_id="SRC-AU-WORK-482",
        country_id="AU",
        program_id="WORK_482",
        visa_category="WORK",
        authority="Department of Home Affairs",
        url="https://immi.homeaffairs.gov.au/visas/work/482",
        source_type="HTML",
        source_subtype="ELIGIBILITY_GUIDANCE",
        priority="PRIMARY",
        monitoring_method="WEB",
        crawl_frequency="DAILY",
        status=status,
        fetch_status="ACCESS_BLOCKED",
        http_status=403,
        last_error="HTTP 403 even via genuine browser session",
        consecutive_failures=3,
    )
    session.add(src)
    session.commit()
    return src


def test_manual_capture_requires_auth(client, seed_lookup_rows):
    resp = client.post(
        "/api/v1/admin/sources/SRC-AU-WORK-482/manual-capture",
        json={"raw_html": "<html>visa info</html>"},
    )
    assert resp.status_code == 401


def test_manual_capture_requires_admin(client, engine, seed_lookup_rows):
    client.post(
        "/api/v1/auth/register",
        json={"email": "mc-user@test.com", "password": "user123", "full_name": "User"},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "mc-user@test.com", "password": "user123"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    with get_session_factory(engine)() as s:
        _seed_blocked_source(s)
    resp = client.post(
        "/api/v1/admin/sources/SRC-AU-WORK-482/manual-capture",
        json={"raw_html": "<html>visa info</html>"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_manual_capture_resolves_blocked_source(client, engine, admin_headers, seed_lookup_rows):
    with get_session_factory(engine)() as s:
        _seed_blocked_source(s)

    resp = client.post(
        "/api/v1/admin/sources/SRC-AU-WORK-482/manual-capture",
        json={"raw_html": "<html><h1>Skilled visa 482</h1><p>sponsor required</p></html>"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["fetch_status"] == "OK"
    assert body["http_status"] == 200
    assert body["consecutive_failures"] == 0

    with get_session_factory(engine)() as s:
        doc = s.get(GovernmentDocument, "DOC-AU-WORK-482")
        assert doc is not None, "document should be created"
        assert doc.current_version == 1
        versions = s.execute(
            select(DocumentVersion).where(DocumentVersion.document_id == "DOC-AU-WORK-482")
        ).scalars().all()
        assert len(versions) == 1
        assert "retrieval_method=MANUAL_CAPTURE" in versions[0].diff_summary

        src = s.get(GovernmentSource, "SRC-AU-WORK-482")
        assert src.status == "ACTIVE"


def test_manual_capture_404_for_unknown_source(client, admin_headers, seed_lookup_rows):
    resp = client.post(
        "/api/v1/admin/sources/DOES-NOT-EXIST/manual-capture",
        json={"raw_html": "<html>visa info</html>"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_manual_capture_empty_html_400(client, engine, admin_headers, seed_lookup_rows):
    with get_session_factory(engine)() as s:
        _seed_blocked_source(s)
    resp = client.post(
        "/api/v1/admin/sources/SRC-AU-WORK-482/manual-capture",
        json={"raw_html": ""},
        headers=admin_headers,
    )
    assert resp.status_code == 400
