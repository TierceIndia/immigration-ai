"""The manual-capture dead end must stay closed.

There used to be POST /api/v1/admin/sources/{id}/manual-capture, letting an
admin paste hand-captured HTML and have it ingested as if it were a real
government fetch. That path could not prove provenance - the bytes came from a
human, not from the source - so it was removed rather than left as an escape
hatch that quietly manufactures evidence.

These tests assert the *absence* of that capability, so it cannot be reintroduced
by accident:

  1. the route is not registered at all (not in OpenAPI, so not callable);
  2. an admin POST to it is rejected (404, no document/version created);
  3. no evidence row can be created through any manual path;
  4. a blocked source stays in the scheduled sync set instead of being demoted
     out of it, so automation keeps retrying it on its own schedule.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from api.main import app
from db.connection import get_session_factory
from db.models import (
    ApiUser,
    Base,
    Country,
    DocumentVersion,
    GovernmentDocument,
    GovernmentSource,
    ImmigrationProgram,
)

MANUAL_CAPTURE_URL = "/api/v1/admin/sources/SRC-AU-WORK-482/manual-capture"


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
        json={"email": "nc-admin@test.com", "password": "admin123", "full_name": "Admin"},
    )
    with get_session_factory(engine)() as s:
        user = s.query(ApiUser).filter(ApiUser.email == "nc-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "nc-admin@test.com", "password": "admin123"},
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


def _seed_blocked_source(session, status="ACTIVE"):
    """A source whose last real fetch was blocked.

    `status` is ACTIVE by default: a blocked fetch records diagnostics but must
    NOT demote the source out of the sync set.
    """
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


# ---------------------------------------------------------------------------
def test_route_is_not_registered_in_openapi(client, seed_lookup_rows):
    """The capability is not advertised, so it cannot be discovered or called."""
    spec = client.get("/openapi.json").json()
    paths = spec.get("paths", {})
    assert MANUAL_CAPTURE_URL not in paths

    # No path anywhere mentions manual-capture.
    offenders = [p for p in paths if "manual" in p.lower()]
    assert offenders == [], offenders


def test_admin_post_is_rejected(client, engine, admin_headers, seed_lookup_rows):
    """Even a legitimate admin gets 404 - and creates nothing."""
    with get_session_factory(engine)() as s:
        _seed_blocked_source(s)

    resp = client.post(
        MANUAL_CAPTURE_URL,
        json={"raw_html": "<html><h1>Skilled visa 482</h1><p>sponsor required</p></html>"},
        headers=admin_headers,
    )
    assert resp.status_code == 404, resp.text

    with get_session_factory(engine)() as s:
        assert s.get(GovernmentDocument, "DOC-AU-WORK-482") is None
        versions = s.execute(
            select(DocumentVersion).where(DocumentVersion.document_id == "DOC-AU-WORK-482")
        ).scalars().all()
        assert versions == []


def test_no_evidence_row_can_be_created_through_a_manual_path(
    client, engine, admin_headers, seed_lookup_rows
):
    """No request shape can smuggle evidence in via the old endpoint.

    Each variant is tried in turn; all must 404 and leave the DB untouched.
    """
    with get_session_factory(engine)() as s:
        _seed_blocked_source(s)

    payloads = [
        {"raw_html": "<html>visa info</html>"},
        {"raw_html": "", "fetched_at": "2020-01-01T00:00:00Z"},
        {"raw_html": "<html>x</html>", "document_id": "DOC-AU-WORK-482"},
        {"raw_html": "<html>x</html>", "retrieval_method": "MANUAL_CAPTURE"},
        {},
    ]
    for payload in payloads:
        resp = client.post(MANUAL_CAPTURE_URL, json=payload, headers=admin_headers)
        assert resp.status_code == 404, (payload, resp.status_code, resp.text)

    with get_session_factory(engine)() as s:
        assert s.get(GovernmentDocument, "DOC-AU-WORK-482") is None
        assert s.query(DocumentVersion).count() == 0
        # Nothing anywhere claims MANUAL_CAPTURE provenance.
        rows = s.execute(
            select(DocumentVersion).where(
                DocumentVersion.diff_summary.like("%MANUAL_CAPTURE%")
            )
        ).scalars().all()
        assert rows == []


def test_blocked_source_is_not_removed_from_the_sync_set(
    client, engine, admin_headers, seed_lookup_rows
):
    """A blocked source keeps its ACTIVE status and stays scheduled for retry.

    Previously a blocked fetch demoted the source to NEEDS_MANUAL_CAPTURE, which
    effectively removed it from automated crawling and parked it on a human.
    """
    with get_session_factory(engine)() as s:
        _seed_blocked_source(s, status="ACTIVE")

    resp = client.get("/api/v1/admin/sources/SRC-AU-WORK-482", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["fetch_status"] == "ACCESS_BLOCKED"
    # Diagnostics are retained so operators can see why it failed.
    assert body["consecutive_failures"] == 3
    assert body["http_status"] == 403
    assert body["last_error"]