"""Tests for repository classes (Sprint P1)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import (
    ApiUser,
    ApplicantProfile,
    Base,
    Country,
    ImmigrationProgram,
    Requirement,
    ExtractionRun,
)
from db.repositories import (
    ApplicantRepo,
    AssessmentRepo,
    CountryRepo,
    EvidenceRepo,
    GapRepo,
    LlmUsageRepo,
    ProgramRepo,
    RequirementRepo,
    RuleRepo,
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


@pytest.fixture()
def country_repo(session):
    return CountryRepo(session)


@pytest.fixture()
def program_repo(session):
    return ProgramRepo(session)


class TestCountryRepo:
    def test_upsert_creates(self, country_repo, session):
        country_repo.upsert("AU", "Australia", "AU")
        session.flush()
        obj = country_repo.get("AU")
        assert obj.name == "Australia"

    def test_upsert_updates(self, country_repo, session):
        country_repo.upsert("AU", "Australia", "AU")
        session.flush()
        country_repo.upsert("AU", "Australia Updated", "AU")
        session.flush()
        assert country_repo.get("AU").name == "Australia Updated"


class TestProgramRepo:
    def _seed_country(self, session):
        session.add(Country(country_id="AU", name="Australia", iso_code="AU"))
        session.flush()

    def test_upsert_creates(self, program_repo, session):
        self._seed_country(session)
        program_repo.upsert("SKILLED_189", "AU", "SKILLED", "189", "Skilled Independent")
        session.flush()
        assert program_repo.get("SKILLED_189").subclass_code == "189"

    def test_list_by_country(self, program_repo, session):
        self._seed_country(session)
        program_repo.upsert("SKILLED_189", "AU", "SKILLED", "189", "Skilled Independent")
        program_repo.upsert("SKILLED_190", "AU", "SKILLED", "190", "Skilled Nominated")
        session.flush()
        progs = program_repo.list_by_country("AU")
        assert len(progs) == 2


class TestRuleRepo:
    def _seed_prereqs(self, session):
        session.add(Country(country_id="AU", name="Australia", iso_code="AU"))
        session.add(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU",
            visa_category="SKILLED", subclass_code="189",
            name="Skilled Independent", status="ACTIVE",
        ))
        run = ExtractionRun(
            run_id="RUN-001", document_id="DOC-AU-189-MAIN",
            document_version=1, model="gpt-4o", prompt_version="v2",
            status="COMPLETED",
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

    def test_create_and_get(self, session):
        self._seed_prereqs(session)
        repo = RuleRepo(session)
        repo.create(
            "RULE-001", requirement_id="REQ-001", program_id="SKILLED_189",
            category="age", field="age", operator="GTE",
            mandatory=True, critical=False, message="Must be 18+",
            status="ACTIVE",
        )
        session.flush()
        loaded = repo.get("RULE-001")
        assert loaded.field == "age"

    def test_list_active(self, session):
        self._seed_prereqs(session)
        repo = RuleRepo(session)
        repo.create("R1", requirement_id="REQ-001", program_id="SKILLED_189",
                    category="age", field="age", operator="GTE",
                    mandatory=True, critical=False, message="m", status="ACTIVE")
        repo.create("R2", requirement_id="REQ-001", program_id="SKILLED_189",
                    category="age", field="age", operator="GTE",
                    mandatory=True, critical=False, message="m", status="DRAFT")
        session.flush()
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "R1"

    def test_count(self, session):
        self._seed_prereqs(session)
        repo = RuleRepo(session)
        repo.create("R1", requirement_id="REQ-001", program_id="SKILLED_189",
                    category="age", field="age", operator="GTE",
                    mandatory=True, critical=False, message="m", status="ACTIVE")
        session.flush()
        assert repo.count() == 1


class TestApplicantRepo:
    def _seed_user(self, session):
        user = ApiUser(email="test@test.com", password_hash="h", full_name="Test")
        session.add(user)
        session.flush()
        return user

    def test_create_and_get(self, session):
        user = self._seed_user(session)
        repo = ApplicantRepo(session)
        profile = repo.create(str(user.user_id), age=30, visa_subclass="189")
        session.flush()
        loaded = repo.get(profile.applicant_id)
        assert loaded.age == 30

    def test_list_for_user(self, session):
        user = self._seed_user(session)
        repo = ApplicantRepo(session)
        repo.create(str(user.user_id), age=30)
        repo.create(str(user.user_id), age=25)
        session.flush()
        profiles = repo.list_for_user(str(user.user_id))
        assert len(profiles) == 2

    def test_update(self, session):
        user = self._seed_user(session)
        repo = ApplicantRepo(session)
        profile = repo.create(str(user.user_id), age=30)
        session.flush()
        repo.update(profile.applicant_id, age=31)
        session.flush()
        assert repo.get(profile.applicant_id).age == 31


class TestAssessmentRepo:
    def _seed_applicant(self, session):
        user = ApiUser(email="a@b.com", password_hash="h", full_name="A")
        session.add(user)
        session.flush()
        profile = ApplicantProfile(user_id=user.user_id, age=30)
        session.add(profile)
        session.flush()
        return profile

    def test_create_and_list(self, session):
        profile = self._seed_applicant(session)
        repo = AssessmentRepo(session)
        repo.create(str(profile.applicant_id), "SKILLED_189",
                    business_status="ELIGIBLE", overall_status="PASS",
                    total_rules=2, passed_count=2, failed_count=0,
                    unknown_count=0, not_applicable_count=0)
        repo.create(str(profile.applicant_id), "SKILLED_190",
                    business_status="INCOMPLETE", overall_status="UNKNOWN",
                    total_rules=3, passed_count=1, failed_count=0,
                    unknown_count=2, not_applicable_count=0)
        session.flush()
        results = repo.list_for_applicant(str(profile.applicant_id))
        assert len(results) == 2


class TestLlmUsageRepo:
    def test_daily_cost_no_records(self, session):
        repo = LlmUsageRepo(session)
        cost = repo.daily_cost("openai")
        assert cost == 0.0
