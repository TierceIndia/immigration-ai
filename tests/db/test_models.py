"""Tests for the SQLAlchemy models (Sprint P1).

Uses SQLite in-memory for fast CI — production uses PostgreSQL via same models.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from db.models import (
    ApiUser,
    ApplicantProfile,
    Assessment,
    AssessmentRuleResult,
    AuditLog,
    Base,
    Country,
    DocumentChunk,
    DocumentVersion,
    ExtractionRun,
    Gap,
    GovernmentDocument,
    GovernmentSource,
    ImmigrationProgram,
    LlmUsage,
    Requirement,
    RequirementEvidence,
    RequirementReference,
    RequirementReview,
    RequirementVersion,
    Rule,
    RuleVersion,
    VisaSelection,
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def session(engine):
    factory = sessionmaker(bind=engine)
    s = factory()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema existence
# ---------------------------------------------------------------------------

class TestSchemaExists:
    def test_all_tables_created(self, engine):
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {
            "countries", "immigration_programs", "government_sources",
            "government_documents", "document_versions", "document_chunks",
            "fetch_log", "extraction_runs", "requirements", "requirement_evidence",
            "requirement_versions", "requirement_review", "requirement_references",
            "rules", "rule_versions", "applicant_profiles", "visa_selections",
            "assessments", "assessment_rule_results", "gaps",
            "api_users", "user_sessions", "audit_log", "llm_usage", "llm_budget_log",
        }
        missing = expected - tables
        assert not missing, f"Missing tables: {missing}"

    def test_country_pk(self, session):
        c = Country(country_id="AU", name="Australia", iso_code="AU")
        session.add(c)
        session.flush()
        assert session.get(Country, "AU").name == "Australia"

    def test_immigration_program_fk(self, session):
        session.add(Country(country_id="AU", name="Australia", iso_code="AU"))
        p = ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU",
            visa_category="SKILLED", subclass_code="189",
            name="Skilled Independent", status="ACTIVE",
        )
        session.add(p)
        session.flush()
        assert session.get(ImmigrationProgram, "SKILLED_189").subclass_code == "189"


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

class TestCrudOperations:
    def _seed_country_program(self, session):
        session.add(Country(country_id="AU", name="Australia", iso_code="AU"))
        session.add(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU",
            visa_category="SKILLED", subclass_code="189",
            name="Skilled Independent", status="ACTIVE",
        ))
        session.flush()

    def _seed_source_document(self, session):
        self._seed_country_program(session)
        src = GovernmentSource(
            source_id="SRC-AU-189-MAIN", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Department of Home Affairs",
            url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189",
            source_type="HTML", priority="PRIMARY", monitoring_method="WEB",
            crawl_frequency="DAILY", status="ACTIVE",
        )
        session.add(src)
        doc = GovernmentDocument(
            document_id="DOC-AU-189-MAIN", source_id="SRC-AU-189-MAIN",
            country_id="AU", program_id="SKILLED_189", visa_category="SKILLED",
            document_type="ELIGIBILITY_GUIDANCE", url=src.url,
            retrieved_at=datetime(2025, 8, 1, tzinfo=timezone.utc),
            content_hash="abc123", raw_path="/tmp/raw.html", status="CURRENT",
        )
        session.add(doc)
        session.flush()

    def _seed_extraction_requirement(self, session):
        self._seed_source_document(session)
        run = ExtractionRun(
            run_id="RUN-001", document_id="DOC-AU-189-MAIN", document_version=1,
            model="gpt-4o", prompt_version="v2", status="COMPLETED",
        )
        session.add(run)
        req = Requirement(
            requirement_id="REQ-001", semantic_key="age-18",
            program_id="SKILLED_189", country_id="AU", category="age",
            name="Minimum Age", operator="GTE", mandatory=1,
            status="APPROVED", extraction_run_id="RUN-001",
        )
        session.add(req)
        session.flush()
        return run, req

    def test_rule_crud(self, session):
        self._seed_extraction_requirement(session)
        rule = Rule(
            rule_id="RULE-001", requirement_id="REQ-001",
            program_id="SKILLED_189", category="age",
            field="age", operator="GTE", value_json="18",
            mandatory=True, critical=False, message="Must be 18+",
            status="ACTIVE", source_id="SRC-AU-189-MAIN",
            document_id="DOC-AU-189-MAIN", document_version=1,
            evidence_text="You must be at least 18.",
        )
        session.add(rule)
        session.flush()
        loaded = session.get(Rule, "RULE-001")
        assert loaded.field == "age"
        assert loaded.status == "ACTIVE"

    def test_rule_versioning(self, session):
        self._seed_extraction_requirement(session)
        rule = Rule(
            rule_id="RULE-002", requirement_id="REQ-001",
            program_id="SKILLED_189", category="age",
            field="age", operator="GTE", value_json="18",
            mandatory=True, critical=False, message="Must be 18+",
            status="ACTIVE", current_version=2,
        )
        session.add(rule)
        rv = RuleVersion(
            rule_id="RULE-002", version_number=1, snapshot_json={"field": "age"},
            change_type="INITIAL",
        )
        session.add(rv)
        rv2 = RuleVersion(
            rule_id="RULE-002", version_number=2, snapshot_json={"field": "age", "value": "21"},
            change_type="MODIFIED",
        )
        session.add(rv2)
        session.flush()

        versions = session.query(RuleVersion).filter(RuleVersion.rule_id == "RULE-002").all()
        assert len(versions) == 2
        assert versions[0].snapshot_json["field"] == "age"
        assert versions[1].change_type == "MODIFIED"

    def test_applicant_profile(self, session):
        user = ApiUser(email="test@example.com", password_hash="hash", full_name="Test User")
        session.add(user)
        session.flush()

        profile = ApplicantProfile(
            user_id=user.user_id, age=30, english_level="competent",
            points=65, occupation="261111", visa_subclass="189",
        )
        session.add(profile)
        session.flush()

        loaded = session.get(ApplicantProfile, profile.applicant_id)
        assert loaded.age == 30
        assert loaded.user_id == user.user_id

    def test_assessment_with_rule_results(self, session):
        user = ApiUser(email="a@b.com", password_hash="h", full_name="A")
        session.add(user)
        session.flush()

        profile = ApplicantProfile(user_id=user.user_id, age=30)
        session.add(profile)
        session.flush()

        assessment = Assessment(
            applicant_id=profile.applicant_id, program_id="SKILLED_189",
            business_status="ELIGIBLE", overall_status="PASS",
            total_rules=2, passed_count=2, failed_count=0,
            unknown_count=0, not_applicable_count=0,
        )
        session.add(assessment)
        session.flush()

        rr1 = AssessmentRuleResult(
            assessment_id=assessment.assessment_id, rule_id="R1",
            rule_version=1, status="PASS", field="age",
            actual_value="30", expected_value="18", operator="GTE",
            message="Age OK",
        )
        rr2 = AssessmentRuleResult(
            assessment_id=assessment.assessment_id, rule_id="R2",
            rule_version=1, status="PASS", field="points",
            actual_value="65", expected_value="65", operator="GTE",
            message="Points OK",
        )
        session.add(rr1)
        session.add(rr2)
        session.flush()

        results = session.query(AssessmentRuleResult).filter(
            AssessmentRuleResult.assessment_id == assessment.assessment_id
        ).all()
        assert len(results) == 2
        assert all(r.status == "PASS" for r in results)

    def test_gap_storage(self, session):
        user = ApiUser(email="g@b.com", password_hash="h", full_name="G")
        session.add(user)
        session.flush()

        profile = ApplicantProfile(user_id=user.user_id)
        session.add(profile)
        session.flush()

        assessment = Assessment(
            applicant_id=profile.applicant_id, program_id="SKILLED_189",
            business_status="INCOMPLETE", overall_status="UNKNOWN",
            total_rules=3, passed_count=1, failed_count=0,
            unknown_count=2, not_applicable_count=0,
        )
        session.add(assessment)
        session.flush()

        gap = Gap(
            assessment_id=assessment.assessment_id,
            field="english_level", priority="HIGH",
            message="English level required", rule_id="RULE-ENG",
        )
        session.add(gap)
        session.flush()

        gaps = session.query(Gap).filter(Gap.assessment_id == assessment.assessment_id).all()
        assert len(gaps) == 1
        assert gaps[0].priority == "HIGH"

    def test_audit_log(self, session):
        log = AuditLog(
            user_id=None, action="CREATE", entity_type="applicant",
            after_json={"age": 30},
        )
        session.add(log)
        session.flush()
        loaded = session.query(AuditLog).first()
        assert loaded.action == "CREATE"
        assert loaded.after_json["age"] == 30

    def test_llm_usage_tracking(self, session):
        usage = LlmUsage(
            provider="openai", model="gpt-4o",
            prompt_tokens=100, completion_tokens=50,
            total_tokens=150, cost_usd=0.005, latency_ms=1200,
        )
        session.add(usage)
        session.flush()
        loaded = session.query(LlmUsage).first()
        assert loaded.cost_usd == 0.005
        assert loaded.model == "gpt-4o"
