"""Tests for requirement → rule bridge (Phase 2 approved req → DRAFT rules)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.models import (
    Base, Requirement, RequirementEvidence,
    Rule as RuleORM, RuleVersion,
)
from db.connection import get_session_factory
from rules.requirement_to_rule import compile_approved_requirements, _load_requirement_dict
from rules.rule_repository import EnhancedRuleRepo


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
        status="COMPLETED",
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
            status="COMPLETED",
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

    def test_recompiling_an_unchanged_rule_reports_unchanged_not_created(self, session):
        """Real bug found live: resyncing a source whose program already had
        approved requirements re-ran compile_approved_requirements() for the
        whole program every time (government_sync.py's _finalize_change does
        this unconditionally whenever approved_count > 0), and the result
        always said "created" even when upsert_from_compiled() made no real
        change to an already-ACTIVE rule - confirmed via 3 real rules whose
        created_at/updated_at were frozen from a previous day, yet a sync
        that touched none of them still reported rules_created=3.

        government_sync.py computes rules_created as
        sum(1 for r in results if r["status"] == "created") - this test
        exercises that exact computation directly.
        """
        _create_approved_requirement(session)
        session.commit()

        first = compile_approved_requirements(session, dry_run=False)
        session.commit()
        assert len(first) == 1
        assert first[0]["status"] == "created"
        first_rules_created = sum(1 for r in first if r["status"] == "created")
        assert first_rules_created == 1

        # Recompile the exact same, still-APPROVED requirement again - nothing
        # about it has changed, so nothing about the resulting rule should
        # either.
        second = compile_approved_requirements(session, dry_run=False)
        assert len(second) == 1
        assert second[0]["status"] == "unchanged"
        second_rules_created = sum(1 for r in second if r["status"] == "created")
        assert second_rules_created == 0

        # Only one rule_versions row should exist - the real CREATED one from
        # the first compile, not a second spurious entry from the no-op.
        versions = (
            session.query(RuleVersion)
            .filter(RuleVersion.rule_id == "RULE-REQ-AGE-001")
            .all()
        )
        assert len(versions) == 1
        assert versions[0].change_type == "CREATED"

    def test_recompiling_after_a_real_field_change_reports_created(self, session):
        """A genuine change to the approved requirement (e.g. a re-extraction
        with a different value) must still be reported as a real change, not
        swallowed by the no-op guard."""
        _create_approved_requirement(session, value=25)
        session.commit()
        compile_approved_requirements(session, dry_run=False)
        session.commit()

        req = session.query(Requirement).filter_by(requirement_id="REQ-AGE-001").first()
        req.value_json = json.dumps(30)
        session.commit()

        second = compile_approved_requirements(session, dry_run=False)
        assert second[0]["status"] == "created"
        rule = session.query(RuleORM).filter_by(rule_id="RULE-REQ-AGE-001").first()
        assert json.loads(rule.value_json) == 30

    def test_recompiling_an_already_active_rule_reports_unchanged(self, session):
        """The exact production scenario: a rule that has already been
        activated (not just DRAFT) gets recompiled on a later, unrelated
        sync of the same program. Must report unchanged and must not touch
        updated_at - activate_rule's ACTIVE-protection path in
        upsert_from_compiled is a different code branch from the plain-DRAFT
        case above and needs its own coverage."""
        _create_approved_requirement(session)
        session.commit()
        compile_approved_requirements(session, dry_run=False)
        session.commit()

        rule_repo = EnhancedRuleRepo(session)
        rule_repo.validate_rule("RULE-REQ-AGE-001", actor="test")
        rule_repo.approve_rule("RULE-REQ-AGE-001", actor="test")
        rule_repo.activate_rule("RULE-REQ-AGE-001", actor="test")
        session.commit()

        rule_before = session.query(RuleORM).filter_by(rule_id="RULE-REQ-AGE-001").first()
        assert rule_before.status == "ACTIVE"
        updated_at_before = rule_before.updated_at

        results = compile_approved_requirements(session, dry_run=False)
        assert results[0]["status"] == "unchanged"
        rules_created = sum(1 for r in results if r["status"] == "created")
        assert rules_created == 0

        rule_after = session.query(RuleORM).filter_by(rule_id="RULE-REQ-AGE-001").first()
        assert rule_after.status == "ACTIVE"
        assert rule_after.updated_at == updated_at_before
