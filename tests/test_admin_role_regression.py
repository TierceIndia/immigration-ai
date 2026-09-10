"""Admin-role regression tests — PostgreSQL keeps this invariant:

    api_users.role  ->  JWT issued from DB identity/role  ->  /auth/me reflects
    the CURRENT DB role  ->  require_admin reflects the CURRENT DB role.

Root cause this guards against: `admin@gmail.com`'s api_users.role was
`applicant` (stale/wrong DB state) while the operator believed the user was an
administrator. The application already read the role from the database (never
from the JWT payload), so the fix is (a) the database is the source of truth and
(b) an admin bootstrap that is idempotent and never downgrades.

Uses in-memory SQLite engine, matching the other route test modules.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db.models import Base  # noqa: E402
from api.main import app  # noqa: E402
from create_admin import promote_or_create  # noqa: E402
from services.auth_service import (  # noqa: E402
    create_access_token, decode_access_token,
)
from db.connection import get_session  # noqa: E402
from db.repositories import UserRepo  # noqa: E402
from services.auth_service import hash_password  # noqa: E402


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
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _login(client, email, password="pass123"):
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _me(client, token):
    return client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )


def _role_in_db(engine, email):
    with get_session(engine) as s:
        return UserRepo(s).get_by_email(email).role


class TestAdminDBSourceOfTruth:
    def test_promoted_user_has_admin_role_in_db(self, db_engine):
        promote_or_create(db_engine, "admin-db@example.com", "pw123456", "Admin")
        assert _role_in_db(db_engine, "admin-db@example.com") == "admin"

    def test_admin_login_jwt_carries_admin_role_claim(self, client, db_engine):
        promote_or_create(db_engine, "admin-jwt@example.com", "pw123456", "Admin")
        token_data = _login(client, "admin-jwt@example.com", "pw123456")
        payload = decode_access_token(token_data["access_token"])
        assert payload is not None
        assert payload["role"] == "admin"
        assert payload["sub"] is not None

    def test_admin_auth_me_returns_admin(self, client, db_engine):
        promote_or_create(db_engine, "admin-me@example.com", "pw123456", "Admin")
        tok = _login(client, "admin-me@example.com", "pw123456")["access_token"]
        resp = _me(client, tok)
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "admin-me@example.com"
        assert body["role"] == "admin"

    def test_applicant_auth_me_returns_applicant(self, client):
        token_data = _register(client, "applicant-me@example.com")
        assert token_data["role"] == "applicant"
        tok = _login(client, "applicant-me@example.com")["access_token"]
        resp = _me(client, tok)
        assert resp.status_code == 200
        assert resp.json()["role"] == "applicant"

    def test_applicant_admin_endpoint_403(self, client):
        _register(client, "app-403@example.com")
        tok = _login(client, "app-403@example.com")["access_token"]
        resp = client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 403

    def test_admin_admin_endpoint_200(self, client, db_engine):
        promote_or_create(db_engine, "admin-ok@example.com", "pw123456", "Admin")
        tok = _login(client, "admin-ok@example.com", "pw123456")["access_token"]
        resp = client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200


class TestAdminNeverDowngraded:
    def test_admin_survives_repeated_operations(self, client, db_engine):
        """After promotion, login + /auth/me + registering another user + another
        bootstrap must never downgrade the admin back to applicant."""
        promote_or_create(db_engine, "stay-admin@example.com", "pw123456", "Admin")
        assert _role_in_db(db_engine, "stay-admin@example.com") == "admin"

        # login + /auth/me
        tok = _login(client, "stay-admin@example.com", "pw123456")["access_token"]
        assert _me(client, tok).json()["role"] == "admin"

        # normal registration of another user must not touch the admin
        _register(client, "other@example.com")
        assert _role_in_db(db_engine, "stay-admin@example.com") == "admin"

        # repeated bootstrap (startup path) must not downgrade
        promote_or_create(db_engine, "stay-admin@example.com", "pw123456", "Admin")
        assert _role_in_db(db_engine, "stay-admin@example.com") == "admin"

        # fresh login still yields admin role
        tok2 = _login(client, "stay-admin@example.com", "pw123456")["access_token"]
        assert _me(client, tok2).json()["role"] == "admin"

    def test_client_cannot_self_promote(self, client):
        """Requesting role=admin at registration must yield an applicant."""
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "self-promote@example.com", "password": "pass123",
                  "role": "admin"},
        )
        # Role field is not part of the request schema -> ignored/rejected at 422.
        # Either way the created user must be an applicant.
        if resp.status_code == 422:
            # Model rejects the extra field; verify by registering without it.
            reg = _register(client, "self-promote@example.com")
            assert reg["role"] == "applicant"
        else:
            assert resp.status_code == 201
            assert resp.json()["role"] == "applicant"

    def test_re_registering_existing_admin_returns_409_and_preserves_role(self, client, db_engine):
        """Re-registering an existing admin email via /auth/register (which always
        carries role='applicant') must be rejected with a clean 409 and must NOT
        downgrade or otherwise touch the existing admin row."""
        promote_or_create(db_engine, "admin-dup@example.com", "pw123456", "Admin")
        assert _role_in_db(db_engine, "admin-dup@example.com") == "admin"

        # Attempt to re-register the SAME email as a normal applicant.
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "admin-dup@example.com", "password": "otherpass"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "Email already registered"

        # Account untouched: still admin, and still logs in with its real password.
        assert _role_in_db(db_engine, "admin-dup@example.com") == "admin"
        tok = _login(client, "admin-dup@example.com", "pw123456")["access_token"]
        assert _me(client, tok).json()["role"] == "admin"
        assert client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {tok}"},
        ).status_code == 200


class TestRoleChangeReflectsCurrentDB:
    def test_promotion_then_fresh_login_shows_admin(self, client, db_engine):
        _register(client, "upgrade@example.com")
        with get_session(db_engine) as s:
            u = UserRepo(s).get_by_email("upgrade@example.com")
            UserRepo(s).set_role(str(u.user_id), "admin")
            s.commit()
        # fresh login reflects the NEW DB role
        tok = _login(client, "upgrade@example.com")["access_token"]
        assert _me(client, tok).json()["role"] == "admin"
        resp = client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200

    def test_demotion_then_fresh_login_shows_applicant(self, client, db_engine):
        promote_or_create(db_engine, "downgrade@example.com", "pw123456", "Admin")
        # admin works now
        tok = _login(client, "downgrade@example.com", "pw123456")["access_token"]
        assert _me(client, tok).json()["role"] == "admin"
        # demote in DB
        with get_session(db_engine) as s:
            u = UserRepo(s).get_by_email("downgrade@example.com")
            UserRepo(s).set_role(str(u.user_id), "applicant")
            s.commit()
        # fresh login reflects the NEW DB role (applicant)
        tok2 = _login(client, "downgrade@example.com", "pw123456")["access_token"]
        assert _me(client, tok2).json()["role"] == "applicant"
        resp = client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {tok2}"},
        )
        assert resp.status_code == 403

    def test_stale_admin_token_does_not_grant_access_after_demotion(self, client, db_engine):
        """A JWT minted while the user was admin must not keep admin access after a
        demotion: require_admin reads the DB on every request."""
        promote_or_create(db_engine, "stale-tok@example.com", "pw123456", "Admin")
        admin_tok = _login(client, "stale-tok@example.com", "pw123456")["access_token"]
        assert _me(client, admin_tok).json()["role"] == "admin"

        with get_session(db_engine) as s:
            u = UserRepo(s).get_by_email("stale-tok@example.com")
            UserRepo(s).set_role(str(u.user_id), "applicant")
            s.commit()

        # The SAME (formerly-admin) token now yields 403 on admin endpoints.
        resp = client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert resp.status_code == 403
        # And /auth/me reports the current DB role, not the JWT role claim.
        me = _me(client, admin_tok)
        assert me.json()["role"] == "applicant"


class TestAuthMeUsesDBRoleNotJWTClaim:
    def test_me_reports_db_role_when_jwt_claim_differs(self, client, db_engine):
        """Even if a token is minted with role=admin from a forged POV, /auth/me must
        report the DB role. (The app mints from DB; this guards against regressions
        where the JWT role claim could be trusted.)"""
        # Create a user as applicant, then mint a token claiming admin via a direct
        # create_access_token call (simulates a stale/wrong claim) using the real
        # user's UUID.
        _register(client, "db-claim@example.com")
        with get_session(db_engine) as s:
            u = UserRepo(s).get_by_email("db-claim@example.com")
            user_id = str(u.user_id)
        with patch.dict(os.environ, {}, clear=False):
            forged = create_access_token(user_id, role="admin")
        resp = _me(client, forged["access_token"])
        assert resp.status_code == 200
        assert resp.json()["role"] == "applicant"
        # And admin endpoint access is denied: DB role is applicant.
        ar = client.get(
            "/api/v1/admin/sources",
            headers={"Authorization": f"Bearer {forged['access_token']}"},
        )
        assert ar.status_code == 403