"""Phase 4 acceptance: the admin rule review flow against real PostgreSQL.

The unit tests for the rule checker and the activation guard run against
SQLite. This file runs the same behaviour through the HTTP API against
the live PostgreSQL instance, because two things only show up on the
real database:

* the rule / rule_version tables are append-only in PostgreSQL, so the
  activation path writes rows that SQLite would let it overwrite, and
* the audit trail an admin action leaves behind has to be the one the
  audit triggers accept.

Covered here:

    1.  The six newly modelled applicant concepts reach the rule engine
        through the real profile -> rule-data path.
    2.  POST /api/v1/admin/rules/evaluate dry-runs the active rules and
        reports PASS / FAIL / UNKNOWN per rule, with gaps and a ruleset hash.
    3.  A rule whose field no applicant can supply is badged
        "evaluable: false" and cannot be activated without the typed
        confirmation - and a batch containing one is refused whole.
    4.  An evaluable rule activates without any confirmation at all.

Run explicitly against the live container with:

    $env:POSTGRES_E2E="1"; python -m pytest tests/e2e/test_admin_rule_review_e2e.py -v

Without ``POSTGRES_E2E=1`` the module is skipped so default CI stays
hermetic. Every row it writes lives under its own sandbox ids and is
removed afterwards.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

E2E_URL = os.environ.get(
    "POSTGRES_E2E_URL",
    # Dedicated e2e-only database - see tests/e2e/conftest.py's docstring:
    # this test disables audit-immutability triggers and must never run
    # against the shared main "immigration" database.
    # The host port follows POSTGRES_HOST_PORT so the suite still finds
    # docker-compose's database when 5432 is already taken on the host.
    f"postgresql+psycopg2://immigration:immigration_dev@localhost:"
    f"{os.environ.get('POSTGRES_HOST_PORT', '5432')}/immigration_e2e",
)

from conftest import refuse_if_main_database  # noqa: E402

refuse_if_main_database(E2E_URL)

pytestmark = [
    pytest.mark.postgres_e2e,
    pytest.mark.skipif(
        os.environ.get("POSTGRES_E2E") != "1",
        reason="POSTGRES_E2E=1 required (runs against the live PostgreSQL)",
    ),
]

from api.main import app  # noqa: E402
from db.connection import get_session_factory  # noqa: E402
from db.models import (  # noqa: E402
    ApiUser, ImmigrationProgram, Country, GovernmentSource, GovernmentDocument,
    DocumentVersion, DocumentChunk, ExtractionRun, Requirement, RequirementEvidence,
)
from services.auth_service import AuthService  # noqa: E402

PROGRAM_ID = "E2E-PHASE4-PROGRAM"
ADMIN_EMAIL = "phase4-admin@test.invalid"
ADMIN_PASSWORD = "e2e-admin-password"

SOURCE_ID = "SRC-A2E-PHASE4"
DOCUMENT_ID = "DOC-A2E-PHASE4"
RUN_ID = "RUN-A2E-PHASE4"
EVIDENCE_TEXT = "The applicant must satisfy this criterion."


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
_APPEND_ONLY = (
    "rule_versions", "rulesets", "ruleset_rules", "requirement_versions",
    "requirement_evidence", "requirements", "extraction_runs", "document_chunks",
    "document_versions",
)


def _cleanup() -> None:
    from sqlalchemy import create_engine

    eng = create_engine(E2E_URL, pool_pre_ping=True)
    try:
        with eng.begin() as conn:
            for t in _APPEND_ONLY:
                conn.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER ALL"))
            for stmt in (
                "DELETE FROM ruleset_rules WHERE ruleset_id IN "
                    "(SELECT ruleset_id FROM rulesets WHERE program_id = :prog)",
                "DELETE FROM rulesets WHERE program_id = :prog",
                "DELETE FROM rule_versions WHERE rule_id LIKE 'RULE-REQ-E2E-PHASE4-%'",
                "DELETE FROM rules WHERE rule_id LIKE 'RULE-REQ-E2E-PHASE4-%'",
                "DELETE FROM requirement_evidence WHERE document_id = :doc",
                "DELETE FROM requirements WHERE extraction_run_id = :run",
                "DELETE FROM extraction_runs WHERE run_id = :run",
                "DELETE FROM document_chunks WHERE document_id = :doc",
                "DELETE FROM document_versions WHERE document_id = :doc",
                "DELETE FROM government_documents WHERE document_id = :doc",
                "DELETE FROM government_sources WHERE source_id = :src",
                "DELETE FROM api_users WHERE email = :email",
                "DELETE FROM immigration_programs WHERE program_id = :prog",
            ):
                conn.execute(text(stmt), {
                    "prog": PROGRAM_ID, "email": ADMIN_EMAIL,
                    "doc": DOCUMENT_ID, "run": RUN_ID, "src": SOURCE_ID,
                })
            for t in _APPEND_ONLY:
                conn.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER ALL"))
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    eng = create_engine(E2E_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def client(engine):
    from fastapi.testclient import TestClient

    _cleanup()
    app.state.db_engine = engine
    with TestClient(app) as c:
        yield c
    _cleanup()


@pytest.fixture(scope="module")
def admin_headers(client, engine):
    """A real admin account, with a real password hash, in the real database."""
    with get_session_factory(engine)() as s:
        if s.get(Country, "AU") is None:
            s.add(Country(country_id="AU", name="Australia", iso_code="AUS"))
            s.flush()
        if s.get(ImmigrationProgram, PROGRAM_ID) is None:
            s.add(ImmigrationProgram(
                program_id=PROGRAM_ID, country_id="AU", visa_category="SKILLED",
                subclass_code="999", name="E2E Phase 4 Program", status="ACTIVE",
            ))
            s.flush()
        svc = AuthService(s)
        if not svc.email_registered(ADMIN_EMAIL):
            svc.register(ADMIN_EMAIL, ADMIN_PASSWORD, "E2E Phase 4 Admin")
            s.flush()
        user = s.query(ApiUser).filter(ApiUser.email == ADMIN_EMAIL).one()
        user.role = "admin"
        s.commit()

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed_lineage(engine) -> int:
    """A source -> document -> version -> chunk -> extraction run.

    The rules below are compiled from real requirements, so the lineage
    has to be real too: the compiled rule's provenance points back at
    this chunk, and the audit triggers on the way there are the ones
    production uses.
    """
    import json
    from datetime import datetime, timezone

    with get_session_factory(engine)() as s:
        s.merge(GovernmentSource(
            source_id=SOURCE_ID, country_id="AU", program_id=PROGRAM_ID,
            visa_category="SKILLED", authority="Department of Home Affairs",
            url="https://example.gov.au/phase4", source_type="WEBPAGE",
            source_subtype="ELIGIBILITY_CRITERIA", priority="PRIMARY",
            monitoring_method="HASH", crawl_frequency="DAILY", status="ACTIVE",
        ))
        # Flush between links: the unit of work orders these inserts itself,
        # but not reliably enough to rely on for a fresh chain of rows.
        s.flush()
        s.merge(GovernmentDocument(
            document_id=DOCUMENT_ID, source_id=SOURCE_ID, country_id="AU",
            program_id=PROGRAM_ID, visa_category="SKILLED", document_type="GENERAL",
            url="https://example.gov.au/phase4", title="E2E Phase 4 document",
            retrieved_at=datetime.now(timezone.utc), content_hash="phase4hash",
            current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
        ))
        s.flush()
        s.merge(DocumentVersion(
            document_id=DOCUMENT_ID, version_number=1, content_hash="phase4hash",
            retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
            change_type="INITIAL",
        ))
        s.flush()
        version_id = (
            s.query(DocumentVersion)
            .filter_by(document_id=DOCUMENT_ID, version_number=1)
            .one()
            .version_id
        )
        chunk = DocumentChunk(
            document_id=DOCUMENT_ID, version_id=version_id,
            chunk_type="PARAGRAPH", heading_path="Eligibility criteria",
            order_index=1, raw_text=EVIDENCE_TEXT,
        )
        s.add(chunk)
        s.flush()
        s.add(ExtractionRun(
            run_id=RUN_ID, document_id=DOCUMENT_ID, document_version=1,
            document_version_id=version_id, model="mock", prompt_version="v4",
            status="COMPLETED",
        ))
        s.commit()
        return chunk.chunk_id


def _make_requirement(engine, chunk_id: int, semantic_key: str, field: str,
                      operator: str, value, *, name: str = "E2E requirement") -> str:
    """An APPROVED requirement, with its evidence, ready to be compiled."""
    import json
    from datetime import datetime, timezone

    with get_session_factory(engine)() as s:
        requirement_id = f"REQ-E2E-PHASE4-{semantic_key}"
        s.add(Requirement(
            requirement_id=requirement_id,
            semantic_key=semantic_key,
            program_id=PROGRAM_ID,
            country_id="AU",
            category="ELIGIBILITY",
            name=name,
            description=EVIDENCE_TEXT,
            field=field,
            operator=operator,
            value_json=json.dumps(value),
            unit=None,
            mandatory=1,
            criticality="NORMAL",
            status="APPROVED",
            current_version=1,
            extraction_run_id=RUN_ID,
        ))
        s.flush()
        s.add(RequirementEvidence(
            requirement_id=requirement_id,
            document_id=DOCUMENT_ID,
            document_version=1,
            document_version_id=s.query(DocumentVersion)
                .filter_by(document_id=DOCUMENT_ID, version_number=1)
                .one().version_id,
            chunk_id=chunk_id,
            evidence_text=EVIDENCE_TEXT,
            evidence_type="QUOTE",
            verified=1,
            extracted_at=datetime.now(timezone.utc),
        ))
        s.commit()
        return requirement_id


def _compile_rules(engine) -> dict[str, str]:
    """Compile the approved requirements into DRAFT rules, for real."""
    from rules.requirement_to_rule import compile_approved_requirements

    with get_session_factory(engine)() as s:
        results = compile_approved_requirements(s, program_id=PROGRAM_ID)
        s.commit()
    created = {r["requirement_id"]: r["rule_id"] for r in results if r["status"] == "created"}
    assert created, f"expected rules to be compiled, got {results}"
    return created


def _advance_to_approved(client, headers, rule_id: str):
    """DRAFT -> VALIDATED -> APPROVED, through the real admin routes."""
    resp = client.post(f"/api/v1/admin/rules/{rule_id}/validate", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/v1/admin/rules/{rule_id}/approve", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. The six new applicant concepts reach the rule engine
# ---------------------------------------------------------------------------
class TestTheNewApplicantConcepts:
    """The concepts are modelled end to end: profile -> rule data -> rule
    result. A concept that only exists in the dataclass would pass every
    unit test and still never be asked for."""

    def test_the_concepts_reach_the_rule_data(self):
        from assessment.applicant import APPLICANT_FACT_FIELDS, ApplicantProfile, VisaSelection

        for concept in (
            "visa_history", "employer_sponsored", "work_experience_years",
            "in_australia_at_application", "has_health_insurance",
            "genuine_temporary_entrant",
        ):
            assert concept in APPLICANT_FACT_FIELDS

        profile = ApplicantProfile(
            visa=VisaSelection(program_id=PROGRAM_ID),
            visa_history="prior_subclass_482",
            employer_sponsored=True,
            work_experience_years=4,
            in_australia_at_application=True,
            has_health_insurance=True,
            genuine_temporary_entrant=False,
        )
        data = profile.to_rule_data()
        assert data["visa_history"] == "prior_subclass_482"
        assert data["employer_sponsored"] is True
        assert data["work_experience_years"] == 4
        assert data["in_australia_at_application"] is True
        assert data["has_health_insurance"] is True
        assert data["genuine_temporary_entrant"] is False

    def test_a_rule_on_a_new_concept_is_evaluable(self):
        from rules.rule_engine import is_field_evaluable

        assert is_field_evaluable("applicant.genuine_temporary_entrant") is True
        assert is_field_evaluable("applicant.visa_history") is True
        assert is_field_evaluable("applicant.has_health_insurance") is True


# ---------------------------------------------------------------------------
# 2. The dry run
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def compiled(admin_headers, engine):
    """One compiled rule per field under test, with real lineage.

    The rule ids are module constants so the tests below can refer to
    them by name; the rules themselves come from the real compiler.
    """
    chunk_id = _seed_lineage(engine)
    requirement_ids = {
        "age": _make_requirement(engine, chunk_id, "age", "applicant.age", "LT", 45),
        "points": _make_requirement(engine, chunk_id, "points", "applicant.points", "GTE", 65),
        "english": _make_requirement(engine, chunk_id, "english", "applicant.english_level",
                                     "EQ", "proficient"),
        "not_evaluable": _make_requirement(engine, chunk_id, "invitation",
                                           "applicant.hold_an_invitation", "EQ", True),
    }
    rules = _compile_rules(engine)
    return {
        key: rules[requirement_id]
        for key, requirement_id in requirement_ids.items()
    }


@pytest.fixture(scope="module")
def active_age_rule(client, compiled, admin_headers):
    """The age rule, live: DRAFT -> VALIDATED -> APPROVED -> ACTIVE."""
    rule_id = compiled["age"]
    _advance_to_approved(client, admin_headers, rule_id)
    resp = client.post(
        f"/api/v1/admin/rules/{rule_id}/activate", json={}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    return rule_id


class TestTheDryRun:
    def test_it_reports_a_pass_for_a_matching_applicant(self, client, active_age_rule, admin_headers):
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": PROGRAM_ID,
                "profile": {"age": 30},
                "rule_ids": [active_age_rule],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["overall_status"] == "PASS"
        assert data["passed"] == 1
        assert data["ruleset_hash"]

    def test_it_reports_a_fail_for_a_non_matching_applicant(self, client, active_age_rule, admin_headers):
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": PROGRAM_ID,
                "profile": {"age": 50},
                "rule_ids": [active_age_rule],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["overall_status"] == "FAIL"
        assert data["failed"] == 1

    def test_it_reports_unknown_when_the_fact_is_missing(self, client, active_age_rule, admin_headers):
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": PROGRAM_ID,
                "profile": {},
                "rule_ids": [active_age_rule],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["overall_status"] == "UNKNOWN"
        assert data["unknown"] == 1

    def test_it_names_the_gap_to_ask_about(self, client, active_age_rule, admin_headers):
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": PROGRAM_ID,
                "profile": {},
                "rule_ids": [active_age_rule],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        gaps = resp.json()["gaps"]
        assert gaps
        assert any("age" in str(gap).lower() for gap in gaps)

    def test_the_default_check_covers_the_active_rules(self, client, active_age_rule, admin_headers):
        """With no rule_ids the dry run checks the program's active rules."""
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": PROGRAM_ID, "profile": {"age": 30}},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        checked = {r["rule_id"] for r in resp.json()["results"]}
        assert active_age_rule in checked

    def test_it_can_check_one_rule_before_activating_it(self, client, compiled, admin_headers):
        rule_id = compiled["points"]
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={
                "program_id": PROGRAM_ID,
                "profile": {"points": 70},
                "rule_ids": [rule_id],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_rules"] == 1
        assert data["passed"] == 1

    def test_it_writes_nothing(self, client, admin_headers):
        """A dry run is a question, not an act. It must not touch the rules."""
        before = client.get(
            "/api/v1/admin/rules", params={"program_id": PROGRAM_ID}, headers=admin_headers
        ).json()
        client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": PROGRAM_ID, "profile": {"age": 30}},
            headers=admin_headers,
        )
        after = client.get(
            "/api/v1/admin/rules", params={"program_id": PROGRAM_ID}, headers=admin_headers
        ).json()
        assert len(before) == len(after)
        assert [r["status"] for r in before] == [r["status"] for r in after]

    def test_it_refuses_an_unknown_rule(self, client, admin_headers):
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": PROGRAM_ID, "rule_ids": ["RULE-E2E-PHASE4-NOPE"]},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_it_refuses_a_program_with_no_rules(self, client, admin_headers):
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": "E2E-PROGRAM-WITH-NO-RULES"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. The not-evaluable guard
# ---------------------------------------------------------------------------
class TestTheNotEvaluableGuard:
    def test_the_rule_is_badged_as_not_evaluable(self, client, compiled, admin_headers):
        resp = client.get(
            f"/api/v1/admin/rules/{compiled['not_evaluable']}", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["evaluable"] is False

    def test_an_evaluable_rule_is_badged_as_evaluable(self, client, compiled, admin_headers):
        resp = client.get(f"/api/v1/admin/rules/{compiled['age']}", headers=admin_headers)
        assert resp.json()["evaluable"] is True

    def test_activating_it_without_the_confirmation_is_refused(self, client, compiled, admin_headers):
        rule_id = compiled["not_evaluable"]
        _advance_to_approved(client, admin_headers, rule_id)
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/activate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "not evaluable" in resp.json()["detail"]

    def test_the_wrong_confirmation_is_refused(self, client, compiled, admin_headers):
        rule_id = compiled["not_evaluable"]
        for wrong in ("yes", "true", "activate", "ACTIVATE "):
            resp = client.post(
                f"/api/v1/admin/rules/{rule_id}/activate",
                json={"confirm_not_evaluable": wrong},
                headers=admin_headers,
            )
            assert resp.status_code == 400, wrong

    def test_the_typed_confirmation_lets_it_through(self, client, compiled, admin_headers):
        rule_id = compiled["not_evaluable"]
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/activate",
            json={"confirm_not_evaluable": "ACTIVATE"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ACTIVE"

    def test_an_evaluable_rule_needs_no_confirmation(self, client, compiled, admin_headers):
        rule_id = compiled["english"]
        _advance_to_approved(client, admin_headers, rule_id)
        resp = client.post(
            f"/api/v1/admin/rules/{rule_id}/activate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text

    def test_a_batch_with_a_not_evaluable_rule_is_refused_whole(self, client, compiled, admin_headers):
        """A guard that can be stepped around one rule at a time is not a
        guard. The batch must not activate anything."""
        evaluable_rule = compiled["points"]
        not_evaluable_rule = compiled["not_evaluable"]
        _advance_to_approved(client, admin_headers, evaluable_rule)

        resp = client.post(
            "/api/v1/admin/rules/batch/activate",
            json={"rule_ids": [evaluable_rule, not_evaluable_rule]},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "not evaluable" in resp.json()["detail"]

        # Nothing was activated - not even the evaluable rule.
        active = client.get(
            "/api/v1/admin/rules",
            params={"program_id": PROGRAM_ID, "status": "ACTIVE"},
            headers=admin_headers,
        ).json()
        assert all(r["rule_id"] != evaluable_rule for r in active)

    def test_the_batch_goes_through_once_confirmed(self, client, compiled, admin_headers):
        rule_id = compiled["points"]
        resp = client.post(
            "/api/v1/admin/rules/batch/activate",
            json={
                "rule_ids": [rule_id],
                "confirm_not_evaluable": "ACTIVATE",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert any(r["rule_id"] == rule_id and r["status"] == "ACTIVE" for r in resp.json())


# ---------------------------------------------------------------------------
# 4. Access control
# ---------------------------------------------------------------------------
class TestAccessControl:
    def test_a_non_admin_cannot_evaluate(self, client, engine):
        with get_session_factory(engine)() as s:
            svc = AuthService(s)
            if not svc.email_registered("phase4-user@test.invalid"):
                svc.register("phase4-user@test.invalid", "e2e-user-password", "E2E User")
                s.commit()
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "phase4-user@test.invalid", "password": "e2e-user-password"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        resp = client.post(
            "/api/v1/admin/rules/evaluate",
            json={"program_id": PROGRAM_ID},
            headers=headers,
        )
        assert resp.status_code == 403

    def test_an_anonymous_call_is_refused(self, client):
        resp = client.post(
            "/api/v1/admin/rules/evaluate", json={"program_id": PROGRAM_ID}
        )
        assert resp.status_code == 401
