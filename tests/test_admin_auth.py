"""Admin role + auth tests.

Covers the explicit admin bootstrap path (env-driven, no assumed default),
role assignment semantics, and Bearer-based admin authorization:
* Admin is never auto-created unless ADMIN_EMAIL + ADMIN_PASSWORD are both set.
* promote_or_create idempotently creates or promotes an admin user.
* /auth/register always creates role="applicant" (no self-promotion).
* Admin-enforced routes require a valid Bearer token AND role="admin".

No real network / DB dependencies — uses an in-memory SQLite engine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db.models import Base  # noqa: E402
from api.main import app  # noqa: E402
from create_admin import (  # noqa: E402
    bootstrap_admin_from_env, promote_or_create,
)
from services.auth_service import create_access_token  # noqa: E402


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


def _register(client, email, password="pass123"):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    return resp.json()["access_token"]


def _count_users(engine):
    from sqlalchemy import text
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM api_users")
        ).scalar()


class TestBootstrapAdminFromEnv:
    def test_returns_none_when_env_missing(self, db_engine):
        with patch.dict(os.environ, {
            "ADMIN_EMAIL": "",
            "ADMIN_PASSWORD": "",
            "ADMIN_FULL_NAME": "",
        }, clear=False):
            result = bootstrap_admin_from_env(db_engine)
        assert result is None
        assert _count_users(db_engine) == 0

    def test_returns_none_when_only_email_set(self, db_engine):
        with patch.dict(os.environ, {
            "ADMIN_EMAIL": "admin@example.com",
            "ADMIN_PASSWORD": "",
        }, clear=False):
            result = bootstrap_admin_from_env(db_engine)
        assert result is None
        assert _count_users(db_engine) == 0

    def test_creates_admin_when_both_env_set(self, db_engine):
        with patch.dict(os.environ, {
            "ADMIN_EMAIL": "root@example.com",
            "ADMIN_PASSWORD": "S3cure!Pass",
            "ADMIN_FULL_NAME": "Root Admin",
        }, clear=False):
            result = bootstrap_admin_from_env(db_engine)
        assert result["action"] == "created"
        assert result["email"] == "root@example.com"
        from db.connection import get_session
        from db.repositories import UserRepo
        with get_session(db_engine) as s:
            user = UserRepo(s).get_by_email("root@example.com")
            assert user.role == "admin"
            assert user.is_active is True

    def test_promotes_existing_user(self, db_engine):
        from db.connection import get_session
        from db.repositories import UserRepo
        from services.auth_service import hash_password
        with get_session(db_engine) as s:
            repo = UserRepo(s)
            repo.create(email="staff@example.com", password_hash=hash_password("x1"),
                        full_name="Staff", role="applicant")
            s.commit()
        with patch.dict(os.environ, {
            "ADMIN_EMAIL": "staff@example.com",
            "ADMIN_PASSWORD": "S3cure!Pass",
        }, clear=False):
            result = bootstrap_admin_from_env(db_engine)
        assert result["action"] == "promoted"
        with get_session(db_engine) as s:
            assert UserRepo(s).get_by_email("staff@example.com").role == "admin"

    def test_idempotent_second_bootstrap_promotes(self, db_engine):
        env = {"ADMIN_EMAIL": "root2@example.com", "ADMIN_PASSWORD": "S3cure!Pass2"}
        with patch.dict(os.environ, env, clear=False):
            first = bootstrap_admin_from_env(db_engine)
            second = bootstrap_admin_from_env(db_engine)
        assert first["action"] == "created"
        assert second["action"] == "promoted"
        assert _count_users(db_engine) == 1


class TestPromoteOrCreate:
    def test_requires_password_for_new_user(self, db_engine):
        with pytest.raises(ValueError, match="password is required"):
            promote_or_create(db_engine, "new-user@example.com", None, None)

    def test_create_then_promote(self, db_engine):
        r1 = promote_or_create(db_engine, "a@b.com", "pw", "A")
        assert r1["action"] == "created"
        r2 = promote_or_create(db_engine, "a@b.com", None, None)
        assert r2["action"] == "promoted"


class TestRegistrationKeepsApplicantRole:
    def test_register_creates_applicant(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "applicant-role@example.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "applicant-role@example.com", "password": "pass123"},
        )
        assert resp.status_code == 200
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["role"] == "applicant"

    def test_register_ignores_role_body_field(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "no-role@example.com", "password": "pass123",
                  "role": "admin"},  # extra field should be ignored / rejected
        )
        assert resp.status_code in (200, 201)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "no-role@example.com", "password": "pass123"},
        )
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.json()["role"] == "applicant"


class TestAdminBearerAuthorization:
    def test_admin_can_access_admin_endpoint(self, client, db_engine):
        promote_or_create(db_engine, "admin-sec@example.com", "pw123456", "Admin")
        token = _register(client, "admin-sec@example.com", "pw123456")
        resp = client.get(
            "/api/v1/admin/rules/queue",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (200, 404)  # reachable with admin role; body may vary

    def test_non_admin_gets_403(self, client):
        token = _register(client, "plain-user@example.com")
        resp = client.get(
            "/api/v1/admin/rules/queue",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()

    def test_missing_token_gets_401(self, client):
        resp = client.get("/api/v1/admin/rules/queue")
        assert resp.status_code == 401

    def test_reusable_require_admin_forged_admin_token(self, client):
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-key"}):
            forged = create_access_token("whoever", role="admin")
        # Unknown user id -> 401 (token valid, but user doesn't exist)
        resp = client.get(
            "/api/v1/admin/rules/queue",
            headers={"Authorization": f"Bearer {forged['access_token']}"},
        )
        assert resp.status_code == 401
