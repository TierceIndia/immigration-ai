"""Tests for authentication (Sprint P4)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
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


@pytest.fixture()
def auth_token(client):
    """Register a user and return their JWT token."""
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "secret123", "full_name": "Test User"},
    )
    return resp.json()["access_token"]


@pytest.fixture()
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_success(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": "pass123", "full_name": "New User"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@example.com"
        assert data["full_name"] == "New User"
        assert data["role"] == "applicant"
        assert "user_id" in data

    def test_register_duplicate_email(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "pass456"},
        )
        # Deterministic 409 — never a raw 500 from the DB constraint.
        assert resp.status_code == 409
        assert resp.json()["detail"] == "Email already registered"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "login@example.com", "password": "mypass"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "mypass"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0
        assert data["role"] == "applicant"

    def test_login_wrong_password(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "wrong@example.com", "password": "correct"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "wrong@example.com", "password": "incorrect"},
        )
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "pass"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Me (protected route)
# ---------------------------------------------------------------------------

class TestMe:
    def test_me_authenticated(self, client, auth_headers):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "test@example.com"
        assert data["full_name"] == "Test User"
        assert data["role"] == "applicant"

    def test_me_no_token(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_invalid_token(self, client):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-token-here"},
        )
        assert resp.status_code == 401

    def test_me_malformed_header(self, client):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Token abc123"},
        )
        assert resp.status_code == 401

    def test_user_routes_require_authentication(self, client):
        assert client.get("/api/v1/applicants").status_code == 401
        assert client.get(
            "/api/v1/assessments", params={"applicant_id": "missing"}
        ).status_code == 401
        assert client.post(
            "/api/v1/assessments/evaluate",
            json={"applicant_id": "missing", "program_id": "missing"},
        ).status_code == 401


# ---------------------------------------------------------------------------
# Applicant ownership
# ---------------------------------------------------------------------------

class TestOwnership:
    def test_applicant_created_with_user_id(self, client, auth_headers):
        resp = client.post(
            "/api/v1/applicants",
            json={"age": 30},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "user_id" in data

    def test_user_sees_own_applicants(self, client, auth_headers):
        client.post("/api/v1/applicants", json={"age": 30}, headers=auth_headers)
        client.post("/api/v1/applicants", json={"age": 25}, headers=auth_headers)
        resp = client.get("/api/v1/applicants", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_user_cannot_access_another_users_applicant(self, client, auth_headers):
        created = client.post(
            "/api/v1/applicants", json={"age": 30}, headers=auth_headers
        ).json()
        other = client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "secret123"},
        ).json()
        other_headers = {"Authorization": f"Bearer {other['access_token']}"}
        applicant_id = created["applicant_id"]

        assert client.get(
            f"/api/v1/applicants/{applicant_id}", headers=other_headers
        ).status_code == 403
        assert client.patch(
            f"/api/v1/applicants/{applicant_id}",
            json={"age": 31},
            headers=other_headers,
        ).status_code == 403
