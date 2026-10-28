"""Tests for the ALLOW_PUBLIC_REGISTRATION gate on POST /api/v1/auth/register
(Part 2). Defaults to false in production; the test session opts in globally
(see conftest.py's _set_testing_env) since many unrelated test files use
self-registration purely as a fixture helper - this file explicitly
overrides that back to false/unset to prove the gate itself.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
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


class TestPublicRegistrationGate:
    def test_disabled_by_default_when_flag_is_unset(self, client, monkeypatch):
        monkeypatch.delenv("ALLOW_PUBLIC_REGISTRATION", raising=False)
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "gate-unset@test.com", "password": "pass123"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()

    def test_disabled_when_flag_is_explicitly_false(self, client, monkeypatch):
        monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "false")
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "gate-false@test.com", "password": "pass123"},
        )
        assert resp.status_code == 403

    def test_enabled_when_flag_is_true(self, client, monkeypatch):
        monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "true")
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "gate-true@test.com", "password": "pass123"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "applicant"

    def test_flag_value_is_case_insensitive(self, client, monkeypatch):
        monkeypatch.setenv("ALLOW_PUBLIC_REGISTRATION", "TRUE")
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "gate-case@test.com", "password": "pass123"},
        )
        assert resp.status_code == 201

    def test_disabled_registration_never_reaches_the_db(self, client, monkeypatch, engine):
        """A rejected registration must not create a user row at all."""
        monkeypatch.delenv("ALLOW_PUBLIC_REGISTRATION", raising=False)
        client.post(
            "/api/v1/auth/register",
            json={"email": "gate-no-row@test.com", "password": "pass123"},
        )
        from db.connection import get_session_factory
        from db.models import ApiUser
        with get_session_factory(engine)() as s:
            assert s.query(ApiUser).filter_by(email="gate-no-row@test.com").first() is None
