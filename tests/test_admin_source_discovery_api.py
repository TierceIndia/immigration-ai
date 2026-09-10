"""API tests for government source auto-discovery endpoints.

All network is mocked: discover_sources() is patched (never hits a real website).
Covers 401 unauthenticated, 403 non-admin, 200/201 admin success, the 404/409
paths, and the fact that approve() actually creates a real government_sources row.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base, Country, ImmigrationProgram, DiscoveredSource
from db.connection import get_session_factory
from db.repositories import DiscoveredSourceRepo
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
        json={"email": "disc-admin@test.com", "password": "admin123", "full_name": "Admin"},
    )
    with get_session_factory(engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "disc-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "disc-admin@test.com", "password": "admin123"},
    )
    hdr = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    # Ensure required lookup rows exist for approval.
    with get_session_factory(engine)() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        s.merge(ImmigrationProgram(
            program_id="WORK_482", country_id="AU", visa_category="WORK",
            subclass_code="482", name="Temporary Skill Shortage", status="ACTIVE",
        ))
        s.commit()
    return hdr


def _seed_candidate(session, candidate_id="DISC-AU-work-482",
                    url=None,
                    category="WORK", status="PENDING_REVIEW"):
    if url is None:
        # Unique URL per candidate (the column is globally unique).
        url = f"https://immi.homeaffairs.gov.au/visas/{candidate_id.lower()}"
    cand = DiscoveredSource(
        discovered_source_id=candidate_id,
        seed_url="https://example.gov.au/visas/work",
        candidate_url=url,
        page_title="Temporary Skill Shortage 482",
        matched_keywords=["482", "sponsor"],
        confidence_score=70,
        suggested_visa_category=category,
        suggested_country_id="AU",
        status=status,
    )
    session.add(cand)
    session.commit()
    return cand


def _mock_discover_fn(created_url="https://immi.homeaffairs.gov.au/visas/work/482"):
    """Return a discover_sources replacement that persists one candidate to the session
    (mirroring the real module) without any network access."""
    def _fn(seed_url, max_depth=2, suggested_category=None, session=None,
            fetch_fn=None, min_delay_seconds=0.0):
        if session is not None:
            repo = DiscoveredSourceRepo(session)
            existing = repo.get("DISC-MOCK-WORK-482")
            if existing is None:
                repo.create(
                    discovered_source_id="DISC-MOCK-WORK-482",
                    seed_url=seed_url,
                    candidate_url=created_url,
                    page_title="Temporary Skill Shortage 482",
                    matched_keywords=["482", "sponsor"],
                    confidence_score=70,
                    suggested_visa_category=suggested_category or "WORK",
                    suggested_country_id="AU",
                )
                session.commit()
        from source_discovery import DiscoveredCandidate
        return [DiscoveredCandidate(candidate_url=created_url)], {
            "seed_url": seed_url, "robots_applied": True,
            "robots_blocked_seed": False, "candidates_found": 1,
        }
    return _fn


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestDiscoveryAuth:
    def test_unauthenticated_401(self, client):
        assert client.post("/api/v1/admin/sources/discover",
                           json={"seed_url": "https://example.gov.au"}).status_code == 401
        assert client.get("/api/v1/admin/sources/discovered").status_code == 401

    def test_non_admin_403(self, client):
        client.post("/api/v1/auth/register",
                    json={"email": "u@t.com", "password": "pass123"})
        tok = client.post("/api/v1/auth/login",
                          json={"email": "u@t.com", "password": "pass123"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert client.post("/api/v1/admin/sources/discover",
                           json={"seed_url": "https://example.gov.au"}, headers=h).status_code == 403
        assert client.get("/api/v1/admin/sources/discovered", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# Discover + list
# ---------------------------------------------------------------------------

class TestDiscoverEndpoint:
    def test_discover_returns_candidates(self, client, engine, admin_headers, monkeypatch):
        monkeypatch.setattr(
            "api.routes.admin_sources.discover_sources",
            _mock_discover_fn(),
        )
        resp = client.post(
            "/api/v1/admin/sources/discover",
            json={"seed_url": "https://immi.homeaffairs.gov.au/visas/work",
                  "suggested_category": "WORK"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any(c["suggested_visa_category"] == "WORK" for c in data)

    def test_list_discovered_filter(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _seed_candidate(s, "DISC-1", category="WORK")
            _seed_candidate(s, "DISC-2", category="STUDENT")
            _seed_candidate(s, "DISC-3", category="STUDENT", status="REJECTED")
        resp = client.get("/api/v1/admin/sources/discovered?suggested_visa_category=STUDENT",
                          headers=admin_headers)
        assert len(resp.json()) == 2
        resp = client.get("/api/v1/admin/sources/discovered?suggested_visa_category=STUDENT&status=REJECTED",
                          headers=admin_headers)
        assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# Approve -> real source
# ---------------------------------------------------------------------------

class TestApproveEndpoint:
    def test_approve_creates_real_source(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _seed_candidate(s, "DISC-APPROVE", url="https://immi.homeaffairs.gov.au/visas/work/482")
        resp = client.post(
            "/api/v1/admin/sources/discovered/DISC-APPROVE/approve",
            json={
                "source_id": "SRC-AU-WORK-482",
                "program_id": "WORK_482",
                "visa_category": "WORK",
                "authority": "Department of Home Affairs",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_id"] == "SRC-AU-WORK-482"
        assert body["url"] == "https://immi.homeaffairs.gov.au/visas/work/482"
        assert body["visa_category"] == "WORK"
        # candidate transitions to APPROVED
        with get_session_factory(engine)() as s:
            cand = s.get(DiscoveredSource, "DISC-APPROVE")
            assert cand.status == "APPROVED"
            assert cand.reviewed_by is not None
        # real government_sources row exists
        from db.models import GovernmentSource
        from db.repositories import SourceRepo
        with get_session_factory(engine)() as s:
            src = SourceRepo(s).get("SRC-AU-WORK-482")
            assert src is not None
            assert src.url == "https://immi.homeaffairs.gov.au/visas/work/482"

    def test_approve_not_found_404(self, client, engine, admin_headers):
        resp = client.post(
            "/api/v1/admin/sources/discovered/NOPE/approve",
            json={"source_id": "SRC-X", "program_id": "WORK_482",
                  "visa_category": "WORK", "authority": "X"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_approve_already_reviewed_409(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _seed_candidate(s, "DISC-REJ", status="REJECTED")
        resp = client.post(
            "/api/v1/admin/sources/discovered/DISC-REJ/approve",
            json={"source_id": "SRC-Y", "program_id": "WORK_482",
                  "visa_category": "WORK", "authority": "X"},
            headers=admin_headers,
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

class TestRejectEndpoint:
    def test_reject_records_reason(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _seed_candidate(s, "DISC-REJECT")
        resp = client.post(
            "/api/v1/admin/sources/discovered/DISC-REJECT/reject",
            json={"reason": "duplicate of existing source"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"
        with get_session_factory(engine)() as s:
            cand = s.get(DiscoveredSource, "DISC-REJECT")
            assert cand.rejection_reason == "duplicate of existing source"
            assert cand.reviewed_by is not None
