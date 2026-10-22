"""Phase 4 acceptance: full source → knowledge → assessment lifecycle on PostgreSQL.

End-to-end proof that frozen government content flows through the knowledge
pipeline (fetch → extract → requirement → rule compile → validate → approve →
activate) and then into the assessment engine, producing PASS / FAIL / UNKNOWN
depending on applicant data.

Tests:
    1. PASS — eligible applicant (age 30, points 80) vs ACTIVE rules
    2. FAIL — ineligible applicant (age 50, fails age rule)
    3. UNKNOWN — incomplete applicant (no age → UNKNOWN on mandatory age rule)

Gated by POSTGRES_E2E=1 against the live localhost:5432 dev PostgreSQL.
Cleanup removes every sandbox row after the test.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.models import (
    ApiUser, ApplicantProfile, VisaSelection, Country, ImmigrationProgram,
    GovernmentSource, Assessment, AssessmentRuleResult, Gap, DocumentVersion,
)
from db.connection import get_session_factory
from db.repositories import PipelineRunRepo
from fetch.fetcher import FetchResult, FetchStatus
from services.assessment_service import AssessmentService
from orchestration.government_sync import sync_source
from rules.rule_repository import EnhancedRuleRepo
from rules.requirement_to_rule import compile_approved_requirements

pytestmark = [
    pytest.mark.postgres_e2e,
    pytest.mark.skipif(
        os.environ.get("POSTGRES_E2E") != "1",
        reason="POSTGRES_E2E=1 required (runs against the live localhost:5432 dev PostgreSQL)",
    ),
]

E2E_URL = os.environ.get(
    "POSTGRES_E2E_URL",
    # Driver named explicitly - see test_db_driver_pinning.py: a bare
    # postgresql:// re-inherits SQLAlchemy's version-dependent default
    # dialect (psycopg v3 on 2.1+, not a project dependency).
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

PROGRAM_ID = "SKILLED_189"
SOURCE_ID = "SRC-A2E-ASSESS-189"
SOURCE_URL = "https://immi.homeaffairs.gov.au/eligibility/skilled-independent-189"

FIXTURE_AU_189 = (
    b"<html><head><title>Skilled Independent (subclass 189)</title></head><body>"
    b"<h1>Eligibility criteria</h1>"
    b"<h2>Age</h2><p>You must be under 45 years of age at the time of invitation.</p>"
    b"<h2>Points</h2><p>You must score at least 65 points on the points test.</p>"
    b"<h2>English</h2><p>You must have competent English or higher.</p>"
    b"</body></html>"
)

USER_ID = "a0b1c2d3-e4f5-6789-abcd-ef0123456789"
ELIGIBLE_APPLICANT_ID = "11111111-2222-3333-4444-555555555555"
FAILING_APPLICANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UNKNOWN_APPLICANT_ID = "11111111-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(E2E_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def session(engine):
    _cleanup()
    s = get_session_factory(engine)()
    yield s
    s.close()
    _cleanup()


_APPEND_ONLY = (
    "document_versions", "requirement_versions", "rule_versions",
    "assessments", "assessment_rule_results",
    "rulesets", "ruleset_rules",
)


def _cleanup():
    from sqlalchemy import create_engine
    eng = create_engine(E2E_URL, pool_pre_ping=True)
    try:
        with eng.begin() as conn:
            for t in _APPEND_ONLY:
                conn.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER ALL"))
            for stmt in (
                "DELETE FROM gaps WHERE assessment_id IN "
                    "(SELECT assessment_id FROM assessments WHERE applicant_id IN "
                    "(:a1,:a2,:a3))",
                "DELETE FROM assessment_rule_results WHERE assessment_id IN "
                    "(SELECT assessment_id FROM assessments WHERE applicant_id IN "
                    "(:a1,:a2,:a3))",
                "DELETE FROM assessments WHERE applicant_id IN (:a1,:a2,:a3)",
                "DELETE FROM visa_selections WHERE applicant_id IN (:a1,:a2,:a3)",
                "DELETE FROM applicant_profiles WHERE applicant_id IN (:a1,:a2,:a3)",
                "DELETE FROM api_users WHERE user_id = :uid",
                # Scoped by THIS test's own document lineage (document_id LIKE
                # 'DOC-A2E-%'), never by program_id + a generic id pattern:
                # real extraction produces requirement_ids in the exact same
                # REQ-{program_id}-{hash} shape this test's fixture does, so a
                # program_id-only filter would also match (and delete) real,
                # unrelated SKILLED_189 data already extracted from a real
                # sync sharing this dev Postgres - confirmed live: 45 real
                # rows matched the old filter, 0 of them belonging to this test.
                "DELETE FROM requirement_evidence WHERE document_id LIKE 'DOC-A2E-%'",
                "DELETE FROM ruleset_rules WHERE ruleset_id IN "
                    "(SELECT ruleset_id FROM rulesets WHERE program_id = :prog)",
                "DELETE FROM rulesets WHERE program_id = :prog",
                "DELETE FROM rule_versions WHERE rule_id IN "
                    "(SELECT rule_id FROM rules WHERE requirement_id IN "
                    "(SELECT requirement_id FROM requirements WHERE document_version_id IN "
                    "(SELECT version_id FROM document_versions WHERE document_id LIKE 'DOC-A2E-%')))",
                "DELETE FROM rules WHERE requirement_id IN "
                    "(SELECT requirement_id FROM requirements WHERE document_version_id IN "
                    "(SELECT version_id FROM document_versions WHERE document_id LIKE 'DOC-A2E-%'))",
                "DELETE FROM requirement_versions WHERE requirement_id IN "
                    "(SELECT requirement_id FROM requirements WHERE document_version_id IN "
                    "(SELECT version_id FROM document_versions WHERE document_id LIKE 'DOC-A2E-%'))",
                "DELETE FROM requirements WHERE document_version_id IN "
                    "(SELECT version_id FROM document_versions WHERE document_id LIKE 'DOC-A2E-%')",
                "DELETE FROM extraction_runs WHERE document_id LIKE 'DOC-A2E-%'",
                "DELETE FROM document_chunks WHERE document_id LIKE 'DOC-A2E-%'",
                "DELETE FROM document_versions WHERE document_id LIKE 'DOC-A2E-%'",
                "DELETE FROM government_documents WHERE document_id LIKE 'DOC-A2E-%'",
                "DELETE FROM fetch_log WHERE source_id = :src",
                "DELETE FROM pipeline_runs WHERE source_id = :src",
                "DELETE FROM government_sources WHERE source_id = :src",
            ):
                conn.execute(text(stmt), {
                    "a1": ELIGIBLE_APPLICANT_ID,
                    "a2": FAILING_APPLICANT_ID,
                    "a3": UNKNOWN_APPLICANT_ID,
                    "uid": USER_ID,
                    "src": SOURCE_ID,
                    "prog": PROGRAM_ID,
                })
            for t in _APPEND_ONLY:
                conn.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER ALL"))
    finally:
        eng.dispose()


def _ok_fetch(payload: bytes):
    def fetch(url, source_type=None, **kw):
        return FetchResult(
            url=url, status=FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=payload,
            duration_ms=5, error=None, error_type=None,
        )
    return fetch


def _seed_program(session):
    if session.get(Country, "AU") is None:
        session.add(Country(country_id="AU", name="Australia", iso_code="AUS"))
    if session.get(ImmigrationProgram, PROGRAM_ID) is None:
        session.add(ImmigrationProgram(
            program_id=PROGRAM_ID, country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
    session.flush()


def _seed_source(session):
    if session.get(GovernmentSource, SOURCE_ID) is None:
        session.add(GovernmentSource(
            source_id=SOURCE_ID, country_id="AU", program_id=PROGRAM_ID,
            visa_category="189", authority="Department of Home Affairs",
            url=SOURCE_URL, source_type="WEBPAGE", source_subtype="ELIGIBILITY_CRITERIA",
            priority="PRIMARY", monitoring_method="HASH", crawl_frequency="DAILY",
            status="ACTIVE",
        ))
    session.flush()


def _seed_user(session):
    if session.get(ApiUser, USER_ID) is None:
        session.add(ApiUser(
            user_id=USER_ID, email="e2e@test.invalid",
            password_hash="x", full_name="E2E Test Applicant",
            role="applicant", is_active=True,
        ))
    session.flush()


def _seed_applicant(session, applicant_id: str, age: int | None = None,
                    points: int | None = None, english_level: str | None = None):
    if session.get(ApplicantProfile, applicant_id) is None:
        session.add(ApplicantProfile(
            applicant_id=applicant_id, user_id=USER_ID,
            first_name="Applicant", last_name="E2E",
            age=age, points=points, english_level=english_level,
            profile_json={"age": age, "points": points, "english_level": english_level},
        ))
        session.add(VisaSelection(
            applicant_id=applicant_id, program_id=PROGRAM_ID,
        ))
    session.flush()


class TestFullKnowledgeToAssessment:
    def test_source_to_pass_fail_unknown(self, session, monkeypatch):
        _seed_program(session)
        _seed_source(session)
        _seed_user(session)
        session.commit()

        # --- Step 1: Sync government source → extract → compile → activate rules ---
        monkeypatch.setattr("fetch.backend.fetch", _ok_fetch(FIXTURE_AU_189))
        result = sync_source(session, SOURCE_ID, trigger_type="ON_DEMAND")
        assert result.status == "changed"
        session.commit()

        # Extractor produced requirements via MockLLMClient; approve those.
        # Scoped to THIS test's own document (DOC-A2E-ASSESS-189), never to
        # "every requirement for program_id=SKILLED_189" - this dev Postgres
        # is shared with real extraction output and demo data (see
        # seed_review_demo.py), and a program-wide filter would blindly
        # overwrite their status too, not just fail: confirmed live, this
        # exact filter previously swept in 45 real requirements plus 2 demo
        # CONFLICT rows and bulk-set them all to APPROVED.
        from db.models import Requirement, ExtractionRun
        document_id = f"DOC-{SOURCE_ID.replace('SRC-', '')}"
        extracted = (
            session.query(Requirement)
            .join(DocumentVersion, DocumentVersion.version_id == Requirement.document_version_id)
            .filter(Requirement.program_id == PROGRAM_ID,
                    Requirement.country_id == "AU",
                    DocumentVersion.document_id == document_id)
            .all()
        )
        assert len(extracted) >= 1, "extraction must produce at least one requirement"

        for req in extracted:
            req.status = "APPROVED"
        session.flush()

        # Compile → DRAFT rules
        compiled = compile_approved_requirements(session, program_id=PROGRAM_ID)
        created_rules = [r for r in compiled if r["status"] == "created"]
        assert len(created_rules) >= 1, f"expected rules created, got: {compiled}"

        # Validate + approve + activate every new rule
        rule_repo = EnhancedRuleRepo(session)
        for r in created_rules:
            rule_repo.validate_rule(r["rule_id"], actor="system")
            rule_repo.approve_rule(r["rule_id"], actor="system")
            rule_repo.activate_rule(r["rule_id"], actor="system")
        session.commit()

        active = rule_repo.list_active(PROGRAM_ID)
        assert len(active) >= 1
        active_fields = {r.field for r in active}

        # --- Step 2: PASS — eligible applicant ---
        _seed_applicant(session, ELIGIBLE_APPLICANT_ID, age=30, points=80,
                        english_level="proficient")
        session.commit()

        svc = AssessmentService(session)
        pass_result = svc.run_assessment(ELIGIBLE_APPLICANT_ID, PROGRAM_ID)
        assert pass_result["business_status"] == "ELIGIBLE"
        assert pass_result["overall_status"] == "PASS"
        assert pass_result["failed_count"] == 0
        assert pass_result["unknown_count"] == 0
        session.commit()

        assessment_pass = (
            session.query(Assessment)
            .filter(Assessment.applicant_id == ELIGIBLE_APPLICANT_ID)
            .order_by(Assessment.evaluated_at.desc())
            .first()
        )
        assert assessment_pass is not None
        assert assessment_pass.business_status == "ELIGIBLE"
        assert assessment_pass.overall_status == "PASS"
        assert assessment_pass.applicant_snapshot["age"] == 30
        assert assessment_pass.ruleset_hash is not None

        # Every rule result is PASS
        results = (
            session.query(AssessmentRuleResult)
            .filter(AssessmentRuleResult.assessment_id == assessment_pass.assessment_id)
            .all()
        )
        assert len(results) == len(active)
        for rr in results:
            assert rr.status == "PASS", f"rule {rr.rule_id} expected PASS, got {rr.status}"

        # No gaps for a PASS assessment
        gaps = (
            session.query(Gap)
            .filter(Gap.assessment_id == assessment_pass.assessment_id)
            .all()
        )
        assert len(gaps) == 0

        # --- Step 3: FAIL — ineligible applicant (age=50 fails age<45 rule) ---
        _seed_applicant(session, FAILING_APPLICANT_ID, age=50, points=80,
                        english_level="proficient")
        session.commit()

        fail_result = svc.run_assessment(FAILING_APPLICANT_ID, PROGRAM_ID)
        assert fail_result["business_status"] == "INELIGIBLE"
        assert fail_result["overall_status"] == "FAIL"
        assert fail_result["failed_count"] >= 1
        session.commit()

        assessment_fail = (
            session.query(Assessment)
            .filter(Assessment.applicant_id == FAILING_APPLICANT_ID)
            .order_by(Assessment.evaluated_at.desc())
            .first()
        )
        assert assessment_fail.business_status == "INELIGIBLE"

        fail_results = (
            session.query(AssessmentRuleResult)
            .filter(AssessmentRuleResult.assessment_id == assessment_fail.assessment_id)
            .all()
        )
        has_fail = any(rr.status == "FAIL" for rr in fail_results)
        assert has_fail, "at least one rule result must be FAIL for age=50 vs age<45"

        # --- Step 4: UNKNOWN — incomplete applicant (no age → UNKNOWN on mandatory age rule) ---
        _seed_applicant(session, UNKNOWN_APPLICANT_ID, age=None, points=80,
                        english_level="proficient")
        session.commit()

        unknown_result = svc.run_assessment(UNKNOWN_APPLICANT_ID, PROGRAM_ID)
        assert unknown_result["business_status"] == "INCOMPLETE"
        assert unknown_result["overall_status"] == "UNKNOWN"
        assert unknown_result["unknown_count"] >= 1
        session.commit()

        assessment_unknown = (
            session.query(Assessment)
            .filter(Assessment.applicant_id == UNKNOWN_APPLICANT_ID)
            .order_by(Assessment.evaluated_at.desc())
            .first()
        )
        assert assessment_unknown.business_status == "INCOMPLETE"

        # Gaps generated for the UNKNOWN field
        gaps = (
            session.query(Gap)
            .filter(Gap.assessment_id == assessment_unknown.assessment_id)
            .all()
        )
        assert len(gaps) >= 1
        gap_fields = {g.field for g in gaps}
        # Gap.field preserves the compiled rule's real field name verbatim (the
        # extraction-contract's dotted form, e.g. "applicant.age") for full
        # provenance/traceability back to the exact rule — not the bare
        # ApplicantProfile.to_rule_data() key it was resolved against.
        assert "applicant.age" in gap_fields, "missing age should produce a gap for UNKNOWN age rule"

        # Every rule result is PASS, FAIL, or UNKNOWN — no errors
        unknown_results = (
            session.query(AssessmentRuleResult)
            .filter(AssessmentRuleResult.assessment_id == assessment_unknown.assessment_id)
            .all()
        )
        for rr in unknown_results:
            assert rr.status in ("PASS", "FAIL", "UNKNOWN"), \
                f"unexpected rule result status {rr.status} for rule {rr.rule_id}"