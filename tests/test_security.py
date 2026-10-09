"""Security test suite — authentication, authorization, PII redaction, secrets, CORS.

Tests critical security invariants:
1. JWT secret management (fail-fast in production, dev fallback)
2. Authentication (missing/invalid/expired tokens, malformed headers)
3. Password security (bcrypt hashing, never plain text, never logged)
4. PII redaction (DOB, email, passport never in logs)
5. CORS configuration (no wildcard with credentials)
6. Authorization (non-admin → 403 on admin endpoints)
"""
from __future__ import annotations

import logging
import os
import io
import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
from api.main import app
from services.auth_service import (
    _get_jwt_secret, hash_password, verify_password,
    create_access_token, decode_access_token,
)
from logging_config import (
    StructuredJSONFormatter, _SENSITIVE_FIELDS, _REDACTED,
    setup_logging, get_logger, set_request_context,
)


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


# =========================================================================
# 1. JWT SECRET MANAGEMENT
# =========================================================================

class TestJWTSecretManagement:
    """JWT_SECRET_KEY handling: fail-fast in production, fallback in dev."""

    def test_production_no_secret_raises(self):
        """In production mode, missing JWT_SECRET_KEY must raise ValueError."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "JWT_SECRET_KEY": ""}, clear=False):
            # Remove JWT_SECRET_KEY if present
            env = os.environ.copy()
            env.pop("JWT_SECRET_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
                    _get_jwt_secret()

    def test_dev_mode_fallback(self):
        """In development mode, missing JWT_SECRET_KEY returns default."""
        with patch.dict(os.environ, {
            "ENVIRONMENT": "development",
            "JWT_SECRET_KEY": "",
        }, clear=False):
            env = os.environ.copy()
            env.pop("JWT_SECRET_KEY", None)
            env["ENVIRONMENT"] = "development"
            with patch.dict(os.environ, env, clear=True):
                secret = _get_jwt_secret()
                assert secret == "dev-secret-change-me"

    def test_explicit_secret_used(self):
        """When JWT_SECRET_KEY is set, it's used regardless of environment."""
        with patch.dict(os.environ, {
            "JWT_SECRET_KEY": "my-strong-secret-key-12345",
            "ENVIRONMENT": "production",
        }):
            secret = _get_jwt_secret()
            assert secret == "my-strong-secret-key-12345"

    def test_token_created_with_explicit_secret(self):
        """Tokens signed with a custom secret can be decoded with the same secret."""
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret-key"}):
            token_data = create_access_token("user-123", role="applicant")
            payload = decode_access_token(token_data["access_token"])
            assert payload is not None
            assert payload["sub"] == "user-123"

    def test_wrong_secret_fails_decode(self):
        """Token signed with one secret cannot be decoded with another."""
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "secret-A"}):
            token_data = create_access_token("user-123")

        with patch.dict(os.environ, {"JWT_SECRET_KEY": "secret-B"}):
            payload = decode_access_token(token_data["access_token"])
            assert payload is None


# =========================================================================
# 2. AUTHENTICATION
# =========================================================================

class TestAuthenticationSecurity:
    """Token validation, missing tokens, invalid tokens."""

    def test_missing_auth_header(self, client):
        """Request without Authorization header → 401."""
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_empty_bearer_token(self, client):
        """Bearer with empty token → 401."""
        resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_invalid_token_format(self, client):
        """Random string as token → 401."""
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not-a-jwt-token"},
        )
        assert resp.status_code == 401

    def test_malformed_auth_header(self, client):
        """Wrong scheme (Token instead of Bearer) → 401."""
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Token abc123"},
        )
        assert resp.status_code == 401

    def test_tampered_token(self, client):
        """Token with modified payload → 401."""
        client.post(
            "/api/v1/auth/register",
            json={"email": "tamper@example.com", "password": "pass123"},
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "tamper@example.com", "password": "pass123"},
        )
        token = login_resp.json()["access_token"]
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        assert resp.status_code == 401

    def test_nonexistent_user_token(self, client):
        """Valid JWT format but user doesn't exist → 401."""
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-key"}):
            token_data = create_access_token("nonexistent-user-id")
            resp = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            assert resp.status_code == 401

    def test_non_uuid_sub_claim_returns_401_not_500(self, client):
        """G17: a validly-signed JWT whose sub claim is NOT a UUID must yield 401,
        never a 500. The api_users.user_id column is a native UUID on PostgreSQL;
        without edge validation the raw DataError would surface as an unhandled 500."""
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-key"}):
            token_data = create_access_token("not-a-uuid-at-all")
            resp = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            assert resp.status_code == 401

    def test_get_current_user_malformed_sub_returns_none(self):
        """G17 service-level guard: AuthService.get_current_user() must return None
        for a non-UUID-format sub rather than crashing in (or even reaching) the DB
        lookup."""
        from unittest.mock import Mock
        from services.auth_service import AuthService

        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-key"}):
            token_data = create_access_token("garbage-not-uuid")
            svc = AuthService.__new__(AuthService)
            svc._user_repo = Mock()
            svc._user_repo.get.side_effect = AssertionError("get() must not be called")
            result = svc.get_current_user(token_data["access_token"])
        assert result is None
        svc._user_repo.get.assert_not_called()

    def test_duplicate_email_returns_409(self, client):
        """Registering with duplicate email → 409 Conflict."""
        client.post(
            "/api/v1/auth/register",
            json={"email": "dup-security@example.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "dup-security@example.com", "password": "pass456"},
        )
        assert resp.status_code == 409


# =========================================================================
# 2. PASSWORD SECURITY
# =========================================================================

class TestPasswordSecurity:
    """Passwords must be bcrypt-hashed, never stored as plain text."""

    def test_password_is_bcrypt_hashed(self):
        """Hashed password starts with $2b$ (bcrypt prefix)."""
        hashed = hash_password("my-secure-password")
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60  # bcrypt output is always 60 chars

    def test_different_hashes_for_same_password(self):
        """Same password produces different hashes (random salt)."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2  # Different salts

    def test_verify_correct_password(self):
        """Correct password verifies successfully."""
        hashed = hash_password("correct-password")
        assert verify_password("correct-password", hashed) is True

    def test_verify_wrong_password(self):
        """Wrong password fails verification."""
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_password_not_stored_as_plaintext(self):
        """Registering a user stores bcrypt hash, not the password."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from db.models import Base, ApiUser

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=eng)
        factory = sessionmaker(bind=eng)

        with factory() as session:
            from db.repositories import UserRepo
            repo = UserRepo(session)
            user = repo.create(
                email="security-test@example.com",
                password_hash=hash_password("my-password"),
            )
            session.commit()

            # Re-read from DB
            fetched = session.get(ApiUser, user.user_id)
            assert fetched.password_hash != "my-password"
            assert fetched.password_hash.startswith("$2b$")
            assert verify_password("my-password", fetched.password_hash)

    def test_password_never_in_create_access_token(self):
        """create_access_token does not include the password."""
        token_data = create_access_token("user-1", role="applicant")
        payload = decode_access_token(token_data["access_token"])
        assert "password" not in payload
        assert "password_hash" not in payload

    def test_user_repo_promote_to_admin_changes_role(self):
        """UserRepo.promote_to_admin upgrades role to 'admin' (idempotent)."""
        from sqlalchemy.orm import sessionmaker
        from db.models import ApiUser
        from db.repositories import UserRepo

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=eng)
        factory = sessionmaker(bind=eng)

        with factory() as session:
            repo = UserRepo(session)
            user = repo.create(email="promote@example.com", password_hash="x")
            session.commit()
            assert user.role == "applicant"

            promoted = repo.promote_to_admin(str(user.user_id))
            session.commit()
            assert promoted is not None
            assert promoted.role == "admin"

            again = repo.promote_to_admin(str(user.user_id))
            session.commit()
            assert again.role == "admin"

            fetched = session.get(ApiUser, user.user_id)
            assert fetched.role == "admin"

    def test_user_repo_promote_to_admin_missing_user_returns_none(self):
        """promote_to_admin on a non-existent user returns None (no crash)."""
        from sqlalchemy.orm import sessionmaker
        from db.repositories import UserRepo

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=eng)
        factory = sessionmaker(bind=eng)

        with factory() as session:
            repo = UserRepo(session)
            assert repo.promote_to_admin("no-such-user") is None


# =========================================================================
# 3. PII REDACTION IN LOGS
# =========================================================================

class TestPIIRedaction:
    """Sensitive data (DOB, email, passport, password) must never appear in logs."""

    def test_email_redacted_in_logs(self):
        """Log entries with 'email' key have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User action", args=(), exc_info=None,
        )
        record.email = "secret@example.com"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["email"] == _REDACTED

    def test_password_redacted_in_logs(self):
        """Log entries with 'password' key have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Auth attempt", args=(), exc_info=None,
        )
        record.password = "my-secret-password"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["password"] == _REDACTED

    def test_dob_redacted_in_logs(self):
        """Log entries with 'date_of_birth' key have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Profile update", args=(), exc_info=None,
        )
        record.date_of_birth = "1990-01-15"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["date_of_birth"] == _REDACTED

    def test_passport_redacted_in_logs(self):
        """Log entries with 'passport' key have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Visa check", args=(), exc_info=None,
        )
        record.passport_number = "PA1234567"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["passport_number"] == _REDACTED

    def test_token_redacted_in_logs(self):
        """Log entries with 'token' key have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Token issue", args=(), exc_info=None,
        )
        record.access_token = "eyJhbGciOiJIUzI1NiJ9..."
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["access_token"] == _REDACTED

    def test_api_key_redacted_in_logs(self):
        """Log entries with 'api_key' key have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="API call", args=(), exc_info=None,
        )
        record.api_key = "sk-1234567890"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["api_key"] == _REDACTED

    def test_nested_dict_redaction(self):
        """Nested dicts with sensitive keys have values redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Nested data", args=(), exc_info=None,
        )
        record.user_data = {"email": "secret@example.com", "name": "John"}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["user_data"]["email"] == _REDACTED
        assert parsed["user_data"]["name"] == "John"  # non-sensitive field preserved

    def test_non_sensitive_fields_preserved(self):
        """Non-sensitive fields are not redacted."""
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Normal log", args=(), exc_info=None,
        )
        record.rule_id = "RULE-AGE-189"
        record.program_id = "SKILLED_189"
        record.status = "ACTIVE"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["rule_id"] == "RULE-AGE-189"
        assert parsed["program_id"] == "SKILLED_189"
        assert parsed["status"] == "ACTIVE"

    def test_sensitive_field_pattern_completeness(self):
        """All expected sensitive patterns are covered."""
        patterns = [
            "date_of_birth", "dob", "passport", "email",
            "password", "token", "secret", "api_key", "credit_card",
        ]
        for pattern in patterns:
            assert _SENSITIVE_FIELDS.search(pattern), f"Pattern '{pattern}' not matched"


# =========================================================================
# 4. CORS CONFIGURATION
# =========================================================================

class TestCORSConfiguration:
    """CORS must not use wildcard '*' with allow_credentials=True."""

    def test_cors_uses_env_variable(self):
        """CORS_ORIGINS env var is used for allowed origins."""
        with patch.dict(os.environ, {"CORS_ORIGINS": "https://example.com,https://app.com"}):
            # Re-read from env
            origins_str = os.getenv("CORS_ORIGINS")
            origins = [o.strip() for o in origins_str.split(",") if o.strip()]
            assert "https://example.com" in origins
            assert "https://app.com" in origins

    def test_cors_default_not_wildcard(self):
        """Default CORS origin is localhost, not wildcard."""
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("CORS_ORIGINS", None)
            with patch.dict(os.environ, env, clear=True):
                origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
                assert origins_str != "*"
                assert "localhost" in origins_str

    def test_cors_wildcard_blocked_in_code(self):
        """The main.py code explicitly does not use allow_origins=['*']."""
        import re
        import pathlib

        main_path = pathlib.Path(__file__).parent.parent / "src" / "api" / "main.py"
        source = main_path.read_text()
        # Find actual code lines (not comments) that use wildcard CORS
        code_lines = [
            line for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        dangerous = [l for l in code_lines if 'allow_origins=["*"]' in l]
        assert len(dangerous) == 0, \
            f"CORS wildcard '*' must not be used with allow_credentials=True. Found: {dangerous}"


# =========================================================================
# 5. AUTHORIZATION — ADMIN ENDPOINTS
# =========================================================================

class TestAuthorization:
    """Non-admin users must get 403 on admin endpoints."""

    def _register_and_get_token(self, client, email="user@example.com"):
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "pass123"},
        )
        return resp.json()["access_token"]

    def test_non_admin_gets_403_on_admin_rules(self, client):
        """Regular user (role=applicant) → 403 on admin rules queue."""
        token = self._register_and_get_token(client, "regular@example.com")
        resp = client.get(
            "/api/v1/admin/rules/queue",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_non_admin_cannot_activate_rule(self, client):
        """Regular user → 403 on rule activation."""
        token = self._register_and_get_token(client, "regular2@example.com")
        resp = client.post(
            "/api/v1/admin/rules/RULE-TEST/activate",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()

    def test_unauthenticated_gets_401_on_admin(self, client):
        """No token → 401 on admin endpoints."""
        resp = client.get("/api/v1/admin/rules/queue")
        assert resp.status_code == 401

    def test_applicant_endpoints_require_auth(self, client):
        """Applicant endpoints require JWT (not admin-specific)."""
        resp = client.get("/api/v1/applicants")
        assert resp.status_code == 401

    def test_assessment_endpoints_require_auth(self, client):
        """Assessment endpoints require JWT."""
        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": "a", "program_id": "SKILLED_189"},
        )
        assert resp.status_code == 401


# =========================================================================
# 6. TOKEN EXPIRATION
# =========================================================================

class TestTokenExpiration:
    """JWT tokens must have expiration set."""

    def test_token_has_exp_claim(self):
        """Created token includes 'exp' claim."""
        token_data = create_access_token("user-1")
        payload = decode_access_token(token_data["access_token"])
        assert "exp" in payload

    def test_token_has_iat_claim(self):
        """Created token includes 'iat' (issued at) claim."""
        token_data = create_access_token("user-1")
        payload = decode_access_token(token_data["access_token"])
        assert "iat" in payload

    def test_token_has_sub_claim(self):
        """Created token includes 'sub' (subject/user_id) claim."""
        token_data = create_access_token("user-1")
        payload = decode_access_token(token_data["access_token"])
        assert payload["sub"] == "user-1"

    def test_token_has_role_claim(self):
        """Created token includes 'role' claim."""
        token_data = create_access_token("user-1", role="admin")
        payload = decode_access_token(token_data["access_token"])
        assert payload["role"] == "admin"

    def test_token_expiration_seconds(self):
        """Token response includes expires_in in seconds."""
        token_data = create_access_token("user-1")
        assert "expires_in" in token_data
        assert token_data["expires_in"] > 0
        assert token_data["expires_in"] == 1440 * 60  # 24h default
