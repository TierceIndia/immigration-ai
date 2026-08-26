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
    def test_health_liveness(self, client):
        """GET /health always returns 200 — process alive."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readiness_ok(self, client):
        """GET /ready returns 200 when DB is connected."""
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"

    def test_readiness_degraded_without_db(self):
        """GET /ready returns 503 when DB is not configured."""
        from api.main import create_app
        app2 = create_app()
        app2.state.db_engine = None
        with TestClient(app2) as c:
            resp = c.get("/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["db"] == "disconnected"


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
    def _auth_headers(self, client):
        """Register a test user and return auth headers."""
        client.post(
            "/api/v1/auth/register",
            json={"email": "assess-test@example.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "assess-test@example.com", "password": "pass123"},
        )
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def _setup_applicant_with_rules(self, client, headers):
        """Create an applicant and seed ACTIVE rules, return applicant_id."""
        from db.connection import get_session
        from db.models import Base
        from db.repositories import ProgramRepo, CountryRepo

        # Create applicant
        resp = client.post(
            "/api/v1/applicants",
            json={"age": 30, "english_level": "competent", "points": 65},
            headers=headers,
        )
        applicant_id = resp.json()["applicant_id"]

        # Seed rules into DB
        with get_session(client.app.state.db_engine) as session:
            # Ensure program exists
            country_repo = CountryRepo(session)
            country_repo.upsert("AU", "Australia", "AU")
            program_repo = ProgramRepo(session)
            program_repo.upsert(
                "SKILLED_189", "AU", "Skilled Independent", "189",
                "Skilled Independent visa",
            )

            from phase3.rule_repository import EnhancedRuleRepo
            rule_repo = EnhancedRuleRepo(session)

            # Create two ACTIVE rules
            rule_repo.create(
                rule_id="RULE-AGE-189",
                requirement_id="REQ-AGE-189",
                program_id="SKILLED_189",
                category="age",
                field="age",
                operator="LT",
                value_json="45",
                mandatory=True,
                critical=True,
                message="Age must be under 45",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=1,
                evidence_text="Applicants must be under 45 years of age",
            )
            rule_repo.create(
                rule_id="RULE-POINTS-189",
                requirement_id="REQ-POINTS-189",
                program_id="SKILLED_189",
                category="points",
                field="points",
                operator="GTE",
                value_json="65",
                mandatory=True,
                critical=False,
                message="Must score at least 65 points",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=2,
                evidence_text="Minimum 65 points on the points test",
            )
            session.commit()

        return applicant_id

    def test_create_assessment_real(self, client):
        """POST /assessments returns 201 with real eligibility result."""
        headers = self._auth_headers(client)
        applicant_id = self._setup_applicant_with_rules(client, headers)

        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["business_status"] == "ELIGIBLE"
        assert data["overall_status"] == "PASS"
        assert data["total_rules"] == 2
        assert data["passed_count"] == 2
        assert data["failed_count"] == 0
        assert len(data["rule_results"]) == 2
        assert "assessment_id" in data

    def test_create_assessment_incomplete(self, client):
        """Assessment is INCOMPLETE when applicant data is missing."""
        from db.connection import get_session
        from db.repositories import ProgramRepo, CountryRepo
        from phase3.rule_repository import EnhancedRuleRepo

        headers = self._auth_headers(client)

        # Create applicant with no data
        resp = client.post(
            "/api/v1/applicants",
            json={},
            headers=headers,
        )
        applicant_id = resp.json()["applicant_id"]

        # Seed a rule requiring english_level
        with get_session(client.app.state.db_engine) as session:
            CountryRepo(session).upsert("AU", "Australia", "AU")
            ProgramRepo(session).upsert(
                "SKILLED_189", "AU", "Skilled Independent", "189",
                "Skilled Independent visa",
            )
            rule_repo = EnhancedRuleRepo(session)
            rule_repo.create(
                rule_id="RULE-ENGLISH-189",
                requirement_id="REQ-ENGLISH-189",
                program_id="SKILLED_189",
                category="english",
                field="english_level",
                operator="IN",
                value_json='["competent","proficient","superior"]',
                mandatory=True,
                critical=True,
                message="Must have competent English or higher",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=1,
                evidence_text="Competent English required",
            )
            session.commit()

        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["business_status"] == "INCOMPLETE"
        assert data["unknown_count"] >= 1
        assert len(data["gaps"]) >= 1

    def test_create_assessment_ineligible(self, client):
        """Assessment is INELIGIBLE when a mandatory rule fails."""
        from db.connection import get_session
        from db.repositories import ProgramRepo, CountryRepo
        from phase3.rule_repository import EnhancedRuleRepo

        headers = self._auth_headers(client)

        # Create applicant with age 50 (over limit)
        resp = client.post(
            "/api/v1/applicants",
            json={"age": 50},
            headers=headers,
        )
        applicant_id = resp.json()["applicant_id"]

        # Seed age rule: age < 45
        with get_session(client.app.state.db_engine) as session:
            CountryRepo(session).upsert("AU", "Australia", "AU")
            ProgramRepo(session).upsert(
                "SKILLED_189", "AU", "Skilled Independent", "189",
                "Skilled Independent visa",
            )
            rule_repo = EnhancedRuleRepo(session)
            rule_repo.create(
                rule_id="RULE-AGE-189",
                requirement_id="REQ-AGE-189",
                program_id="SKILLED_189",
                category="age",
                field="age",
                operator="LT",
                value_json="45",
                mandatory=True,
                critical=True,
                message="Age must be under 45",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=1,
                evidence_text="Under 45 required",
            )
            session.commit()

        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["business_status"] == "INELIGIBLE"
        assert data["failed_count"] >= 1

    def test_create_assessment_no_rules(self, client):
        """Returns 400 when no ACTIVE rules exist for the program."""
        headers = self._auth_headers(client)
        resp = client.post(
            "/api/v1/applicants",
            json={"age": 30},
            headers=headers,
        )
        applicant_id = resp.json()["applicant_id"]

        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "No ACTIVE rules" in resp.json()["detail"]

    def test_create_assessment_applicant_not_found(self, client):
        """Returns 400 when applicant doesn't exist."""
        headers = self._auth_headers(client)

        # Seed rules
        from db.connection import get_session
        from db.repositories import ProgramRepo, CountryRepo
        from phase3.rule_repository import EnhancedRuleRepo

        with get_session(client.app.state.db_engine) as session:
            CountryRepo(session).upsert("AU", "Australia", "AU")
            ProgramRepo(session).upsert(
                "SKILLED_189", "AU", "Skilled Independent", "189",
                "Skilled Independent visa",
            )
            rule_repo = EnhancedRuleRepo(session)
            rule_repo.create(
                rule_id="RULE-AGE-189",
                requirement_id="REQ-AGE-189",
                program_id="SKILLED_189",
                category="age",
                field="age",
                operator="LT",
                value_json="45",
                mandatory=True,
                critical=True,
                message="Age must be under 45",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=1,
                evidence_text="Under 45",
            )
            session.commit()

        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": "nonexistent", "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]

    def test_get_assessment(self, client):
        """GET /assessments/{id} returns persisted assessment."""
        headers = self._auth_headers(client)
        applicant_id = self._setup_applicant_with_rules(client, headers)

        # Create
        create_resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assessment_id = create_resp.json()["assessment_id"]

        # Get
        resp = client.get(f"/api/v1/assessments/{assessment_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["assessment_id"] == assessment_id
        assert data["business_status"] == "ELIGIBLE"
        assert len(data["rule_results"]) == 2

    def test_list_assessments(self, client):
        """GET /assessments?applicant_id= lists assessments."""
        headers = self._auth_headers(client)
        applicant_id = self._setup_applicant_with_rules(client, headers)

        client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )

        resp = client.get(
            "/api/v1/assessments",
            params={"applicant_id": applicant_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["assessments"][0]["business_status"] == "ELIGIBLE"

    def test_evaluate_inline_real(self, client):
        """POST /assessments/evaluate returns real result without persistence."""
        headers = self._auth_headers(client)
        applicant_id = self._setup_applicant_with_rules(client, headers)

        resp = client.post(
            "/api/v1/assessments/evaluate",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["business_status"] == "ELIGIBLE"
        assert data["total_rules"] == 2


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


# ---------------------------------------------------------------------------
# PostgreSQL code paths + resilience
# ---------------------------------------------------------------------------

class TestPostgreSQLCodePaths:
    def test_pg_engine_uses_connection_pool(self):
        """PostgreSQL URL creates pooled engine (not StaticPool)."""
        from db.connection import get_engine
        eng = get_engine("postgresql://user:pass@localhost:5432/testdb")
        from sqlalchemy.pool import QueuePool
        assert eng.pool.__class__ is QueuePool
        assert eng.pool.size() == 5
        assert eng.pool._max_overflow == 10

    def test_sqlite_engine_uses_static_pool(self):
        """SQLite URL creates StaticPool for thread safety."""
        from db.connection import get_engine
        eng = get_engine("sqlite:///:memory:")
        from sqlalchemy.pool import StaticPool
        assert eng.pool.__class__ is StaticPool

    def test_pg_engine_pool_pre_ping(self):
        """PostgreSQL engine has pool_pre_ping enabled."""
        from db.connection import get_engine
        eng = get_engine("postgresql://user:pass@localhost:5432/testdb")
        assert eng.pool._pre_ping is True or getattr(eng.pool, 'pre_ping', None) is True

    def test_get_db_engine_returns_engine(self):
        """get_db_engine returns the engine from app.state."""
        from db.connection import get_db_engine
        from unittest.mock import MagicMock
        request = MagicMock()
        request.app.state.db_engine = "fake_engine"
        result = get_db_engine(request)
        assert result == "fake_engine"

    def test_get_db_engine_raises_503_when_none(self):
        """get_db_engine raises HTTP 503 when db_engine is None."""
        from db.connection import get_db_engine
        from unittest.mock import MagicMock
        from fastapi import HTTPException
        request = MagicMock()
        request.app.state.db_engine = None
        with pytest.raises(HTTPException) as exc_info:
            get_db_engine(request)
        assert exc_info.value.status_code == 503
        assert "Database not available" in exc_info.value.detail

    def test_get_db_engine_raises_503_when_missing(self):
        """get_db_engine raises HTTP 503 when db_engine attr is missing."""
        from db.connection import get_db_engine
        from unittest.mock import MagicMock
        from fastapi import HTTPException
        request = MagicMock()
        del request.app.state.db_engine
        with pytest.raises(HTTPException) as exc_info:
            get_db_engine(request)
        assert exc_info.value.status_code == 503

    def test_api_routes_return_503_without_db(self):
        """API routes that need DB return 503 when engine is None."""
        from api.main import create_app
        from services.auth_service import create_access_token
        app2 = create_app()
        app2.state.db_engine = None
        with TestClient(app2, raise_server_exceptions=False) as c:
            token_data = create_access_token(user_id="test-user", role="applicant")
            resp = c.get(
                "/api/v1/applicants",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            assert resp.status_code == 503


class TestApplicantSnapshot:
    def _auth_headers(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "snap-test@example.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "snap-test@example.com", "password": "pass123"},
        )
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def _setup_applicant_with_rules(self, client, headers):
        from db.connection import get_session
        from db.repositories import ProgramRepo, CountryRepo
        from phase3.rule_repository import EnhancedRuleRepo

        resp = client.post(
            "/api/v1/applicants",
            json={"age": 30, "english_level": "competent", "points": 65},
            headers=headers,
        )
        applicant_id = resp.json()["applicant_id"]

        with get_session(client.app.state.db_engine) as session:
            CountryRepo(session).upsert("AU", "Australia", "AU")
            ProgramRepo(session).upsert(
                "SKILLED_189", "AU", "Skilled Independent", "189",
                "Skilled Independent visa",
            )
            rule_repo = EnhancedRuleRepo(session)
            rule_repo.create(
                rule_id="RULE-AGE-189",
                requirement_id="REQ-AGE-189",
                program_id="SKILLED_189",
                category="age",
                field="age",
                operator="LT",
                value_json="45",
                mandatory=True,
                critical=True,
                message="Age must be under 45",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=1,
                evidence_text="Under 45 required",
            )
            rule_repo.create(
                rule_id="RULE-POINTS-189",
                requirement_id="REQ-POINTS-189",
                program_id="SKILLED_189",
                category="points",
                field="points",
                operator="GTE",
                value_json="65",
                mandatory=True,
                critical=False,
                message="Must score at least 65 points",
                status="ACTIVE",
                current_version=1,
                source_id="SRC-AU-189-MAIN",
                document_id="DOC-AU-189-MAIN",
                document_version=1,
                chunk_id=2,
                evidence_text="Minimum 65 points",
            )
            session.commit()

        return applicant_id

    def test_assessment_stores_snapshot(self, client):
        """Assessment persists applicant snapshot at time of assessment."""
        headers = self._auth_headers(client)
        applicant_id = self._setup_applicant_with_rules(client, headers)

        resp = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        snapshot = data.get("applicant_snapshot")
        assert snapshot is not None
        assert isinstance(snapshot, dict)
        assert "age" in snapshot
        assert "english_level" in snapshot
        assert snapshot["age"] == 30
        assert snapshot["english_level"] == "competent"

    def test_assessment_snapshot_immutable_after_creation(self, client):
        """Changing applicant profile after assessment doesn't affect stored snapshot."""
        headers = self._auth_headers(client)
        applicant_id = self._setup_applicant_with_rules(client, headers)

        resp1 = client.post(
            "/api/v1/assessments",
            json={"applicant_id": applicant_id, "program_id": "SKILLED_189"},
            headers=headers,
        )
        assert resp1.status_code == 201
        assessment_id1 = resp1.json()["assessment_id"]
        snapshot1 = resp1.json()["applicant_snapshot"]

        client.patch(
            f"/api/v1/applicants/{applicant_id}",
            json={"age": 35},
            headers=headers,
        )

        resp2 = client.get(f"/api/v1/assessments/{assessment_id1}")
        assert resp2.status_code == 200
        snapshot2 = resp2.json()["applicant_snapshot"]
        assert snapshot2["age"] == 30
