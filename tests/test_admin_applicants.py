"""Tests for GET /api/v1/admin/applicants and GET /api/v1/admin/applicants/{id}
(Part 1c) - a read-only, admin-only, cross-user view. The existing
owner-scoped /applicants and /assessments routes are untouched and must stay
exactly as restrictive as before.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
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


def _register_and_login(client, email, password="pass123"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def admin_headers(client):
    headers = _register_and_login(client, "applicants-admin@test.com")
    with get_session_factory(client.app.state.db_engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "applicants-admin@test.com").first()
        user.role = "admin"
        s.commit()
    return _register_and_login(client, "applicants-admin@test.com")


@pytest.fixture()
def applicant_user(client):
    """A normal (non-admin) user who owns one applicant profile."""
    headers = _register_and_login(client, "owner1@test.com")
    resp = client.post(
        "/api/v1/applicants",
        json={"first_name": "Jane", "last_name": "Doe", "age": 29,
              "english_level": "COMPETENT", "points": 65},
        headers=headers,
    )
    assert resp.status_code == 201
    return {"headers": headers, "applicant": resp.json(), "email": "owner1@test.com"}


class TestListApplicants:
    def test_admin_sees_all_applicants_with_owner_email(self, client, admin_headers, applicant_user):
        resp = client.get("/api/v1/admin/applicants", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        match = next(a for a in body["applicants"] if a["applicant_id"] == applicant_user["applicant"]["applicant_id"])
        assert match["owner_email"] == applicant_user["email"]
        assert match["first_name"] == "Jane"

    def test_pagination_fields_present(self, client, admin_headers):
        resp = client.get("/api/v1/admin/applicants?limit=5&offset=0", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 5
        assert body["offset"] == 0
        assert isinstance(body["total"], int)

    def test_non_admin_gets_403(self, client, applicant_user):
        resp = client.get("/api/v1/admin/applicants", headers=applicant_user["headers"])
        assert resp.status_code == 403

    def test_no_token_gets_401(self, client):
        resp = client.get("/api/v1/admin/applicants")
        assert resp.status_code == 401


class TestGetApplicantDetail:
    def test_admin_sees_applicant_and_assessments(self, client, admin_headers, applicant_user):
        applicant_id = applicant_user["applicant"]["applicant_id"]
        resp = client.get(f"/api/v1/admin/applicants/{applicant_id}", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["applicant_id"] == applicant_id
        assert body["owner_email"] == applicant_user["email"]
        assert body["assessments"] == []  # none created in this test

    def test_unknown_applicant_returns_404(self, client, admin_headers):
        resp = client.get(
            "/api/v1/admin/applicants/00000000-0000-0000-0000-000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_malformed_id_returns_404_not_500(self, client, admin_headers):
        resp = client.get("/api/v1/admin/applicants/not-a-uuid", headers=admin_headers)
        assert resp.status_code == 404

    def test_non_admin_gets_403(self, client, applicant_user):
        applicant_id = applicant_user["applicant"]["applicant_id"]
        resp = client.get(
            f"/api/v1/admin/applicants/{applicant_id}", headers=applicant_user["headers"],
        )
        assert resp.status_code == 403


class TestExistingOwnerScopingIsUntouched:
    """Guard against this Part accidentally loosening the existing
    user-owned /applicants and /assessments routes."""

    def test_owner_scoped_applicants_route_still_only_sees_own(self, client, applicant_user):
        other_headers = _register_and_login(client, "owner2@test.com")
        resp = client.get("/api/v1/applicants", headers=other_headers)
        assert resp.status_code == 200
        assert resp.json() == []  # owner2 has no applicants of their own

    def test_owner_scoped_get_by_id_still_403s_for_non_owner(self, client, applicant_user):
        applicant_id = applicant_user["applicant"]["applicant_id"]
        other_headers = _register_and_login(client, "owner3@test.com")
        resp = client.get(f"/api/v1/applicants/{applicant_id}", headers=other_headers)
        assert resp.status_code == 403
