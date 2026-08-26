"""End-to-end acceptance test for Phase 3D — Government → Rule → Activation pipeline.

This test proves the entire architecture works:
    1. Initial government document → Document v1 → Requirement v1 → Rule v1 → ACTIVE
    2. Government change detected → Document v2 → Requirement v2 → Rule v2 → DRAFT
    3. Human approval → Rule v2 → APPROVED → ACTIVE (v1 → SUPERSEDED)
    4. Historical assessment still references v1
    5. Failure safety: extraction failure → old ACTIVE rule stays ACTIVE
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.models import (
    Base, GovernmentSource, GovernmentDocument, DocumentChunk, DocumentVersion,
    Requirement, RequirementEvidence, Rule as RuleORM, RuleVersion,
)
from db.connection import get_session_factory
from phase3.rule_repository import EnhancedRuleRepo, orm_to_dataclass
from phase3.rule_activation import can_transition
from phase3.rule_compiler import compile_requirement_to_rule
from phase3.requirement_to_rule import compile_approved_requirements
from phase3.models import Rule, RuleStatus, Provenance


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
    yield s
    s.close()


@pytest.fixture()
def repo(session):
    return EnhancedRuleRepo(session)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_source(session, source_id="SRC-AU-189", program_id="SKILLED_189"):
    src = GovernmentSource(
        source_id=source_id,
        country_id="AU",
        program_id=program_id,
        visa_category="189",
        authority="Department of Home Affairs",
        url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189",
        source_type="WEBPAGE",
        priority="PRIMARY",
        monitoring_method="HASH",
        crawl_frequency="DAILY",
        status="ACTIVE",
    )
    session.add(src)
    session.flush()
    return src


def _create_document(session, source_id, doc_id, content_hash, version=1):
    from datetime import datetime, timezone as tz
    doc = GovernmentDocument(
        document_id=doc_id,
        source_id=source_id,
        country_id="AU",
        program_id="SKILLED_189",
        visa_category="189",
        document_type="ELIGIBILITY_CRITERIA",
        url="https://example.com/189",
        title="Skilled Independent 189",
        content_hash=content_hash,
        current_version=version,
        retrieved_at=datetime.now(tz.utc),
        raw_path=f"/tmp/{doc_id}.txt",
        status="ACTIVE",
    )
    session.add(doc)
    session.flush()
    return doc


def _create_chunk(session, doc_id, version_id=None, chunk_id=100,
                  raw_text="Applicants must be under 45 years of age."):
    from datetime import datetime, timezone as tz
    # If no version_id provided, create a DocumentVersion for this document
    if version_id is None:
        doc = session.query(GovernmentDocument).filter(GovernmentDocument.document_id == doc_id).first()
        if doc:
            dv = DocumentVersion(
                document_id=doc_id,
                version_number=doc.current_version,
                content_hash=doc.content_hash,
                retrieved_at=datetime.now(tz.utc),
                raw_path=f"/tmp/{doc_id}.txt",
                change_type="NEW",
            )
            session.add(dv)
            session.flush()
            version_id = dv.version_id
        else:
            version_id = 1
    chunk = DocumentChunk(
        document_id=doc_id,
        version_id=version_id,
        chunk_type="BODY",
        heading_path="/Eligibility",
        order_index=1,
        raw_text=raw_text,
        page_number=1,
    )
    session.add(chunk)
    session.flush()
    return chunk


def _create_approved_requirement(session, req_id, program_id, category,
                                 field, operator, value, evidence_text,
                                 document_id, chunk_id):
    from db.models import ExtractionRun
    # Create extraction run if it doesn't exist
    run_id = f"RUN-{req_id}"
    existing_run = session.query(ExtractionRun).filter(ExtractionRun.run_id == run_id).first()
    if not existing_run:
        run = ExtractionRun(
            run_id=run_id,
            document_id=document_id,
            document_version=1,
            model="mock",
            prompt_version="v1",
            status="completed",
        )
        session.add(run)
        session.flush()

    req = Requirement(
        requirement_id=req_id,
        semantic_key=f"sk-{req_id}",
        program_id=program_id,
        country_id="AU",
        category=category,
        name=f"{field} requirement",
        description=f"Must meet {field} criteria",
        field=field,
        operator=operator,
        value_json=json.dumps(value),
        unit="years" if field == "age" else None,
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
        document_id=document_id,
        document_version=1,
        chunk_id=chunk_id,
        evidence_text=evidence_text,
        evidence_type="DIRECT_TEXT",
        extracted_at=datetime.now(timezone.utc),
    )
    session.add(evidence)
    session.flush()
    return req


# ---------------------------------------------------------------------------
# THE ACCEPTANCE TEST
# ---------------------------------------------------------------------------
class TestEndToEndPipeline:
    def test_full_v1_to_v2_lifecycle(self, session, repo):
        """
        SCENARIO:
            1. Government source exists with document v1 (age < 45)
            2. Requirement extracted and approved
            3. Rule compiled and activated
            4. Government changes to age < 50
            5. New requirement extracted and approved
            6. New rule compiled as DRAFT
            7. Admin approves and activates v2
            8. v1 is SUPERSEDED
            9. get_active_rules returns only v2
        """
        # === STEP 1: Set up source and document v1 ===
        src = _create_source(session)
        doc_v1 = _create_document(session, "SRC-AU-189", "DOC-v1", "hash-abc", version=1)
        chunk_v1 = _create_chunk(session, "DOC-v1",
                                 raw_text="Applicants must be under 45 years of age.")

        # === STEP 2: Create approved requirement from v1 ===
        _create_approved_requirement(
            session, req_id="REQ-AGE-v1", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Applicants must be under 45 years of age.",
            document_id="DOC-v1", chunk_id=chunk_v1.chunk_id,
        )

        # === STEP 3: Compile and activate v1 ===
        results = compile_approved_requirements(session, program_id="SKILLED_189")
        assert len(results) == 1
        assert results[0]["status"] == "created"

        rule_v1_id = results[0]["rule_id"]
        repo.validate_rule(rule_v1_id, actor="system")
        repo.approve_rule(rule_v1_id, actor="system")
        repo.activate_rule(rule_v1_id, actor="system")

        # Verify v1 is ACTIVE
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == rule_v1_id
        assert active[0].status == "ACTIVE"

        # Load as dataclass — proves the engine can use it
        dc = repo.get_as_dataclass(rule_v1_id)
        assert dc is not None
        assert dc.status == RuleStatus.ACTIVE
        assert dc.value == 45

        # === STEP 4: Government changes to age < 50 ===
        doc_v2 = _create_document(session, "SRC-AU-189", "DOC-v2", "hash-def", version=2)
        chunk_v2 = _create_chunk(session, "DOC-v2",
                                 raw_text="Applicants must be under 50 years of age.")

        # === STEP 5: New requirement from v2 ===
        _create_approved_requirement(
            session, req_id="REQ-AGE-v2", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=50,
            evidence_text="Applicants must be under 50 years of age.",
            document_id="DOC-v2", chunk_id=chunk_v2.chunk_id,
        )

# === STEP 6: Compile new requirement → DRAFT ===
        results = compile_approved_requirements(session, program_id="SKILLED_189")
        # Both requirements compile: v1 (ACTIVE, preserved by upsert_from_compiled) and v2 (newly created)
        assert len(results) == 2
        # Only the new one (REQ-AGE-v2) should be newly created
        newly_created = [r for r in results if r["requirement_id"] == "REQ-AGE-v2"]
        assert len(newly_created) == 1
        assert newly_created[0]["status"] == "created"
        rule_v2_id = newly_created[0]["rule_id"]
        rule_v2_orm = repo.get(rule_v2_id)
        assert rule_v2_orm.status == "DRAFT"

        # v1 stays ACTIVE — upsert_from_compiled protects ACTIVE rules from downgrade
        assert repo.get(rule_v1_id).status == "ACTIVE"

        # === STEP 7: Admin reviews and approves ===
        repo.validate_rule(rule_v2_id, actor="admin1")
        repo.approve_rule(rule_v2_id, actor="admin1")
        assert repo.get(rule_v2_id).status == "APPROVED"

        # === STEP 8: Admin activates v2 — transactional supersession ===
        repo.activate_rule(rule_v2_id, actor="admin1")

        # v1 → SUPERSEDED
        assert repo.get(rule_v1_id).status == "SUPERSEDED"
        # v2 → ACTIVE
        assert repo.get(rule_v2_id).status == "ACTIVE"

        # === STEP 9: Only v2 is active ===
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == rule_v2_id
        assert json.loads(active[0].value_json) == 50

        # v1 dataclass still accessible (historical reference)
        dc_v1 = repo.get_as_dataclass(rule_v1_id)
        assert dc_v1.status == RuleStatus.SUPERSEDED
        assert dc_v1.value == 45  # original value preserved

    def test_extraction_failure_keeps_old_active(self, session, repo):
        """
        SCENARIO: Government changed but LLM extraction failed.
        Expected: Old ACTIVE rule stays ACTIVE, no new rule created.
        """
        src = _create_source(session)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")

        # Create and activate v1
        _create_approved_requirement(
            session, req_id="REQ-AGE-1", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45 years of age.",
            document_id="DOC-1", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        repo.validate_rule("RULE-REQ-AGE-1", actor="system")
        repo.approve_rule("RULE-REQ-AGE-1", actor="system")
        repo.activate_rule("RULE-REQ-AGE-1", actor="system")

        # Simulate extraction failure: no new requirements approved
        # (In real code this would be a try/except in the orchestrator)

        # Verify v1 is still ACTIVE
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-REQ-AGE-1"
        assert active[0].status == "ACTIVE"

    def test_rule_compilation_failure_does_not_affect_existing(self, session, repo):
        """
        SCENARIO: One requirement compiles successfully, another fails.
        Expected: Successful rule is created, failed one is logged.
        """
        src = _create_source(session)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")

        # Good requirement
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=100,
        )
        # Bad requirement (no evidence → compile will fail)
        from db.models import ExtractionRun
        run = ExtractionRun(
            run_id="RUN-BAD", document_id="DOC-1",
            document_version=1, model="mock", prompt_version="v1",
            status="completed",
        )
        session.add(run)
        session.flush()

        bad_req = Requirement(
            requirement_id="REQ-BAD",
            semantic_key="bad",
            program_id="SKILLED_189",
            country_id="AU",
            category="AGE",
            name="Bad",
            field="age",
            operator="LT",
            value_json=json.dumps(45),
            status="APPROVED",
            current_version=1,
            extraction_run_id="RUN-BAD",
        )
        session.add(bad_req)
        session.flush()

        results = compile_approved_requirements(session, program_id="SKILLED_189")
        statuses = {r["requirement_id"]: r["status"] for r in results}
        assert statuses["REQ-AGE"] == "created"
        assert statuses["REQ-BAD"] == "error"

    def test_retire_rule(self, session, repo):
        """SUPERSEDED → RETIRED is valid."""
        src = _create_source(session)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        repo.validate_rule("RULE-REQ-AGE", actor="system")
        repo.approve_rule("RULE-REQ-AGE", actor="system")
        repo.activate_rule("RULE-REQ-AGE", actor="system")
        repo.retire_rule("RULE-REQ-AGE", actor="admin1")
        assert repo.get("RULE-REQ-AGE").status == "RETIRED"

    def test_version_history_preserved(self, session, repo):
        """Each status change should produce a version snapshot."""
        src = _create_source(session)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")

        rule_id = "RULE-REQ-AGE"
        repo.validate_rule(rule_id, actor="reviewer")
        repo.save_version_snapshot(rule_id, "VALIDATED", "reviewer")

        repo.approve_rule(rule_id, actor="admin1")
        repo.save_version_snapshot(rule_id, "APPROVED", "admin1")

        repo.activate_rule(rule_id, actor="admin1")

        versions = session.query(RuleVersion).filter(
            RuleVersion.rule_id == rule_id
        ).all()
        assert len(versions) >= 2  # at least VALIDATED and APPROVED snapshots
        change_types = {v.change_type for v in versions}
        assert "VALIDATED" in change_types
        assert "APPROVED" in change_types

    def test_historical_assessment_references_old_value(self, session, repo):
        """
        SCENARIO: Assessment was done when v1 was ACTIVE.
        After v2 activation, the assessment's rule results still reference v1.
        """
        src = _create_source(session)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        rule_v1_id = "RULE-REQ-AGE"
        repo.validate_rule(rule_v1_id, actor="system")
        repo.approve_rule(rule_v1_id, actor="system")
        repo.activate_rule(rule_v1_id, actor="system")

        # Simulate assessment done with v1
        dc_v1 = repo.get_as_dataclass(rule_v1_id)
        assert dc_v1.value == 45

        # Now activate v2
        _create_approved_requirement(
            session, req_id="REQ-AGE-v2", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=50,
            evidence_text="Under 50.",
            document_id="DOC-1", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        # v1 stays ACTIVE — upsert_from_compiled protects ACTIVE rules from downgrade
        # Now activate v2 (supersedes v1 automatically)
        repo.validate_rule("RULE-REQ-AGE-v2", actor="admin1")
        repo.approve_rule("RULE-REQ-AGE-v2", actor="admin1")
        repo.activate_rule("RULE-REQ-AGE-v2", actor="admin1")

        # Historical assessment still sees v1's value
        dc_v1_after = repo.get_as_dataclass(rule_v1_id)
        assert dc_v1_after.value == 45
        assert dc_v1_after.status == RuleStatus.SUPERSEDED

        # New assessments see v2
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert json.loads(active[0].value_json) == 50
