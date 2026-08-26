"""Tests for requirement → rule bridge (Phase 2 approved req → DRAFT rules)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.models import (
    Base, Requirement, RequirementEvidence,
    Rule as RuleORM,
)
from db.connection import get_session_factory
from phase3.requirement_to_rule import compile_approved_requirements, _load_requirement_dict
from phase3.rule_repository import EnhancedRuleRepo


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
    factory = get_session_factory(engine)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _create_approved_requirement(session, req_id="REQ-AGE-001",
                                 program_id="SKILLED_189",
                                 category="AGE", field="age",
                                 operator="GTE", value=25):
    from db.models import ExtractionRun
    # Create a minimal extraction run first (required FK) — use req_id to ensure uniqueness
    run_id = f"RUN-{req_id}"
    run = ExtractionRun(
        run_id=run_id,
        document_id="DOC-001",
        document_version=1,
        model="mock",
        prompt_version="v1",
        status="completed",
    )
    session.add(run)
    session.flush()

    req = Requirement(
        requirement_id=req_id,
        semantic_key="abc123",
        program_id=program_id,
        country_id="AU",
        category=category,
        name="Age requirement",
        description="Must be at least 25",
        field=field,
        operator=operator,
        value_json=json.dumps(value),
        unit="years",
        mandatory=True,
        criticality="CRITICAL",
        status="APPROVED",
        current_version=1,
        extraction_run_id=run_id,
    )
    session.add(req)
    session.flush()

    evidence = RequirementEvidence(
        requirement_id=req_id,
        document_id="DOC-001",
        document_version=1,
        chunk_id=100,
        evidence_text="Applicants must be at least 25 years of age",
        evidence_type="DIRECT_TEXT",
        extracted_at=datetime.now(timezone.utc),
    )
    session.add(evidence)
    session.flush()
    return req


class TestLoadRequirementDict:
    def test_loads_evidence(self, session):
        _create_approved_requirement(session)
        req = session.query(Requirement).first()
        d = _load_requirement_dict(session, req)
        assert len(d["evidence"]) == 1
        assert d["evidence"][0]["evidence_text"] == "Applicants must be at least 25 years of age"

    def test_preserves_fields(self, session):
        _create_approved_requirement(session)
        req = session.query(Requirement).first()
        d = _load_requirement_dict(session, req)
        assert d["category"] == "AGE"
        assert d["operator"] == "GTE"
        assert d["value"] == 25
        assert d["mandatory"]


class TestCompileApprovedRequirements:
    def test_compiles_approved_requirements(self, session):
        _create_approved_requirement(session)
        results = compile_approved_requirements(session, dry_run=True)
        assert len(results) == 1
        assert results[0]["status"] == "created"

    def test_persists_as_draft_rule(self, session):
        _create_approved_requirement(session)
        compile_approved_requirements(session, dry_run=False)

        rule = session.query(RuleORM).first()
        assert rule is not None
        assert rule.status == "DRAFT"
        assert rule.field == "age"
        assert rule.operator == "GTE"
        assert json.loads(rule.value_json) == 25

    def test_no_rules_for_nonexistent_approved(self, session):
        results = compile_approved_requirements(session)
        assert len(results) == 0

    def test_filters_by_program(self, session):
        _create_approved_requirement(session, req_id="REQ-1", program_id="SKILLED_189")
        _create_approved_requirement(session, req_id="REQ-2", program_id="SKILLED_190")
        results = compile_approved_requirements(session, program_id="SKILLED_189")
        assert len(results) == 1
        assert results[0]["requirement_id"] == "REQ-1"

    def test_handles_compile_error_gracefully(self, session):
        """A requirement with missing evidence should error, not crash."""
        from db.models import ExtractionRun
        run = ExtractionRun(
            run_id="RUN-BAD", document_id="DOC-001",
            document_version=1, model="mock", prompt_version="v1",
            status="completed",
        )
        session.add(run)
        session.flush()

        req = Requirement(
            requirement_id="REQ-BAD",
            semantic_key="bad",
            program_id="SKILLED_189",
            country_id="AU",
            category="AGE",
            name="Bad requirement",
            field="age",
            operator="GTE",
            value_json=json.dumps(25),
            status="APPROVED",
            current_version=1,
            extraction_run_id="RUN-BAD",
        )
        session.add(req)
        session.flush()
        # No evidence added — compile_requirement_to_rule requires evidence

        results = compile_approved_requirements(session)
        assert len(results) == 1
        assert results[0]["status"] == "error"

    def test_dry_run_does_not_persist(self, session):
        _create_approved_requirement(session)
        compile_approved_requirements(session, dry_run=True)
        assert session.query(RuleORM).count() == 0
