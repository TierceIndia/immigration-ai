"""Item 4: review-queue triage - evaluable_only filter (default on), the
evaluable badge, and in-scope-program-first / mandatory / criticality sort.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, ExtractionRun, Requirement, RequirementEvidence,
)
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


@pytest.fixture()
def admin_headers(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "evaluable-filter-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(client.app.state.db_engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "evaluable-filter-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "evaluable-filter-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed_program(session, program_id):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id=program_id, country_id="AU", visa_category="SKILLED",
        subclass_code=None, name=program_id, status="ACTIVE",
    ))
    source_id = f"SRC-{program_id}"
    session.merge(GovernmentSource(
        source_id=source_id, country_id="AU", program_id=program_id,
        visa_category="SKILLED", authority="Home Affairs",
        url=f"https://example.gov.au/{program_id.lower()}", source_type="HTML",
        priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    document_id = f"DOC-{program_id}"
    session.merge(GovernmentDocument(
        document_id=document_id, source_id=source_id, country_id="AU",
        program_id=program_id, visa_category="SKILLED", document_type="GENERAL",
        url=f"https://example.gov.au/{program_id.lower()}", title=program_id,
        retrieved_at=datetime.now(timezone.utc), content_hash=f"hash-{program_id}",
        current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
    ))
    session.flush()
    session.merge(DocumentVersion(
        document_id=document_id, version_number=1, content_hash=f"hash-{program_id}",
        retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion).filter_by(document_id=document_id, version_number=1).one().version_id
    )
    run_id = f"RUN-{program_id}"
    session.add(ExtractionRun(
        run_id=run_id, document_id=document_id, document_version=1,
        document_version_id=version_id, model="mock", prompt_version="requirement_extraction_v3",
        status="COMPLETED",
    ))
    session.flush()
    session.commit()
    return run_id, version_id


def _make_requirement(session, requirement_id, program_id, run_id, version_id, *,
                       field, concept=None, mandatory=True, criticality="NORMAL",
                       status="NEEDS_REVIEW"):
    req = Requirement(
        requirement_id=requirement_id, semantic_key=requirement_id,
        program_id=program_id, country_id="AU", category="AGE",
        name=requirement_id, description="test requirement",
        field=field, concept=concept, operator="LT", value_json=json.dumps(45),
        unit="years", mandatory=1 if mandatory else 0, confidence=0.95,
        criticality=criticality, status=status, current_version=1,
        extraction_run_id=run_id, document_version_id=version_id,
    )
    session.add(req)
    session.flush()
    session.add(RequirementEvidence(
        requirement_id=requirement_id, document_id=f"DOC-{program_id}",
        document_version=1, chunk_id=1, evidence_text="evidence",
        evidence_type="DIRECT_TEXT", extracted_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return req


class TestEvaluableOnlyFilter:
    def test_defaults_to_true_and_hides_not_modeled(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run_id, version_id = _seed_program(s, "SKILLED_189")
            _make_requirement(s, "REQ-EVAL", "SKILLED_189", run_id, version_id, field="applicant.age")
            _make_requirement(s, "REQ-NOTMODELED", "SKILLED_189", run_id, version_id,
                               field="NOT_MODELED", concept="visa_history")

        resp = client.get("/api/v1/admin/requirements/review-queue", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["evaluable_only"] is True
        ids = {r["requirement_id"] for r in body["requirements"]}
        assert ids == {"REQ-EVAL"}

    def test_evaluable_badge_is_present_on_every_item(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run_id, version_id = _seed_program(s, "SKILLED_189")
            _make_requirement(s, "REQ-EVAL", "SKILLED_189", run_id, version_id, field="applicant.age")

        resp = client.get("/api/v1/admin/requirements/review-queue", headers=admin_headers)
        item = resp.json()["requirements"][0]
        assert item["evaluable"] is True
        assert item["field"] == "applicant.age"

    def test_toggle_reaches_the_hidden_rows(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run_id, version_id = _seed_program(s, "SKILLED_189")
            _make_requirement(s, "REQ-NOTMODELED", "SKILLED_189", run_id, version_id,
                               field="NOT_MODELED", concept="visa_history")

        resp = client.get(
            "/api/v1/admin/requirements/review-queue",
            params={"evaluable_only": "false"},
            headers=admin_headers,
        )
        body = resp.json()
        assert body["evaluable_only"] is False
        item = body["requirements"][0]
        assert item["requirement_id"] == "REQ-NOTMODELED"
        assert item["evaluable"] is False
        assert item["concept"] == "visa_history"

    def test_blank_field_is_also_hidden_by_default(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run_id, version_id = _seed_program(s, "SKILLED_189")
            _make_requirement(s, "REQ-BLANK", "SKILLED_189", run_id, version_id, field=None)

        resp = client.get("/api/v1/admin/requirements/review-queue", headers=admin_headers)
        assert resp.json()["requirements"] == []


class TestReviewQueueSortOrder:
    def test_in_scope_program_sorts_before_out_of_scope(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run1, v1 = _seed_program(s, "SKILLED_189")
            run2, v2 = _seed_program(s, "LEGISLATION_MIGRATION_REGS")
            _make_requirement(s, "REQ-OUT-OF-SCOPE", "LEGISLATION_MIGRATION_REGS", run2, v2,
                               field="applicant.age")
            _make_requirement(s, "REQ-IN-SCOPE", "SKILLED_189", run1, v1, field="applicant.age")

        resp = client.get("/api/v1/admin/requirements/review-queue", headers=admin_headers)
        ids = [r["requirement_id"] for r in resp.json()["requirements"]]
        assert ids == ["REQ-IN-SCOPE", "REQ-OUT-OF-SCOPE"]

    def test_mandatory_sorts_before_optional_within_the_same_scope(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run_id, version_id = _seed_program(s, "SKILLED_189")
            _make_requirement(s, "REQ-OPTIONAL", "SKILLED_189", run_id, version_id,
                               field="applicant.age", mandatory=False)
            _make_requirement(s, "REQ-MANDATORY", "SKILLED_189", run_id, version_id,
                               field="applicant.english_level", mandatory=True)

        resp = client.get("/api/v1/admin/requirements/review-queue", headers=admin_headers)
        ids = [r["requirement_id"] for r in resp.json()["requirements"]]
        assert ids == ["REQ-MANDATORY", "REQ-OPTIONAL"]

    def test_criticality_breaks_ties_within_mandatory_and_scope(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            run_id, version_id = _seed_program(s, "SKILLED_189")
            _make_requirement(s, "REQ-NORMAL", "SKILLED_189", run_id, version_id,
                               field="applicant.age", mandatory=True, criticality="NORMAL")
            _make_requirement(s, "REQ-CRITICAL", "SKILLED_189", run_id, version_id,
                               field="applicant.english_level", mandatory=True, criticality="CRITICAL")

        resp = client.get("/api/v1/admin/requirements/review-queue", headers=admin_headers)
        ids = [r["requirement_id"] for r in resp.json()["requirements"]]
        assert ids == ["REQ-CRITICAL", "REQ-NORMAL"]
