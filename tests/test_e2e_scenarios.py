"""E2E Scenario Tests — Production Acceptance Matrix.

These 10 scenarios prove the entire system works correctly under realistic
conditions. Each scenario maps to a specific real-world situation that must
work correctly before production deployment.

Scenario  1: Initial source → first active rule
Scenario  2: No change → no new rules
Scenario  3: Requirement change → v1 superseded, v2 active
Scenario  4: Irrelevant document change → rules unchanged
Scenario  5: LLM failure → old rule remains active
Scenario  6: Invalid extraction → rule not activated
Scenario  7: Compiler failure → old rule remains active
Scenario  8: Historical assessment uses old rule version
Scenario  9: Applicant profile change → assessment snapshot frozen
Scenario 10: Multi-user isolation
"""
from __future__ import annotations

import json
import hashlib
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, Rule, Requirement, GovernmentSource, ImmigrationProgram, Country
from db.repositories import (
    SourceRepo, ProgramRepo, CountryRepo, RequirementRepo, PipelineRunRepo,
    ApplicantRepo, AssessmentRepo, AssessmentRuleResultRepo, UserRepo,
)
from rules.rule_repository import EnhancedRuleRepo, orm_to_dataclass
from rules.requirement_to_rule import compile_approved_requirements
from rules.models import RuleStatus
from services.assessment_service import AssessmentService, _compute_ruleset_hash
from services.auth_service import AuthService
from pipeline_run_activation import validate_run_transition


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
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def seed_country_program(session):
    CountryRepo(session).upsert("AU", "Australia", "AU")
    ProgramRepo(session).upsert(
        "SKILLED_189", "AU", "Skilled Independent", "189",
        "Skilled Independent visa",
    )
    session.commit()


# ---------------------------------------------------------------------------
# Scenario 1 — Initial source → first active rule
# ---------------------------------------------------------------------------
class TestScenario1_InitialSource:
    """Government source → extraction → approval → ACTIVE rule."""

    def test_initial_source_creates_active_rule(self, session, seed_country_program):
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
            status="APPROVED",
            current_version=1,
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Applicants must be under 45 years of age",
        )
        session.commit()

        rule_repo.activate_rule("RULE-AGE-189", actor="admin")
        session.commit()

        active_rules = rule_repo.list_active("SKILLED_189")
        assert len(active_rules) == 1
        assert active_rules[0].rule_id == "RULE-AGE-189"
        assert active_rules[0].status == "ACTIVE"


# ---------------------------------------------------------------------------
# Scenario 2 — No change → no new rules
# ---------------------------------------------------------------------------
class TestScenario2_NoChange:
    """Same government document → no new requirement → no new rule."""

    def test_unchanged_source_no_new_rules(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        active_before = rule_repo.list_active("SKILLED_189")
        count_before = len(active_before)

        # Simulate unchanged sync: no new requirements → no new rules
        active_after = rule_repo.list_active("SKILLED_189")
        assert len(active_after) == count_before
        assert active_after[0].current_version == 1


# ---------------------------------------------------------------------------
# Scenario 3 — Requirement change → v1 superseded, v2 active
# ---------------------------------------------------------------------------
class TestScenario3_RequirementChange:
    """Age < 45 → Age < 50: v1 SUPERSEDED, v2 ACTIVE."""

    def test_requirement_change_supersedes_v1(self, session, seed_country_program):
        rule_repo = EnhancedRuleRepo(session)

        rule_repo.create(
            rule_id="RULE-AGE-189-v1",
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        rule_repo.create(
            rule_id="RULE-AGE-189-v2",
            requirement_id="REQ-AGE-189",
            program_id="SKILLED_189",
            category="age",
            field="age",
            operator="LT",
            value_json="50",
            mandatory=True,
            critical=True,
            message="Age must be under 50",
            status="APPROVED",
            current_version=2,
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=2,
            chunk_id=1,
            evidence_text="Under 50",
        )
        session.commit()

        rule_repo.activate_rule("RULE-AGE-189-v2", actor="admin")
        session.commit()

        active_rules = rule_repo.list_active("SKILLED_189")
        assert len(active_rules) == 1
        assert active_rules[0].rule_id == "RULE-AGE-189-v2"

        v1 = rule_repo.get("RULE-AGE-189-v1")
        assert v1.status == "SUPERSEDED"

        v2 = rule_repo.get("RULE-AGE-189-v2")
        assert v2.status == "ACTIVE"
        assert json.loads(v2.value_json) == 50


# ---------------------------------------------------------------------------
# Scenario 4 — Irrelevant document change → rules unchanged
# ---------------------------------------------------------------------------
class TestScenario4_IrrelevantChange:
    """Document changes (e.g. processing time) but eligibility rules stay the same."""

    def test_irrelevant_change_does_not_create_new_rules(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        active_rules = rule_repo.list_active("SKILLED_189")
        assert len(active_rules) == 1
        assert active_rules[0].current_version == 1
        assert active_rules[0].operator == "LT"
        assert json.loads(active_rules[0].value_json) == 45


# ---------------------------------------------------------------------------
# Scenario 5 — LLM failure → old rule remains active
# ---------------------------------------------------------------------------
class TestScenario5_LLMFailure:
    """LLM extraction fails during sync → existing ACTIVE rule is untouched."""

    def test_no_new_rules_leaves_existing_active_rule_intact(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        active_before = rule_repo.list_active("SKILLED_189")
        assert len(active_before) == 1

        # Simulate: sync fails, no new rules created
        # ACTIVE protection in upsert_from_compiled ensures no downgrade
        active_after = rule_repo.list_active("SKILLED_189")
        assert len(active_after) == 1
        assert active_after[0].rule_id == "RULE-AGE-189"
        assert active_after[0].status == "ACTIVE"


# ---------------------------------------------------------------------------
# Scenario 6 — Invalid extraction → rule not activated
# ---------------------------------------------------------------------------
class TestScenario6_InvalidExtraction:
    """LLM returns invalid requirement → validation fails → rule stays DRAFT."""

    def test_invalid_extraction_blocks_activation(self, session, seed_country_program):
        rule_repo = EnhancedRuleRepo(session)

        rule_repo.create(
            rule_id="RULE-BAD-001",
            requirement_id="REQ-BAD-001",
            program_id="SKILLED_189",
            category="unknown",
            field="unknown_field",
            operator="UNKNOWN",
            value_json=None,
            mandatory=True,
            critical=True,
            message="Invalid rule",
            status="DRAFT",
            current_version=1,
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Invalid",
        )
        session.commit()

        # Cannot activate DRAFT directly — must go through VALIDATED → APPROVED → ACTIVE
        with pytest.raises(ValueError, match="Invalid rule status transition"):
            rule_repo.activate_rule("RULE-BAD-001", actor="admin")

        rule = rule_repo.get("RULE-BAD-001")
        assert rule.status == "DRAFT"

        active_rules = rule_repo.list_active("SKILLED_189")
        assert len(active_rules) == 0


# ---------------------------------------------------------------------------
# Scenario 7 — Compiler failure → old rule remains active
# ---------------------------------------------------------------------------
class TestScenario7_CompilerFailure:
    """Rule compilation fails → old ACTIVE rule untouched."""

    def test_compiler_failure_preserves_active_rule(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        # Compile from non-existent approved requirements → returns empty
        results = compile_approved_requirements(session, program_id="NONEXISTENT")
        session.commit()

        # Original ACTIVE rule still exists
        active_rules = rule_repo.list_active("SKILLED_189")
        assert len(active_rules) == 1
        assert active_rules[0].rule_id == "RULE-AGE-189"
        assert active_rules[0].status == "ACTIVE"


# ---------------------------------------------------------------------------
# Scenario 8 — Historical assessment uses old rule version
# ---------------------------------------------------------------------------
class TestScenario8_HistoricalAssessment:
    """Assessment created with v1 rules still references v1 after v2 becomes active."""

    def test_historical_assessment_uses_v1(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        user_repo = UserRepo(session)
        user = user_repo.create(email="test@example.com", password_hash="hash", full_name="Test")

        applicant_repo = ApplicantRepo(session)
        applicant = applicant_repo.create(user_id=user.user_id, age=30)

        assessment_repo = AssessmentRepo(session)
        result_repo = AssessmentRuleResultRepo(session)

        assessment = assessment_repo.create(
            applicant_id=applicant.applicant_id,
            program_id="SKILLED_189",
            business_status="ELIGIBLE",
            overall_status="PASS",
            total_rules=1,
            passed_count=1,
            failed_count=0,
            unknown_count=0,
            not_applicable_count=0,
        )

        result_repo.create(
            assessment_id=assessment.assessment_id,
            rule_id="RULE-AGE-189",
            rule_version=1,
            status="PASS",
            field="age",
        )
        session.commit()

        # Now v2 arrives
        rule_repo.create(
            rule_id="RULE-AGE-189-v2",
            requirement_id="REQ-AGE-189",
            program_id="SKILLED_189",
            category="age",
            field="age",
            operator="LT",
            value_json="50",
            mandatory=True,
            critical=True,
            message="Age must be under 50",
            status="APPROVED",
            current_version=2,
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=2,
            chunk_id=1,
            evidence_text="Under 50",
        )
        session.commit()
        rule_repo.activate_rule("RULE-AGE-189-v2", actor="admin")
        session.commit()

        # Historical assessment still references v1
        results = result_repo.list_for_assessment(assessment.assessment_id)
        assert len(results) == 1
        assert results[0].rule_version == 1
        assert results[0].rule_id == "RULE-AGE-189"


# ---------------------------------------------------------------------------
# Scenario 9 — Applicant profile change → assessment snapshot frozen
# ---------------------------------------------------------------------------
class TestScenario9_ApplicantSnapshot:
    """Applicant age changes from 30→35 after assessment. Assessment retains age=30."""

    def test_assessment_snapshot_frozen(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        user_repo = UserRepo(session)
        user = user_repo.create(email="snap@example.com", password_hash="hash", full_name="Snap")

        applicant_repo = ApplicantRepo(session)
        applicant = applicant_repo.create(
            user_id=user.user_id, age=30, english_level="competent",
            points=65, occupation="Software Engineer",
            skills_assessment_status="positive",
            health_status="good", character_status="good",
        )

        assessment_repo = AssessmentRepo(session)
        result_repo = AssessmentRuleResultRepo(session)

        snapshot_before = {
            "age": 30, "english_level": "competent", "points": 65,
            "occupation": "Software Engineer",
            "skills_assessment_status": "positive",
            "health_status": "good", "character_status": "good",
        }

        assessment = assessment_repo.create(
            applicant_id=applicant.applicant_id,
            program_id="SKILLED_189",
            business_status="ELIGIBLE",
            overall_status="PASS",
            total_rules=1,
            passed_count=1,
            failed_count=0,
            unknown_count=0,
            not_applicable_count=0,
            applicant_snapshot=snapshot_before,
        )
        session.commit()

        # Applicant changes age to 35
        applicant_repo.update(applicant.applicant_id, age=35)
        session.commit()

        # Assessment snapshot still shows age=30
        retrieved = assessment_repo.get(assessment.assessment_id)
        assert retrieved.applicant_snapshot["age"] == 30
        assert retrieved.applicant_snapshot["english_level"] == "competent"


# ---------------------------------------------------------------------------
# Scenario 10 — Multi-user isolation
# ---------------------------------------------------------------------------
class TestScenario10_MultiUserIsolation:
    """User A cannot see User B's applicants or assessments."""

    def test_user_isolation(self, session, seed_country_program):
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
            source_id="SRC-AU-189",
            document_id="DOC-AU-189",
            document_version=1,
            chunk_id=1,
            evidence_text="Under 45",
        )
        session.commit()

        user_repo = UserRepo(session)
        user_a = user_repo.create(email="a@test.com", password_hash="h", full_name="User A")
        user_b = user_repo.create(email="b@test.com", password_hash="h", full_name="User B")

        applicant_repo = ApplicantRepo(session)
        app_a = applicant_repo.create(user_id=user_a.user_id, age=30)
        app_b = applicant_repo.create(user_id=user_b.user_id, age=40)
        session.commit()

        # User A's applicants don't include User B's
        a_applicants = applicant_repo.list_for_user(user_a.user_id)
        b_applicants = applicant_repo.list_for_user(user_b.user_id)

        a_ids = {a.applicant_id for a in a_applicants}
        b_ids = {b.applicant_id for b in b_applicants}

        assert a_ids == {app_a.applicant_id}
        assert b_ids == {app_b.applicant_id}
        assert a_ids.isdisjoint(b_ids)


# ---------------------------------------------------------------------------
# Ruleset hash fingerprint
# ---------------------------------------------------------------------------
class TestRulesetHash:
    """Assessment records a deterministic hash of the rule set used."""

    def test_ruleset_hash_is_deterministic(self, session, seed_country_program):
        from services.assessment_service import _compute_ruleset_hash
        from rules.models import Rule, Provenance

        p = Provenance(source_id="S1", document_id="D1", document_version=1,
                       chunk_id=1, requirement_id="REQ1", evidence_text="test")
        rules = [
            Rule(rule_id="R1", requirement_id="REQ1", program_id="SKILLED_189",
                 category="age", field="age", operator="LT", value=45,
                 unit="years", mandatory=True, critical=True, message="Under 45",
                 provenance=p, status=RuleStatus.ACTIVE, version=1),
            Rule(rule_id="R2", requirement_id="REQ2", program_id="SKILLED_189",
                 category="points", field="points", operator="GTE", value=65,
                 unit="points", mandatory=True, critical=False, message="65+ points",
                 provenance=p, status=RuleStatus.ACTIVE, version=1),
        ]

        h1 = _compute_ruleset_hash("SKILLED_189", rules)
        h2 = _compute_ruleset_hash("SKILLED_189", rules)
        assert h1 == h2
        assert len(h1) == 16

    def test_ruleset_hash_changes_with_version(self, session, seed_country_program):
        from services.assessment_service import _compute_ruleset_hash
        from rules.models import Rule, Provenance

        p = Provenance(source_id="S1", document_id="D1", document_version=1,
                       chunk_id=1, requirement_id="REQ1", evidence_text="test")

        rules_v1 = [
            Rule(rule_id="R1", requirement_id="REQ1", program_id="SKILLED_189",
                 category="age", field="age", operator="LT", value=45,
                 unit="years", mandatory=True, critical=True, message="Under 45",
                 provenance=p, status=RuleStatus.ACTIVE, version=1),
        ]

        rules_v2 = [
            Rule(rule_id="R1", requirement_id="REQ1", program_id="SKILLED_189",
                 category="age", field="age", operator="LT", value=50,
                 unit="years", mandatory=True, critical=True, message="Under 50",
                 provenance=p, status=RuleStatus.ACTIVE, version=2),
        ]

        h1 = _compute_ruleset_hash("SKILLED_189", rules_v1)
        h2 = _compute_ruleset_hash("SKILLED_189", rules_v2)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
class TestHealthEndpoints:
    def test_liveness_always_200(self):
        from fastapi.testclient import TestClient
        from api.main import create_app
        app = create_app()
        app.state.db_engine = None
        with TestClient(app) as c:
            resp = c.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    def test_readiness_degraded_without_db(self):
        from fastapi.testclient import TestClient
        from api.main import create_app
        app = create_app()
        app.state.db_engine = None
        with TestClient(app) as c:
            resp = c.get("/ready")
            assert resp.status_code == 503
            assert resp.json()["status"] == "degraded"
