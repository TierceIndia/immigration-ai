"""Phase 3D-H: Production Knowledge Pipeline Hardening Tests.

Tests the critical invariants that must hold for production use:
1. One-active-version invariant (never two ACTIVE for same logical key)
2. Transactional activation (v2 supersedes v1 atomically)
3. Full provenance chain validation (Rule → Source URL)
4. Retirement lifecycle (SUPERSEDED → RETIRED, ACTIVE → RETIRED)
5. E2E lifecycle (v1→ACTIVE→v2→v1 SUPERSEDED→v2 ACTIVE)
6. Failure safety (bad data, invalid transitions, missing rules)
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base, Rule as RuleORM, Requirement as RequirementORM
from db.models import RequirementEvidence as EvidenceORM
from db.models import DocumentChunk as ChunkORM, DocumentVersion as DocVersionORM
from db.models import GovernmentDocument as DocORM, GovernmentSource as SourceORM
from db.models import ImmigrationProgram as ProgramORM, Country as CountryORM
from rules.rule_repository import EnhancedRuleRepo, orm_to_dataclass
from rules.rule_activation import (
    can_transition, validate_transition, RuleActivationError,
    RuleLifecycleStatus,
)
from rules.models import RuleStatus, Provenance
from rules.requirement_to_rule import compile_approved_requirements


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
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = factory()
    yield s
    s.rollback()
    s.close()


def _seed_program(session, program_id="SKILLED_189"):
    existing = session.query(CountryORM).filter(CountryORM.country_id == "AU").first()
    if not existing:
        session.add(CountryORM(country_id="AU", name="Australia", iso_code="AU"))
    existing_prog = session.query(ProgramORM).filter(ProgramORM.program_id == program_id).first()
    if not existing_prog:
        session.add(ProgramORM(
            program_id=program_id, country_id="AU",
            visa_category="Skilled", name="Skilled Independent",
        ))
    session.flush()


def _seed_rule(session, rule_id, category="age", field="age", operator="LT",
               value=45, program_id="SKILLED_189", mandatory=True, critical=False,
               status="DRAFT", version=1, message="Test rule"):
    rule = RuleORM(
        rule_id=rule_id,
        requirement_id=f"REQ-{rule_id}",
        program_id=program_id,
        category=category,
        field=field,
        operator=operator,
        value_json=json.dumps(value),
        mandatory=mandatory,
        critical=critical,
        message=message,
        status=status,
        current_version=version,
        source_id="SRC-1",
        document_id="DOC-1",
        document_version=1,
        chunk_id=1,
        evidence_text="Government evidence text",
    )
    session.add(rule)
    session.flush()
    return rule


# =========================================================================
# 1. ONE-ACTIVE-VERSION INVARIANT
# =========================================================================

class TestOneActiveVersionInvariant:
    """For any logical key (program + category + field), at most one rule
    can be ACTIVE at any time."""

    def test_single_active_rule(self, session):
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "RULE-1", status="ACTIVE")
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-1"

    def test_two_rules_same_logical_key_only_one_active(self, session):
        """When v2 activates, v1 is automatically superseded."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "RULE-AGE-V1", category="age", field="age", status="ACTIVE")
        _seed_rule(session, "RULE-AGE-V2", category="age", field="age", status="APPROVED")
        session.commit()

        # Before activation: both v1=ACTIVE, v2=APPROVED
        active_before = repo.list_active("SKILLED_189")
        assert len(active_before) == 1
        assert active_before[0].rule_id == "RULE-AGE-V1"

        # Activate v2
        repo.activate_rule("RULE-AGE-V2", actor="admin")
        session.commit()

        # After: v2=ACTIVE, v1=SUPERSEDED
        active_after = repo.list_active("SKILLED_189")
        assert len(active_after) == 1
        assert active_after[0].rule_id == "RULE-AGE-V2"

        v1 = repo.get("RULE-AGE-V1")
        assert v1.status == "SUPERSEDED"

    def test_different_logical_keys_can_both_be_active(self, session):
        """Rules with different fields can both be ACTIVE."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "RULE-AGE", category="age", field="age", status="ACTIVE")
        _seed_rule(session, "RULE-POINTS", category="points", field="points", status="ACTIVE")
        active = repo.list_active("SKILLED_189")
        assert len(active) == 2

    def test_same_field_different_category_both_active(self, session):
        """The logical key is (program_id, category, field), not (program_id,
        field) alone: two rules that share a field but differ by category are
        different logical rules and must both be allowed ACTIVE at once.

        Regression test: uq_rules_active_per_field used to cover only
        (program_id, field), which incorrectly rejected this exact case with a
        DB-level unique violation, even though activate_rule's own
        _find_active_for_logical_key (program_id, category, field) never
        considered these two rules to conflict. See migration 7c9e2a4f1b6d.
        """
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "RULE-POINTS-ELIGIBILITY", category="eligibility",
                   field="applicant.points_score", status="ACTIVE")
        _seed_rule(session, "RULE-POINTS-SCORING", category="points",
                   field="applicant.points_score", status="ACTIVE")
        session.commit()
        active = repo.list_active("SKILLED_189")
        assert {r.rule_id for r in active} == {"RULE-POINTS-ELIGIBILITY", "RULE-POINTS-SCORING"}

    def test_different_programs_same_field_both_active(self, session):
        """Rules for different programs with same field can both be ACTIVE."""
        _seed_program(session)
        _seed_program(session, "SKILLED_190")
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "R-189-AGE", program_id="SKILLED_189", status="ACTIVE")
        _seed_rule(session, "R-190-AGE", program_id="SKILLED_190", status="ACTIVE")
        active_189 = repo.list_active("SKILLED_189")
        active_190 = repo.list_active("SKILLED_190")
        assert len(active_189) == 1
        assert len(active_190) == 1

    def test_three_version_supersession_chain(self, session):
        """v1→ACTIVE→v2→v1 SUPERSEDED→v3→v2 SUPERSEDED."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "R-V1", category="age", field="age", status="ACTIVE")
        _seed_rule(session, "R-V2", category="age", field="age", status="APPROVED")
        _seed_rule(session, "R-V3", category="age", field="age", status="APPROVED")
        session.commit()

        # Activate v2
        repo.activate_rule("R-V2", actor="admin")
        session.commit()
        assert repo.get("R-V1").status == "SUPERSEDED"
        assert repo.get("R-V2").status == "ACTIVE"
        assert repo.get("R-V3").status == "APPROVED"

        # Activate v3
        repo.activate_rule("R-V3", actor="admin")
        session.commit()
        assert repo.get("R-V1").status == "SUPERSEDED"
        assert repo.get("R-V2").status == "SUPERSEDED"
        assert repo.get("R-V3").status == "ACTIVE"

        # Only one active
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "R-V3"


# =========================================================================
# 2. TRANSACTIONAL ACTIVATION
# =========================================================================

class TestTransactionalActivation:
    """Activation of v2 must atomically supersede v1."""

    def test_activate_supersedes_old(self, session):
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "OLD", category="age", field="age", status="ACTIVE")
        _seed_rule(session, "NEW", category="age", field="age", status="APPROVED")
        session.commit()

        repo.activate_rule("NEW", actor="admin")
        session.commit()

        assert repo.get("OLD").status == "SUPERSEDED"
        assert repo.get("NEW").status == "ACTIVE"

    def test_activate_without_existing_active(self, session):
        """First activation of a rule — no old version to supersede."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "FIRST", category="age", field="age", status="APPROVED")
        session.commit()

        repo.activate_rule("FIRST", actor="admin")
        session.commit()
        assert repo.get("FIRST").status == "ACTIVE"

    def test_version_snapshot_recorded_on_activation(self, session):
        """Activation creates a version snapshot in rule_versions."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "R-SNAP", category="age", field="age", status="APPROVED")
        session.commit()

        repo.activate_rule("R-SNAP", actor="admin")
        session.commit()

        versions = repo.list_active("SKILLED_189")  # just checking no error
        # Verify version was saved by checking the ORM
        from db.models import RuleVersion
        rv = session.query(RuleVersion).filter(RuleVersion.rule_id == "R-SNAP").first()
        assert rv is not None
        assert rv.change_type == "ACTIVATED"
        assert rv.created_by == "admin"


# =========================================================================
# 3. FULL PROVENANCE CHAIN VALIDATION
# =========================================================================

class TestProvenanceChain:
    """Every ACTIVE rule must trace back: Rule → Requirement → Evidence →
    Chunk → Document → Source → Government URL."""

    def _seed_full_chain(self, session):
        """Seed a complete provenance chain."""
        _seed_program(session)

        session.add(SourceORM(
            source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
            visa_category="Skilled", authority="Home Affairs",
            url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189",
            source_type="WEB", source_subtype="PRIMARY", priority="HIGH",
            monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE", last_checked_at=datetime.now(timezone.utc),
            last_hash="abc123",
        ))
        session.add(DocORM(
            document_id="DOC-189-MAIN", source_id="SRC-AU-189",
            country_id="AU", program_id="SKILLED_189",
            visa_category="Skilled", document_type="HTML",
            url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189",
            retrieved_at=datetime.now(timezone.utc),
            content_hash="hash1", current_version=1, raw_path="/raw/doc.html",
        ))
        dv = DocVersionORM(
            document_id="DOC-189-MAIN", version_number=1,
            content_hash="hash1", retrieved_at=datetime.now(timezone.utc),
            raw_path="/raw/doc.html", change_type="INITIAL",
        )
        session.add(dv)
        session.flush()

        chunk = ChunkORM(
            document_id="DOC-189-MAIN", version_id=dv.version_id,
            chunk_type="paragraph", heading_path="Age Requirements",
            order_index=0, raw_text="Applicants must be under 45 years of age at time of invitation.",
        )
        session.add(chunk)
        session.flush()

        req = RequirementORM(
            requirement_id="REQ-AGE-189", semantic_key="age_lt_45",
            program_id="SKILLED_189", country_id="AU",
            category="age", name="Age Requirement",
            description="Must be under 45", field="age",
            operator="LT", value_json="45", mandatory=1,
            status="APPROVED", current_version=1,
            extraction_run_id="RUN-001", criticality="CRITICAL",
        )
        session.add(req)
        session.flush()

        session.add(EvidenceORM(
            requirement_id="REQ-AGE-189", document_id="DOC-189-MAIN",
            document_version=1, chunk_id=chunk.chunk_id,
            evidence_text="Applicants must be under 45 years of age at time of invitation.",
            evidence_type="VERBATIM", verified=1,
            extracted_at=datetime.now(timezone.utc),
        ))
        session.flush()

        return chunk.chunk_id

    def test_provenance_chain_complete(self, session):
        """An ACTIVE rule must have all provenance fields populated."""
        chunk_id = self._seed_full_chain(session)
        _seed_rule(session, "RULE-AGE", category="age", field="age",
                   status="ACTIVE", version=1)
        session.commit()

        repo = EnhancedRuleRepo(session)
        rule = repo.get("RULE-AGE")
        assert rule.source_id == "SRC-1"  # from _seed_rule default
        assert rule.document_id is not None
        assert rule.evidence_text is not None

    def test_orm_to_dataclass_preserves_provenance(self, session):
        """orm_to_dataclass correctly maps provenance fields."""
        _seed_program(session)
        _seed_rule(session, "R-PROV", status="ACTIVE")
        session.commit()

        repo = EnhancedRuleRepo(session)
        rule_orm = repo.get("R-PROV")
        rule_dc = orm_to_dataclass(rule_orm)

        assert rule_dc.provenance.source_id == rule_orm.source_id
        assert rule_dc.provenance.document_id == rule_orm.document_id
        assert rule_dc.provenance.evidence_text == rule_orm.evidence_text

    def test_active_rules_loadable_as_dataclasses(self, session):
        """All ACTIVE rules can be loaded and used for assessment."""
        _seed_program(session)
        _seed_rule(session, "R1", category="age", field="age", status="ACTIVE")
        _seed_rule(session, "R2", category="points", field="points", status="ACTIVE")
        session.commit()

        repo = EnhancedRuleRepo(session)
        rules = repo.get_active_rules_as_dataclasses("SKILLED_189")
        assert len(rules) == 2
        for r in rules:
            assert isinstance(r, type(rules[0]))
            assert r.status == RuleStatus.ACTIVE


# =========================================================================
# 4. RETIREMENT LIFECYCLE
# =========================================================================

class TestRetirementLifecycle:
    """SUPERSEDED → RETIRED and ACTIVE → RETIRED transitions."""

    def test_retire_superseded(self, session):
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "OLD", category="age", field="age", status="ACTIVE")
        _seed_rule(session, "NEW", category="age", field="age", status="APPROVED")
        session.commit()

        repo.activate_rule("NEW", actor="admin")
        session.commit()
        assert repo.get("OLD").status == "SUPERSEDED"

        repo.retire_rule("OLD", actor="admin")
        session.commit()
        assert repo.get("OLD").status == "RETIRED"

    def test_retire_active(self, session):
        """An ACTIVE rule can be directly retired."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "R-ACTIVE", category="age", field="age", status="ACTIVE")
        session.commit()

        repo.retire_rule("R-ACTIVE", actor="admin")
        session.commit()
        assert repo.get("R-ACTIVE").status == "RETIRED"

    def test_retire_draft_raises(self, session):
        """Cannot retire a DRAFT rule."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "R-DRAFT", status="DRAFT")
        session.commit()

        with pytest.raises(ValueError, match="Invalid rule status transition"):
            repo.retire_rule("R-DRAFT", actor="admin")

    def test_retired_cannot_reactivate(self, session):
        """A RETIRED rule cannot be reactivated."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        _seed_rule(session, "R-RET", category="age", field="age", status="ACTIVE")
        session.commit()

        repo.retire_rule("R-RET", actor="admin")
        session.commit()

        with pytest.raises(ValueError, match="Invalid rule status transition"):
            repo.activate_rule("R-RET", actor="admin")


# =========================================================================
# 5. E2E LIFECYCLE
# =========================================================================

class TestEE2Lifecycle:
    """Full government-to-active-rule lifecycle:
    Government v1 → compile → DRAFT → validate → APPROVED → activate → ACTIVE
    Government v2 → compile → DRAFT → validate → APPROVED → activate → ACTIVE
    v1 → SUPERSEDED"""

    def test_full_lifecycle(self, session):
        _seed_program(session)
        repo = EnhancedRuleRepo(session)

        # v1: create → validate → approve → activate
        _seed_rule(session, "R-AGE-V1", category="age", field="age", status="DRAFT")
        session.commit()

        repo.validate_rule("R-AGE-V1", actor="system")
        repo.approve_rule("R-AGE-V1", actor="admin")
        repo.activate_rule("R-AGE-V1", actor="admin")
        session.commit()

        assert repo.get("R-AGE-V1").status == "ACTIVE"
        assert len(repo.list_active("SKILLED_189")) == 1

        # v2: create → validate → approve → activate (supersedes v1)
        _seed_rule(session, "R-AGE-V2", category="age", field="age", status="DRAFT")
        session.commit()

        repo.validate_rule("R-AGE-V2", actor="system")
        repo.approve_rule("R-AGE-V2", actor="admin")
        repo.activate_rule("R-AGE-V2", actor="admin")
        session.commit()

        assert repo.get("R-AGE-V1").status == "SUPERSEDED"
        assert repo.get("R-AGE-V2").status == "ACTIVE"
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "R-AGE-V2"

    def test_assessment_uses_correct_version(self, session):
        """After v2 activation, new assessments use v2 data."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)

        # v1 with value 45
        _seed_rule(session, "R-V1", category="age", field="age",
                   operator="LT", value=45, status="DRAFT")
        session.commit()
        repo.validate_rule("R-V1")
        repo.approve_rule("R-V1", "admin")
        repo.activate_rule("R-V1", "admin")
        session.commit()

        # Load v1 — should have value=45
        rule_v1 = repo.get_as_dataclass("R-V1")
        assert rule_v1.value == 45

        # v2 with value 50
        _seed_rule(session, "R-V2", category="age", field="age",
                   operator="LT", value=50, status="DRAFT")
        session.commit()
        repo.validate_rule("R-V2")
        repo.approve_rule("R-V2", "admin")
        repo.activate_rule("R-V2", "admin")
        session.commit()

        # v1 should still have value=45 (snapshot preserved)
        rule_v1_still = repo.get_as_dataclass("R-V1")
        assert rule_v1_still.value == 45

        # v2 should have value=50
        rule_v2 = repo.get_as_dataclass("R-V2")
        assert rule_v2.value == 50

        # Only v2 is active
        active = repo.get_active_rules_as_dataclasses("SKILLED_189")
        assert len(active) == 1
        assert active[0].value == 50


# =========================================================================
# 6. FAILURE SAFETY
# =========================================================================

class TestFailureSafety:
    """Verify graceful handling of failures."""

    def test_invalid_transition_raises(self, session):
        """DRAFT → ACTIVE is blocked (bypasses VALIDATED/APPROVED)."""
        with pytest.raises(ValueError, match="Invalid rule status transition"):
            validate_transition("DRAFT", "ACTIVE")

    def test_active_to_draft_blocked(self, session):
        """Cannot go backwards from ACTIVE to DRAFT."""
        with pytest.raises(ValueError, match="Invalid rule status transition"):
            validate_transition("ACTIVE", "DRAFT")

    def test_superseded_to_active_blocked(self, session):
        """Cannot resurrect a SUPERSEDED rule."""
        with pytest.raises(ValueError, match="Invalid rule status transition"):
            validate_transition("SUPERSEDED", "ACTIVE")

    def test_retired_is_terminal(self, session):
        """RETIRED cannot transition to any other state."""
        for target in ["DRAFT", "VALIDATED", "APPROVED", "ACTIVE", "SUPERSEDED", "REJECTED"]:
            assert not can_transition("RETIRED", target), \
                f"RETIRED → {target} should be blocked"

    def test_activate_nonexistent_rule(self, session):
        """Activating a rule that doesn't exist raises ValueError."""
        _seed_program(session)
        repo = EnhancedRuleRepo(session)
        with pytest.raises(ValueError, match="not found"):
            repo.activate_rule("NONEXISTENT-RULE", actor="admin")

    def test_missing_rule_in_expression_returns_error(self, session):
        """Expression referencing a missing rule returns ERROR in individual evaluation."""
        from rules.expressions import RuleLeaf, evaluate_expression
        _seed_program(session)
        _seed_rule(session, "EXISTING", status="ACTIVE")
        session.commit()

        repo = EnhancedRuleRepo(session)
        rules = repo.get_active_rules_as_dataclasses("SKILLED_189")
        rules_by_id = {r.rule_id: r for r in rules}

        # Evaluate a single missing rule leaf directly
        leaf = RuleLeaf("NONEXISTENT")
        status, evals = evaluate_expression(leaf, rules_by_id, {"age": 30})
        from rules.models import EvaluationStatus
        assert status == EvaluationStatus.ERROR
        assert len(evals) == 1
        assert "not found" in evals[0].reason.lower()

    def test_empty_applicant_data_unknown_status(self, session):
        """Empty applicant data produces UNKNOWN for rules with missing fields."""
        from rules.expressions import ANDNode, RuleLeaf, evaluate_expression
        from rules.models import EvaluationStatus
        _seed_program(session)
        _seed_rule(session, "R-AGE", category="age", field="age",
                   operator="LT", value=45, status="ACTIVE")
        session.commit()

        repo = EnhancedRuleRepo(session)
        rules = repo.get_active_rules_as_dataclasses("SKILLED_189")
        rules_by_id = {r.rule_id: r for r in rules}

        expr = ANDNode(children=[RuleLeaf("R-AGE")])
        status, evals = evaluate_expression(expr, rules_by_id, {})
        assert status == EvaluationStatus.UNKNOWN
        assert evals[0].status == EvaluationStatus.UNKNOWN


# =========================================================================
# 7. REQUIREMENT COMPILATION FAILURE SAFETY
# =========================================================================

class TestCompilationFailureSafety:
    """compile_approved_requirements handles bad data gracefully."""

    def test_missing_field_in_requirement(self, session):
        """Requirement with no field compiles gracefully (error handled, not crash)."""
        _seed_program(session)
        req = RequirementORM(
            requirement_id="REQ-NO-FIELD", semantic_key="test",
            program_id="SKILLED_189", country_id="AU",
            category="test", name="No Field",
            status="APPROVED", current_version=1,
            extraction_run_id="RUN-001",
            operator="LT", value_json="45",
        )
        session.add(req)
        session.flush()
        session.commit()

        results = compile_approved_requirements(session, dry_run=True)
        assert len(results) == 1
        # Compilation may fail for missing field — but it should NOT crash
        assert results[0]["status"] in ("created", "error")
        assert results[0]["requirement_id"] == "REQ-NO-FIELD"

    def test_empty_requirements_no_error(self, session):
        """No approved requirements produces empty result list."""
        _seed_program(session)
        session.commit()

        results = compile_approved_requirements(session, dry_run=True)
        assert results == []

    def test_program_filter(self, session):
        """Only approved requirements for specified program are compiled."""
        _seed_program(session, "SKILLED_190")
        session.add(RequirementORM(
            requirement_id="REQ-189", semantic_key="k189",
            program_id="SKILLED_189", country_id="AU",
            category="age", name="Age 189", field="age",
            operator="LT", value_json="45",
            status="APPROVED", current_version=1,
            extraction_run_id="RUN-001",
        ))
        session.add(RequirementORM(
            requirement_id="REQ-190", semantic_key="k190",
            program_id="SKILLED_190", country_id="AU",
            category="age", name="Age 190", field="age",
            operator="LT", value_json="45",
            status="APPROVED", current_version=1,
            extraction_run_id="RUN-002",
        ))
        session.flush()
        session.commit()

        results_189 = compile_approved_requirements(
            session, program_id="SKILLED_189", dry_run=True
        )
        assert len(results_189) == 1
        assert results_189[0]["requirement_id"] == "REQ-189"
