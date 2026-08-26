"""Tests for admin rules API — review, approve, reject, activate."""
from __future__ import annotations

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base, Rule as RuleORM
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
def admin_token(client, engine):
    """Register admin user and return JWT token."""
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "admin@test.com", "password": "admin123", "full_name": "Admin"},
    )
    # Need to set role to admin directly in DB
    with get_session_factory(engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "admin@test.com").first()
        user.role = "admin"
        s.commit()

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "admin123"},
    )
    return resp.json()["access_token"]


@pytest.fixture()
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _create_rule(session, rule_id="R-1", status="DRAFT", program_id="SKILLED_189",
                 category="AGE", field="age"):
    obj = RuleORM(
        rule_id=rule_id,
        requirement_id=f"REQ-{rule_id}",
        program_id=program_id,
        category=category,
        field=field,
        operator="GTE",
        value_json=json.dumps(25),
        unit="years",
        mandatory=True,
        critical=True,
        message="Must be at least 25",
        status=status,
        current_version=1,
        evidence_text="Applicants must be at least 25",
    )
    session.add(obj)
    session.commit()
    return obj


class TestAdminAccess:
    def test_non_admin_rejected(self, client):
        # Register normal user
        client.post(
            "/api/v1/auth/register",
            json={"email": "user@test.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "user@test.com", "password": "pass123"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        resp = client.get("/api/v1/admin/rules/queue", headers=headers)
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/admin/rules/queue")
        assert resp.status_code == 401


class TestReviewQueue:
    def test_empty_queue(self, client, engine, admin_headers):
        resp = client.get("/api/v1/admin/rules/queue", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["drafts"] == 0
        assert data["counts"]["active"] == 0

    def test_queue_shows_drafts(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
            _create_rule(s, "R-2", status="DRAFT")
        resp = client.get("/api/v1/admin/rules/queue", headers=admin_headers)
        assert resp.json()["counts"]["drafts"] == 2


class TestListRules:
    def test_list_all(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
            _create_rule(s, "R-2", status="ACTIVE")
        resp = client.get("/api/v1/admin/rules", headers=admin_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_by_status(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
            _create_rule(s, "R-2", status="ACTIVE")
        resp = client.get("/api/v1/admin/rules?status=DRAFT", headers=admin_headers)
        assert len(resp.json()) == 1
        assert resp.json()[0]["rule_id"] == "R-1"


class TestStatusTransitionsAPI:
    def test_validate_rule(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
        resp = client.post(
            "/api/v1/admin/rules/R-1/validate",
            json={"actor": "admin1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "VALIDATED"

    def test_approve_rule(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="VALIDATED")
        resp = client.post(
            "/api/v1/admin/rules/R-1/approve",
            json={"actor": "admin1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"

    def test_activate_rule(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED")
        resp = client.post(
            "/api/v1/admin/rules/R-1/activate",
            json={"actor": "admin1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACTIVE"

    def test_reject_rule(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
        resp = client.post(
            "/api/v1/admin/rules/R-1/reject",
            json={"actor": "admin1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"

    def test_invalid_transition_returns_400(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
        resp = client.post(
            "/api/v1/admin/rules/R-1/activate",
            json={"actor": "admin1"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_full_lifecycle(self, client, engine, admin_headers):
        """DRAFT → VALIDATED → APPROVED → ACTIVE"""
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")

        resp = client.post("/api/v1/admin/rules/R-1/validate",
                           json={"actor": "a"}, headers=admin_headers)
        assert resp.json()["status"] == "VALIDATED"

        resp = client.post("/api/v1/admin/rules/R-1/approve",
                           json={"actor": "a"}, headers=admin_headers)
        assert resp.json()["status"] == "APPROVED"

        resp = client.post("/api/v1/admin/rules/R-1/activate",
                           json={"actor": "a"}, headers=admin_headers)
        assert resp.json()["status"] == "ACTIVE"


class TestTransactionalActivation:
    def test_activate_supersedes_old(self, client, engine, admin_headers):
        """Activating R-2 should supersede R-1 (same field)."""
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", category="AGE", field="age")
            _create_rule(s, "R-2", status="APPROVED", category="AGE", field="age")

        resp = client.post("/api/v1/admin/rules/R-2/activate",
                           json={"actor": "a"}, headers=admin_headers)
        assert resp.json()["status"] == "ACTIVE"

        # Check R-1 was superseded
        resp = client.get("/api/v1/admin/rules/R-1", headers=admin_headers)
        assert resp.json()["status"] == "SUPERSEDED"


class TestGetRule:
    def test_get_existing(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT")
        resp = client.get("/api/v1/admin/rules/R-1", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["rule_id"] == "R-1"

    def test_get_nonexistent(self, client, admin_headers):
        resp = client.get("/api/v1/admin/rules/NOPE", headers=admin_headers)
        assert resp.status_code == 404
