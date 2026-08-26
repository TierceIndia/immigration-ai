"""Tests for the FastAPI application (Sprint P3)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
from api.main import app


@pytest.fixture()
def db_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def client(db_engine):
    app.state.db_engine = db_engine
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Applicants
# ---------------------------------------------------------------------------

class TestApplicants:
    def _auth_headers(self, client):
        """Register a test user and return auth headers."""
        client.post(
            "/api/v1/auth/register",
            json={"email": "api-test@example.com", "password": "pass123", "full_name": "API Test"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "api-test@example.com", "password": "pass123"},
        )
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_create_applicant(self, client):
        headers = self._auth_headers(client)
        resp = client.post(
            "/api/v1/applicants",
            json={"age": 30, "english_level": "competent", "visa_subclass": "189"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["age"] == 30
        assert data["english_level"] == "competent"
        assert "applicant_id" in data

    def test_list_applicants(self, client):
        headers = self._auth_headers(client)
        client.post("/api/v1/applicants", json={"age": 30}, headers=headers)
        client.post("/api/v1/applicants", json={"age": 25}, headers=headers)
        resp = client.get("/api/v1/applicants", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_get_applicant(self, client):
        headers = self._auth_headers(client)
        create_resp = client.post(
            "/api/v1/applicants",
            json={"age": 30},
            headers=headers,
        )
        applicant_id = create_resp.json()["applicant_id"]
        resp = client.get(f"/api/v1/applicants/{applicant_id}")
        assert resp.status_code == 200
        assert resp.json()["age"] == 30

    def test_get_applicant_not_found(self, client):
        resp = client.get("/api/v1/applicants/nonexistent")
        assert resp.status_code == 404

    def test_update_applicant(self, client):
        headers = self._auth_headers(client)
        create_resp = client.post(
            "/api/v1/applicants",
            json={"age": 30},
            headers=headers,
        )
        applicant_id = create_resp.json()["applicant_id"]
        resp = client.patch(
            f"/api/v1/applicants/{applicant_id}",
            json={"age": 31, "english_level": "proficient"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["age"] == 31
        assert data["english_level"] == "proficient"

    def test_update_applicant_not_found(self, client):
        resp = client.patch("/api/v1/applicants/nonexistent", json={"age": 31})
        assert resp.status_code == 404

    def test_create_applicant_minimal(self, client):
        headers = self._auth_headers(client)
        resp = client.post(
            "/api/v1/applicants",
            json={},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["age"] is None
        assert "applicant_id" in data


# ---------------------------------------------------------------------------
# Assessments (stub endpoints)
# ---------------------------------------------------------------------------

class TestAssessments:
    def test_list_assessments_empty(self, client):
        resp = client.get(
            "/api/v1/assessments",
            params={"applicant_id": "nonexistent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["assessments"] == []

    def test_get_assessment_not_found(self, client):
        resp = client.get("/api/v1/assessments/nonexistent")
        assert resp.status_code == 404

    def test_create_assessment_not_implemented(self, client):
        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": "a-1", "program_id": "SKILLED_189"},
        )
        assert resp.status_code == 501

    def test_evaluate_inline_not_implemented(self, client):
        resp = client.post(
            "/api/v1/assessments/evaluate",
            json={"applicant_id": "a-1", "program_id": "SKILLED_189"},
        )
        assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------

class TestMiddleware:
    def test_request_id_returned(self, client):
        resp = client.get("/health")
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_forwarded(self, client):
        resp = client.get("/health", headers={"X-Request-ID": "my-custom-id"})
        assert resp.headers["X-Request-ID"] == "my-custom-id"
