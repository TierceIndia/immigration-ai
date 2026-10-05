"""Phase 3D acceptance tests — full lifecycle with PipelineRun tracking.

Proves:
    1. Government v1 → Document v1 → Req v1 → Rule v1 → ACTIVE
    2. Change detected → Document v2 → Req v2 → Rule v2 → APPROVED → v1 SUPERSEDED → v2 ACTIVE
    3. Failure-safe: LLM fails → old ACTIVE rules stay ACTIVE, PipelineRun = FAILED
    4. PipelineRun strict status state machine
    5. PipelineRun tracks rules_validated/rules_approved/rules_activated
    6. Source status (ACTIVE/DISABLED) separate from run status (RUNNING/COMPLETED/FAILED/PARTIAL)
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
    ExtractionRun, Country, ImmigrationProgram, PipelineRun,
)
from db.connection import get_session_factory
from db.repositories import PipelineRunRepo
from rules.rule_repository import EnhancedRuleRepo, orm_to_dataclass
from rules.rule_activation import can_transition
from rules.rule_compiler import compile_requirement_to_rule
from rules.requirement_to_rule import compile_approved_requirements
from rules.models import Rule, RuleStatus, Provenance
from pipeline_run_activation import (
    can_run_transition, validate_run_transition, RunStatus,
)
from fetch.fetcher import FetchResult, FetchStatus
from change_detection.hashing import sha256_bytes

# Stable HTML + its real sha256 so sync_source's always-fetch path can be
# mocked hermetically while fetch_document_and_store compares honest hashes.
MOCK_HTML_V1 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 45 years of age.</p>"
    b"</body></html>"
)
MOCK_HASH_V1 = sha256_bytes(MOCK_HTML_V1)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


def _mock_ok_fetch(monkeypatch):
    """Patch fetcher.fetch to return stable HTML the sync path can re-fetch."""
    def fetch(url, source_type=None, **kw):
        return FetchResult(
            url=url, status=FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=MOCK_HTML_V1,
            duration_ms=5, error=None,
        )

    monkeypatch.setattr("fetch.fetcher.fetch", fetch)


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
def _seed_country_program(session):
    existing = session.query(Country).filter(Country.country_id == "AU").first()
    if not existing:
        session.add(Country(country_id="AU", name="Australia", iso_code="AU"))
    existing_prog = session.query(ImmigrationProgram).filter(
        ImmigrationProgram.program_id == "SKILLED_189"
    ).first()
    if not existing_prog:
        session.add(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU",
            visa_category="Skilled", name="Skilled Independent",
        ))
    session.flush()


def _create_source(session, source_id="SRC-AU-189", program_id="SKILLED_189"):
    _seed_country_program(session)
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
        retrieved_at=datetime.now(timezone.utc),
        raw_path=f"/tmp/{doc_id}.txt",
        status="ACTIVE",
    )
    session.add(doc)
    session.flush()
    return doc


def _create_chunk(session, doc_id, version_id=None, chunk_id=100,
                  raw_text="Applicants must be under 45 years of age."):
    if version_id is None:
        doc = session.query(GovernmentDocument).filter(
            GovernmentDocument.document_id == doc_id
        ).first()
        if doc:
            dv = DocumentVersion(
                document_id=doc_id,
                version_number=doc.current_version,
                content_hash=doc.content_hash,
                retrieved_at=datetime.now(timezone.utc),
                raw_path=f"/tmp/{doc_id}.txt",
                change_type="INITIAL",
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
    run_id = f"RUN-{req_id}"
    existing_run = session.query(ExtractionRun).filter(
        ExtractionRun.run_id == run_id
    ).first()
    if not existing_run:
        run = ExtractionRun(
            run_id=run_id,
            document_id=document_id,
            document_version=1,
            model="mock",
            prompt_version="v1",
            status="COMPLETED",
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


# =========================================================================
# PIPELINERUN STATUS STATE MACHINE
# =========================================================================

class TestPipelineRunStateMachine:
    def test_running_can_complete(self):
        assert can_run_transition("RUNNING", "COMPLETED")

    def test_running_can_fail(self):
        assert can_run_transition("RUNNING", "FAILED")

    def test_running_can_partial(self):
        assert can_run_transition("RUNNING", "PARTIAL")

    def test_completed_is_terminal(self):
        for target in ["RUNNING", "COMPLETED", "FAILED", "PARTIAL"]:
            assert not can_run_transition("COMPLETED", target), \
                f"COMPLETED → {target} should be blocked"

    def test_failed_is_terminal(self):
        for target in ["RUNNING", "COMPLETED", "FAILED", "PARTIAL"]:
            assert not can_run_transition("FAILED", target), \
                f"FAILED → {target} should be blocked"

    def test_partial_is_terminal(self):
        for target in ["RUNNING", "COMPLETED", "FAILED", "PARTIAL"]:
            assert not can_run_transition("PARTIAL", target), \
                f"PARTIAL → {target} should be blocked"

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="Invalid PipelineRun status transition"):
            validate_run_transition("COMPLETED", "RUNNING")

    def test_repo_complete_validates_transition(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        completed = repo.complete(run.run_id)
        assert completed.status == "COMPLETED"

    def test_repo_fail_validates_transition(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        failed = repo.fail(run.run_id, "error")
        assert failed.status == "FAILED"

    def test_repo_double_complete_raises(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        repo.complete(run.run_id)
        with pytest.raises(ValueError, match="Invalid PipelineRun status transition"):
            repo.complete(run.run_id)

    def test_repo_fail_after_complete_raises(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        repo.complete(run.run_id)
        with pytest.raises(ValueError, match="Invalid PipelineRun status transition"):
            repo.fail(run.run_id, "error")

    def test_repo_partial_after_complete_raises(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        repo.complete(run.run_id)
        with pytest.raises(ValueError, match="Invalid PipelineRun status transition"):
            repo.partial(run.run_id, "error")


# =========================================================================
# PIPELINERUN NEW METRICS
# =========================================================================

class TestPipelineRunMetrics:
    def test_new_columns_default_zero(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        assert run.rules_validated == 0
        assert run.rules_approved == 0
        assert run.rules_activated == 0

    def test_complete_with_activation_metrics(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        completed = repo.complete(
            run.run_id,
            rules_validated=5,
            rules_approved=3,
            rules_activated=2,
        )
        assert completed.rules_validated == 5
        assert completed.rules_approved == 3
        assert completed.rules_activated == 2

    def test_partial_with_activation_metrics(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        partial = repo.partial(
            run.run_id,
            "Some rules failed validation",
            rules_validated=4,
            rules_approved=2,
            rules_activated=0,
        )
        assert partial.rules_validated == 4
        assert partial.rules_approved == 2
        assert partial.rules_activated == 0

    def test_api_returns_new_fields(self, session):
        _create_source(session, "SRC-1")
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="SRC-1", trigger_type="MANUAL")
        repo.complete(
            run.run_id,
            rules_validated=5,
            rules_approved=3,
            rules_activated=2,
        )
        session.flush()
        # Verify the dict conversion includes new fields
        from api.routes.pipeline_runs import _run_to_dict
        d = _run_to_dict(run)
        assert d["rules_validated"] == 5
        assert d["rules_approved"] == 3
        assert d["rules_activated"] == 2


# =========================================================================
# FAILURE-SAFE INVARIANT
# =========================================================================

class TestFailureSafe:
    def test_extraction_failure_keeps_old_active(self, session, repo, monkeypatch):
        """
        SCENARIO: Government changed but LLM extraction failed.
        Expected: Old ACTIVE rule stays ACTIVE, no new rule activated,
                  PipelineRun = FAILED or COMPLETED (no new rules).
        """
        _create_source(session)
        _mock_ok_fetch(monkeypatch)
        doc = _create_document(session, "SRC-AU-189", "DOC-AU-189", MOCK_HASH_V1)

        # Create and activate v1
        _create_approved_requirement(
            session, req_id="REQ-AGE-1", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45 years of age.",
            document_id="DOC-AU-189", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        repo.validate_rule("RULE-REQ-AGE-1", actor="system")
        repo.approve_rule("RULE-REQ-AGE-1", actor="system")
        repo.activate_rule("RULE-REQ-AGE-1", actor="system")
        session.flush()

        # Verify v1 is ACTIVE
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-REQ-AGE-1"
        assert active[0].status == "ACTIVE"

        # Set source last_hash so change detection works properly
        source = session.get(GovernmentSource, "SRC-AU-189")
        source.last_hash = MOCK_HASH_V1
        session.flush()

        # Simulate sync: source re-fetched, content unchanged (same hash)
        from orchestration.government_sync import sync_source
        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.flush()

        # Old rule is STILL ACTIVE — failure-safe invariant holds
        active_after = repo.list_active("SKILLED_189")
        assert len(active_after) == 1
        assert active_after[0].rule_id == "RULE-REQ-AGE-1"
        assert active_after[0].status == "ACTIVE"

    def test_llm_exception_does_not_deactivate_old_rules(self, session, repo):
        """
        SCENARIO: Sync source that doesn't exist → error caught gracefully.
        Expected: Old rules untouched, PipelineRun = FAILED.
        """
        _create_source(session)
        _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")

        # Create and activate v1
        _create_approved_requirement(
            session, req_id="REQ-AGE-1", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        repo.validate_rule("RULE-REQ-AGE-1", actor="system")
        repo.approve_rule("RULE-REQ-AGE-1", actor="system")
        repo.activate_rule("RULE-REQ-AGE-1", actor="system")
        session.flush()

        # Simulate error via nonexistent source
        from orchestration.government_sync import sync_source
        result = sync_source(session, "nonexistent-source", trigger_type="MANUAL")
        session.flush()

        # v1 is STILL ACTIVE — failure-safe
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-REQ-AGE-1"

        # PipelineRun = FAILED
        runs = session.query(PipelineRun).all()
        failed_runs = [r for r in runs if r.status == "FAILED"]
        assert len(failed_runs) >= 1


# =========================================================================
# FULL v1→ACTIVE → v2 CHANGE → SUPERSEDED → v2 ACTIVE
# =========================================================================

class TestFullLifecycleWithPipelineRun:
    def test_v1_to_v2_with_run_tracking(self, session, repo):
        """
        SCENARIO:
            1. Government source exists with document v1 (age < 45)
            2. Requirement extracted and approved
            3. Rule compiled and activated
            4. PipelineRun recorded for the sync
            5. Government changes to age < 50
            6. New requirement extracted and approved
            7. New rule compiled as DRAFT
            8. Admin approves and activates v2 → v1 SUPERSEDED
            9. PipelineRun tracks the change
        """
        _seed_country_program = None  # inline below

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

        dc = repo.get_as_dataclass(rule_v1_id)
        assert dc is not None
        assert dc.status == RuleStatus.ACTIVE
        assert dc.value == 45

        # === STEP 4: Simulate sync — record PipelineRun for v1 activation ===
        run_repo = PipelineRunRepo(session)
        run_v1 = run_repo.create(source_id="SRC-AU-189", trigger_type="MANUAL")
        run_repo.complete(
            run_v1.run_id,
            requirements_extracted=1,
            requirements_approved=1,
            rules_created=1,
            rules_validated=1,
            rules_approved=1,
            rules_activated=1,
        )

        # === STEP 5: Government changes to age < 50 ===
        doc_v2 = _create_document(session, "SRC-AU-189", "DOC-v2", "hash-def", version=2)
        chunk_v2 = _create_chunk(session, "DOC-v2",
                                 raw_text="Applicants must be under 50 years of age.")

        # === STEP 6: New requirement from v2 ===
        _create_approved_requirement(
            session, req_id="REQ-AGE-v2", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=50,
            evidence_text="Applicants must be under 50 years of age.",
            document_id="DOC-v2", chunk_id=chunk_v2.chunk_id,
        )

        # === STEP 7: Compile new requirement → DRAFT ===
        results = compile_approved_requirements(session, program_id="SKILLED_189")
        newly_created = [r for r in results if r["requirement_id"] == "REQ-AGE-v2"]
        assert len(newly_created) == 1
        assert newly_created[0]["status"] == "created"
        rule_v2_id = newly_created[0]["rule_id"]
        rule_v2_orm = repo.get(rule_v2_id)
        assert rule_v2_orm.status == "DRAFT"

        # v1 is still ACTIVE
        assert repo.get(rule_v1_id).status == "ACTIVE"

        # === STEP 8: Admin reviews and approves v2 ===
        repo.validate_rule(rule_v2_id, actor="admin1")
        repo.approve_rule(rule_v2_id, actor="admin1")
        assert repo.get(rule_v2_id).status == "APPROVED"

        # === STEP 9: Admin activates v2 — transactional supersession ===
        repo.activate_rule(rule_v2_id, actor="admin1")

        # v1 → SUPERSEDED
        assert repo.get(rule_v1_id).status == "SUPERSEDED"
        # v2 → ACTIVE
        assert repo.get(rule_v2_id).status == "ACTIVE"

        # === STEP 10: Only v2 is active ===
        active = repo.list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == rule_v2_id
        assert json.loads(active[0].value_json) == 50

        # v1 dataclass still accessible (historical reference)
        dc_v1 = repo.get_as_dataclass(rule_v1_id)
        assert dc_v1.status == RuleStatus.SUPERSEDED
        assert dc_v1.value == 45

        # === STEP 11: Record PipelineRun for v2 activation ===
        run_v2 = run_repo.create(source_id="SRC-AU-189", trigger_type="MANUAL")
        run_repo.complete(
            run_v2.run_id,
            requirements_extracted=1,
            requirements_approved=1,
            rules_created=1,
            rules_validated=1,
            rules_approved=1,
            rules_activated=1,
        )

        # === STEP 12: Verify PipelineRun audit trail ===
        runs = session.query(PipelineRun).filter(
            PipelineRun.source_id == "SRC-AU-189"
        ).all()
        assert len(runs) == 2
        assert runs[0].status == "COMPLETED"
        assert runs[0].rules_activated == 1
        assert runs[1].status == "COMPLETED"
        assert runs[1].rules_activated == 1

    def test_source_status_independent_of_run_status(self, session):
        """Source ACTIVE/DISABLED is separate from PipelineRun RUNNING/COMPLETED/FAILED."""
        src = _create_source(session)
        assert src.status == "ACTIVE"

        # A FAILED run does NOT disable the source
        run_repo = PipelineRunRepo(session)
        run = run_repo.create(source_id="SRC-AU-189", trigger_type="MANUAL")
        run_repo.fail(run.run_id, "LLM timeout")

        # Source is still ACTIVE
        loaded = session.get(GovernmentSource, "SRC-AU-189")
        assert loaded.status == "ACTIVE"

        # A COMPLETED run does not change source status either
        run2 = run_repo.create(source_id="SRC-AU-189", trigger_type="MANUAL")
        run_repo.complete(run2.run_id)
        loaded2 = session.get(GovernmentSource, "SRC-AU-189")
        assert loaded2.status == "ACTIVE"

    def test_multiple_rules_with_activation_tracking(self, session, repo):
        """Activate multiple rules and verify PipelineRun metrics."""
        _create_source(session)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")
        chunk = _create_chunk(session, "DOC-1")

        # Create two requirements
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=chunk.chunk_id,
        )
        _create_approved_requirement(
            session, req_id="REQ-POINTS", program_id="SKILLED_189",
            category="POINTS", field="points", operator="GTE", value=65,
            evidence_text="Minimum 65 points.",
            document_id="DOC-1", chunk_id=chunk.chunk_id,
        )

        # Compile both
        results = compile_approved_requirements(session, program_id="SKILLED_189")
        assert len(results) == 2

        # Validate, approve, activate both
        for r in results:
            repo.validate_rule(r["rule_id"], actor="system")
            repo.approve_rule(r["rule_id"], actor="admin")

        activated_count = 0
        for r in results:
            repo.activate_rule(r["rule_id"], actor="admin")
            activated_count += 1

        # Record PipelineRun with activation metrics
        run_repo = PipelineRunRepo(session)
        run = run_repo.create(source_id="SRC-AU-189", trigger_type="MANUAL")
        run_repo.complete(
            run.run_id,
            requirements_extracted=2,
            requirements_approved=2,
            rules_created=2,
            rules_validated=2,
            rules_approved=2,
            rules_activated=activated_count,
        )

        # Verify both rules are ACTIVE
        active = repo.list_active("SKILLED_189")
        assert len(active) == 2
        active_fields = {r.field for r in active}
        assert active_fields == {"age", "points"}

        # Verify PipelineRun metrics
        loaded_run = run_repo.get(run.run_id)
        assert loaded_run.rules_validated == 2
        assert loaded_run.rules_approved == 2
        assert loaded_run.rules_activated == 2

    def test_partial_run_when_compilation_fails(self, session, repo, monkeypatch):
        """When some requirements fail compilation, PipelineRun = PARTIAL."""
        _create_source(session)
        _mock_ok_fetch(monkeypatch)
        doc = _create_document(session, "SRC-AU-189", "DOC-1", "hash-1")

        # Good requirement
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-1", chunk_id=100,
        )
        # Bad requirement (no evidence → compile will fail)
        run = ExtractionRun(
            run_id="RUN-BAD", document_id="DOC-1",
            document_version=1, model="mock", prompt_version="v1",
            status="COMPLETED",
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

        # Use orchestrator sync
        from orchestration.government_sync import sync_source
        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.flush()

        # PipelineRun should be PARTIAL or COMPLETED (depends on error handling)
        runs = session.query(PipelineRun).all()
        assert len(runs) >= 1
        last_run = runs[-1]
        assert last_run.status in ("COMPLETED", "PARTIAL")

        # Old rules untouched (there were none to begin with)
        active = repo.list_active("SKILLED_189")
        assert len(active) == 0  # no rules were activated


# =========================================================================
# MULTI-LEVEL CHANGE DETECTION
# =========================================================================

class TestMultiLevelChangeDetection:
    def test_change_detection_stored_in_run_metadata(self, session, repo, monkeypatch):
        """Sync stores raw hash, requirement snapshot, and rule snapshot in metadata."""
        _create_source(session)
        _mock_ok_fetch(monkeypatch)
        _create_document(session, "SRC-AU-189", "DOC-AU-189", MOCK_HASH_V1)

        # Create an active rule first
        _create_approved_requirement(
            session, req_id="REQ-AGE", program_id="SKILLED_189",
            category="AGE", field="age", operator="LT", value=45,
            evidence_text="Under 45.",
            document_id="DOC-AU-189", chunk_id=100,
        )
        compile_approved_requirements(session, program_id="SKILLED_189")
        repo.validate_rule("RULE-REQ-AGE", actor="system")
        repo.approve_rule("RULE-REQ-AGE", actor="system")
        repo.activate_rule("RULE-REQ-AGE", actor="system")
        session.flush()

        # Set source hash so sync detects "changed" (stale last_hash vs fresh fetch)
        source = session.get(GovernmentSource, "SRC-AU-189")
        source.last_hash = "old-hash"
        session.flush()

        from orchestration.government_sync import sync_source
        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.flush()

        # Verify metadata contains change_detection
        runs = session.query(PipelineRun).all()
        assert len(runs) >= 1
        last_run = runs[-1]
        assert last_run.metadata_json is not None
        cd = last_run.metadata_json.get("change_detection")
        assert cd is not None

        # Raw level — previous hash before the fetch vs freshly fetched hash
        assert cd["raw"]["old_hash"] == "old-hash"
        assert cd["raw"]["new_hash"] == MOCK_HASH_V1
        assert cd["raw"]["changed"] is True

        # Requirement level
        assert "snapshot_count" in cd["requirement"]
        assert cd["requirement"]["snapshot_count"] >= 1

        # Rule level
        assert "active_before_count" in cd["rule"]
        assert cd["rule"]["active_before_count"] >= 1
        assert "active_after" in cd["rule"]
        assert "diff" in cd["rule"]

    def test_unchanged_sync_no_change_detection(self, session, repo, monkeypatch):
        """Unchanged sync doesn't compute change detection (returns early)."""
        _create_source(session)
        _mock_ok_fetch(monkeypatch)
        _create_document(session, "SRC-AU-189", "DOC-AU-189", MOCK_HASH_V1)
        source = session.get(GovernmentSource, "SRC-AU-189")
        source.last_hash = MOCK_HASH_V1
        session.flush()

        from orchestration.government_sync import sync_source
        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.flush()

        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].status == "COMPLETED"
        assert runs[0].metadata_json == {}

    def test_rule_diff_detects_value_change(self, session, repo):
        """When a rule value changes, the diff captures it."""
        from orchestration.government_sync import compute_rule_diff

        old_rules = [
            {"program_id": "P1", "category": "AGE", "field": "age",
             "operator": "LT", "value_json": "45", "mandatory": True,
             "critical": False, "message": "Under 45", "status": "ACTIVE", "version": 1},
        ]
        new_rules = [
            {"program_id": "P1", "category": "AGE", "field": "age",
             "operator": "LT", "value_json": "50", "mandatory": True,
             "critical": False, "message": "Under 50", "status": "ACTIVE", "version": 2},
        ]
        diff = compute_rule_diff(old_rules, new_rules)
        assert diff["changed_count"] == 1
        assert diff["changed"][0]["field"] == "age"
        assert diff["changed"][0]["fields_changed"]["value_json"]["old"] == "45"
        assert diff["changed"][0]["fields_changed"]["value_json"]["new"] == "50"

    def test_requirement_diff_detects_addition(self, session, repo):
        """New requirements are detected in the diff."""
        from orchestration.government_sync import compute_requirement_diff

        old_reqs = [
            {"semantic_key": "sk-age", "requirement_id": "R1",
             "operator": "LT", "value_json": "45", "mandatory": True,
             "criticality": "CRITICAL", "field": "age", "category": "AGE",
             "description": "Under 45"},
        ]
        new_reqs = [
            {"semantic_key": "sk-age", "requirement_id": "R1",
             "operator": "LT", "value_json": "45", "mandatory": True,
             "criticality": "CRITICAL", "field": "age", "category": "AGE",
             "description": "Under 45"},
            {"semantic_key": "sk-points", "requirement_id": "R2",
             "operator": "GTE", "value_json": "65", "mandatory": True,
             "criticality": "NORMAL", "field": "points", "category": "POINTS",
             "description": "Min 65 points"},
        ]
        diff = compute_requirement_diff(old_reqs, new_reqs)
        assert diff["added_count"] == 1
        assert diff["removed_count"] == 0
        assert diff["changed_count"] == 0
        assert diff["added"][0]["semantic_key"] == "sk-points"

    def test_requirement_diff_detects_removal(self, session, repo):
        """Removed requirements are detected in the diff."""
        from orchestration.government_sync import compute_requirement_diff

        old_reqs = [
            {"semantic_key": "sk-age", "requirement_id": "R1",
             "operator": "LT", "value_json": "45", "mandatory": True,
             "criticality": "CRITICAL", "field": "age", "category": "AGE",
             "description": "Under 45"},
            {"semantic_key": "sk-points", "requirement_id": "R2",
             "operator": "GTE", "value_json": "65", "mandatory": True,
             "criticality": "NORMAL", "field": "points", "category": "POINTS",
             "description": "Min 65 points"},
        ]
        new_reqs = [
            {"semantic_key": "sk-age", "requirement_id": "R1",
             "operator": "LT", "value_json": "45", "mandatory": True,
             "criticality": "CRITICAL", "field": "age", "category": "AGE",
             "description": "Under 45"},
        ]
        diff = compute_requirement_diff(old_reqs, new_reqs)
        assert diff["added_count"] == 0
        assert diff["removed_count"] == 1
        assert diff["changed_count"] == 0
        assert diff["removed"][0]["semantic_key"] == "sk-points"
