"""API tests for the admin rule checker and the not-evaluable guard (Phase 4b).

The checker endpoint is a dry run: it must answer the admin's question
without changing anything. The activation guard must make an
unanswerable rule hard to activate by accident.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api.main import app
from db.connection import get_session_factory
from db.models import Base, Rule as RuleORM


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
def admin_headers(client, engine):
    client.post(
        "/api/v1/auth/register",
        json={"email": "admin@test.com", "password": "admin123", "full_name": "Admin"},
    )
    with get_session_factory(engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_rule(session, rule_id="R-1", status="DRAFT", program_id="SKILLED_189",
                 category="AGE", field="age", operator="GTE", value=25,
                 mandatory=True, critical=False):
    obj = RuleORM(
        rule_id=rule_id,
        requirement_id=f"REQ-{rule_id}",
        program_id=program_id,
        category=category,
        field=field,
        operator=operator,
        value_json=json.dumps(value),
        unit="years",
        mandatory=mandatory,
        critical=critical,
        message="Must be at least 25",
        status=status,
        current_version=1,
        evidence_text="Applicants must be at least 25",
    )
    session.add(obj)
    session.commit()
    return obj


class TestTheRuleCheckerEndpoint:
    def test_a_non_admin_is_refused(self, client, engine):
        client.post(
            "/api/v1/auth/register",
            json={"email": "user@test.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "user@test.com", "password": "pass123"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {}},
            headers=headers,
        )
        assert out.status_code == 403

    def test_an_unauthenticated_call_is_refused(self, client):
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {}},
        )
        assert out.status_code == 401

    def test_a_passing_profile_passes(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {"age": 30}},
            headers=admin_headers,
        )
        assert out.status_code == 200
        data = out.json()
        assert data["overall_status"] == "PASS"
        assert data["business_status"] == "ELIGIBLE"
        assert data["passed"] == 1
        assert data["results"][0]["status"] == "PASS"
        assert data["results"][0]["explanation"]

    def test_a_failing_profile_fails(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {"age": 20}},
            headers=admin_headers,
        )
        data = out.json()
        assert data["overall_status"] == "FAIL"
        assert data["business_status"] == "INELIGIBLE"

    def test_a_missing_fact_is_unknown_and_becomes_a_gap(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {}},
            headers=admin_headers,
        )
        data = out.json()
        assert data["overall_status"] == "UNKNOWN"
        assert data["business_status"] == "INCOMPLETE"
        assert len(data["gaps"]) == 1
        assert data["gaps"][0]["question"]

    def test_the_response_carries_the_ruleset_hash(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {"age": 30}},
            headers=admin_headers,
        )
        assert len(out.json()["ruleset_hash"]) == 16

    def test_a_not_evaluable_rule_is_flagged(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="applicant.financial_capacity",
                         operator="GTE", value=50000)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {"age": 30}},
            headers=admin_headers,
        )
        result = out.json()["results"][0]
        assert result["evaluable"] is False
        assert "never resolve to PASS or FAIL" in result["explanation"]

    def test_only_active_rules_are_checked_by_default(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)
            _create_rule(s, "R-2", status="DRAFT", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {"age": 30}},
            headers=admin_headers,
        )
        data = out.json()
        assert data["total_rules"] == 1
        assert data["results"][0]["rule_id"] == "R-1"

    def test_a_named_rule_can_be_checked_before_it_is_activated(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": "SKILLED_189",
                "profile": {"age": 30},
                "rule_ids": ["R-1"],
            },
            headers=admin_headers,
        )
        assert out.json()["total_rules"] == 1
        assert out.json()["results"][0]["status"] == "PASS"

    def test_an_unknown_rule_id_is_a_404(self, client, engine, admin_headers):
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {}, "rule_ids": ["NOPE"]},
            headers=admin_headers,
        )
        assert out.status_code == 404

    def test_a_program_with_no_active_rules_is_a_404(self, client, engine, admin_headers):
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_190", "profile": {}},
            headers=admin_headers,
        )
        assert out.status_code == 404

    def test_the_check_persists_nothing(self, client, engine, admin_headers):
        from db.models import Rule as RuleORM
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)

        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "SKILLED_189", "profile": {"age": 30}},
            headers=admin_headers,
        )
        assert out.status_code == 200

        # No new rules, no assessments, no audit rows: a dry run is a dry run.
        with get_session_factory(engine)() as s:
            assert s.query(RuleORM).count() == 1
            assert s.query(RuleORM).filter(RuleORM.rule_id == "R-1").first().status == "ACTIVE"

    def test_an_unknown_profile_key_is_ignored(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="ACTIVE", field="age", value=25)
        out = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": "SKILLED_189",
                "profile": {"age": 30, "not_a_real_fact": "surprise"},
            },
            headers=admin_headers,
        )
        assert out.status_code == 200
        assert out.json()["results"][0]["actual_value"] == 30


class TestActivatingANotEvaluableRule:
    def test_it_is_refused_without_a_typed_confirmation(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/R-1/activate",
            json={},
            headers=admin_headers,
        )
        assert out.status_code == 400
        assert "not evaluable" in out.json()["detail"]
        assert "ACTIVATE" in out.json()["detail"]

    def test_the_wrong_confirmation_is_refused(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/R-1/activate",
            json={"confirm_not_evaluable": "yes"},
            headers=admin_headers,
        )
        assert out.status_code == 400

    def test_the_right_confirmation_is_accepted(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/R-1/activate",
            json={"confirm_not_evaluable": "ACTIVATE"},
            headers=admin_headers,
        )
        assert out.status_code == 200
        assert out.json()["status"] == "ACTIVE"

    def test_an_evaluable_rule_needs_no_confirmation(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="age")
        out = client.post(
            "/api/v1/admin/rules/R-1/activate",
            json={},
            headers=admin_headers,
        )
        assert out.status_code == 200
        assert out.json()["status"] == "ACTIVE"

    def test_the_batch_route_has_the_same_guard(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/batch/activate",
            json={"rule_ids": ["R-1"]},
            headers=admin_headers,
        )
        assert out.status_code == 400
        assert "not evaluable" in out.json()["detail"]

    def test_the_batch_route_accepts_the_confirmation(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/batch/activate",
            json={"rule_ids": ["R-1"], "confirm_not_evaluable": "ACTIVATE"},
            headers=admin_headers,
        )
        assert out.status_code == 200
        assert out.json()[0]["status"] == "ACTIVE"

    def test_one_not_evaluable_rule_blocks_the_whole_batch(self, client, engine, admin_headers):
        # The guard is on the batch, not on each rule: a batch that mixes an
        # answerable rule with an unanswerable one still needs the typed
        # confirmation.
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="age")
            _create_rule(s, "R-2", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/batch/activate",
            json={"rule_ids": ["R-1", "R-2"]},
            headers=admin_headers,
        )
        assert out.status_code == 400

    def test_the_guard_refuses_before_anything_is_activated(self, client, engine, admin_headers):
        from db.models import Rule as RuleORM
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="APPROVED", field="age")
            _create_rule(s, "R-2", status="APPROVED", field="applicant.financial_capacity")
        out = client.post(
            "/api/v1/admin/rules/batch/activate",
            json={"rule_ids": ["R-1", "R-2"]},
            headers=admin_headers,
        )
        assert out.status_code == 400
        with get_session_factory(engine)() as s:
            # R-1 was answerable, but the batch was refused as a whole - a
            # partially applied batch would leave the admin guessing which
            # half took effect.
            assert s.query(RuleORM).filter(RuleORM.rule_id == "R-1").first().status == "APPROVED"


class TestTheEvaluableFlagIsVisible:
    def test_the_rule_list_carries_it(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT", field="age")
            _create_rule(s, "R-2", status="DRAFT", field="applicant.financial_capacity")
        out = client.get("/api/v1/admin/rules", headers=admin_headers)
        by_id = {r["rule_id"]: r for r in out.json()}
        assert by_id["R-1"]["evaluable"] is True
        assert by_id["R-2"]["evaluable"] is False

    def test_the_review_queue_carries_it(self, client, engine, admin_headers):
        with get_session_factory(engine)() as s:
            _create_rule(s, "R-1", status="DRAFT", field="applicant.financial_capacity")
        out = client.get("/api/v1/admin/rules/queue", headers=admin_headers)
        assert out.json()["drafts"][0]["evaluable"] is False
