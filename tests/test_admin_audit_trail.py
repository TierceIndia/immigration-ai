"""Tests for Part 1e: the rule/requirement review audit trail must always
record the AUTHENTICATED admin, never a client-supplied actor/reviewer
string.

Before Part 1e, every rule lifecycle action (validate/approve/reject/
activate/retire, single and batch) and every requirement review action
(approve/reject/mark-needs-review) persisted whatever free-text string the
client put in the request body's actor/reviewer field - despite the route
itself requiring admin auth, the identity recorded in rule_versions.created_by
/ requirement_review.reviewer could be spoofed to anyone's name. Part 1e made
the field ignored; a later pass removed it from the request schema entirely
(it is no longer a field FastAPI/Pydantic even recognizes), so these tests
now also prove that sending it anyway - an old client, or a probe - is
harmlessly dropped as an unrecognized key rather than rejected or honoured.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base, Rule as RuleORM, RuleVersion, RequirementReview
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
        json={"email": "audit-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(client.app.state.db_engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "audit-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "audit-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_rule(engine, rule_id="R-AUDIT-1", status="DRAFT"):
    with get_session_factory(engine)() as s:
        obj = RuleORM(
            rule_id=rule_id,
            requirement_id=f"REQ-{rule_id}",
            program_id="SKILLED_189",
            category="AGE",
            field="age",
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
        s.add(obj)
        s.commit()
    return rule_id


def _create_requirement(engine, requirement_id="REQ-AUDIT-1", status="NEEDS_REVIEW"):
    from db.models import ExtractionRun, Requirement
    with get_session_factory(engine)() as s:
        s.add(ExtractionRun(
            run_id="RUN-AUDIT-1", document_id="DOC-AUDIT-1", document_version=1,
            model="mock", prompt_version="v1", status="COMPLETED",
        ))
        s.flush()
        s.add(Requirement(
            requirement_id=requirement_id,
            semantic_key="audit-semkey",
            program_id="SKILLED_189",
            country_id="AU",
            category="AGE",
            name="Age requirement",
            field="applicant.age",
            operator="LT",
            value_json=json.dumps(45),
            unit="years",
            mandatory=1,
            status=status,
            current_version=1,
            extraction_run_id="RUN-AUDIT-1",
        ))
        s.commit()
    return requirement_id


class TestRuleAuditTrailIsServerDerived:
    def test_validate_records_authenticated_admin_not_spoofed_actor(self, client, admin_headers, engine):
        rule_id = _create_rule(engine, "R-AUDIT-VALIDATE", "DRAFT")
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/validate",
            json={"actor": "totally-not-the-real-admin"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            version = s.query(RuleVersion).filter_by(rule_id=rule_id).one()
            assert version.created_by == "audit-admin@test.com"
            assert version.created_by != "totally-not-the-real-admin"

    def test_validate_works_even_with_no_actor_field_at_all(self, client, admin_headers, engine):
        """actor is now optional - omitting it entirely must still work."""
        rule_id = _create_rule(engine, "R-AUDIT-NOACTOR", "DRAFT")
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/validate", json={}, headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            version = s.query(RuleVersion).filter_by(rule_id=rule_id).one()
            assert version.created_by == "audit-admin@test.com"

    def test_approve_records_authenticated_admin(self, client, admin_headers, engine):
        rule_id = _create_rule(engine, "R-AUDIT-APPROVE", "VALIDATED")
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/approve",
            json={"actor": "spoofed-name"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            versions = s.query(RuleVersion).filter_by(rule_id=rule_id).all()
            assert all(v.created_by == "audit-admin@test.com" for v in versions)

    def test_activate_records_authenticated_admin(self, client, admin_headers, engine):
        rule_id = _create_rule(engine, "R-AUDIT-ACTIVATE", "APPROVED")
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/activate",
            json={"actor": "spoofed-name"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            version = s.query(RuleVersion).filter_by(
                rule_id=rule_id, change_type="ACTIVATED",
            ).one()
            assert version.created_by == "audit-admin@test.com"

    def test_batch_approve_records_authenticated_admin_for_every_rule(self, client, admin_headers, engine):
        r1 = _create_rule(engine, "R-AUDIT-BATCH-1", "VALIDATED")
        r2 = _create_rule(engine, "R-AUDIT-BATCH-2", "VALIDATED")
        resp = client.post(
            "/api/v1/admin/rules/batch/approve",
            json={"rule_ids": [r1, r2], "actor": "spoofed-batch-actor"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            versions = s.query(RuleVersion).filter(
                RuleVersion.rule_id.in_([r1, r2]), RuleVersion.change_type == "APPROVED",
            ).all()
            assert len(versions) == 2
            assert all(v.created_by == "audit-admin@test.com" for v in versions)


class TestRequirementAuditTrailIsServerDerived:
    def test_approve_records_authenticated_admin_not_spoofed_reviewer(self, client, admin_headers, engine):
        req_id = _create_requirement(engine, "REQ-AUDIT-APPROVE")
        resp = client.post(
            f"/api/v1/admin/requirements/review/{req_id}/approve",
            json={"requirement_id": req_id, "reviewer": "totally-not-the-real-admin",
                  "action": "APPROVE"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            review = s.query(RequirementReview).filter_by(requirement_id=req_id).one()
            assert review.reviewer == "audit-admin@test.com"
            assert review.reviewer != "totally-not-the-real-admin"

    def test_reject_works_even_with_no_reviewer_field_at_all(self, client, admin_headers, engine):
        req_id = _create_requirement(engine, "REQ-AUDIT-NOREV")
        resp = client.post(
            f"/api/v1/admin/requirements/review/{req_id}/reject",
            json={"requirement_id": req_id, "action": "REJECT"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        with get_session_factory(engine)() as s:
            review = s.query(RequirementReview).filter_by(requirement_id=req_id).one()
            assert review.reviewer == "audit-admin@test.com"


class TestActorReviewerFieldsAreFullyRemoved:
    """actor/reviewer went from "ignored but present" (Part 1e) to simply
    gone - this proves removal, not just non-effect."""

    def test_actor_is_not_in_the_openapi_schema_for_rule_actions(self, client):
        schema = client.get("/openapi.json").json()
        rule_action = schema["components"]["schemas"]["RuleActionRequest"]
        assert "actor" not in rule_action.get("properties", {})
        batch_action = schema["components"]["schemas"]["BatchActionRequest"]
        assert "actor" not in batch_action.get("properties", {})

    def test_reviewer_is_not_in_the_openapi_schema_for_requirement_review(self, client):
        schema = client.get("/openapi.json").json()
        review_request = schema["components"]["schemas"]["RequirementReviewRequest"]
        assert "reviewer" not in review_request.get("properties", {})
