"""Phase 4 acceptance: frozen AU fixture pipeline E2E on real PostgreSQL.

Runs the real ``fetcher.fetch`` (monkeypatched to a frozen, offline Home Affairs
Skilled-189 HTML fixture) through ``government_sync.sync_source`` against the
live PostgreSQL instance (localhost:5432, the docker-compose dev DB), proving:

    1.  v1 fetch → GovernmentDocument v1 + DocumentVersion + chunks persisted
        with real sha256 + FetchLog "OK" + source.last_hash/last_successful_fetch_at
    2.  Approved requirement → compile → ACTIVE rule (value 45)
    3.  Government change (v2: age 45 → 50) → Document v2, previous ACTIVE rule
        untouched (failure-safe until human activates)
    4.  New approved requirement → compile → DRAFT rule v2 → admin validate/approve/
        activate → v1 SUPERSEDED, v2 ACTIVE
    5.  PipelineRun COMPLETED rows with change_detection metadata on every sync

Run explicitly against the live container with:

    $env:POSTGRES_E2E="1"; python -m pytest tests/e2e/test_postgres_pipeline_lifecycle.py -v

Without ``POSTGRES_E2E=1`` the module is skipped so default CI stays hermetic
(no live-services dependency). The test writes only rows under its own sandbox
ids (source SRC-AU-E2E-189 → DOC-AU-E2E-189, requirements E2E-REQ-*, rules
RULE-E2E-*) and removes them afterwards.
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

from db.models import (
    Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, DocumentChunk, FetchLog, ExtractionRun, Requirement,
    RequirementEvidence, Rule, RuleVersion, PipelineRun,
)
from db.connection import get_session_factory
from db.repositories import PipelineRunRepo
from fetcher import FetchResult, FetchStatus, fetch
from hashing import sha256_bytes
from orchestration.government_sync import sync_source
from phase3.rule_repository import EnhancedRuleRepo
from phase3.requirement_to_rule import compile_approved_requirements

pytestmark = [
    pytest.mark.postgres_e2e,
    pytest.mark.skipif(
        os.environ.get("POSTGRES_E2E") != "1",
        reason="POSTGRES_E2E=1 required (runs against the live localhost:5432 dev PostgreSQL)",
    ),
]

E2E_URL = os.environ.get(
    "POSTGRES_E2E_URL",
    "postgresql://immigration:immigration_dev@localhost:5432/immigration",
)

SOURCE_ID = "SRC-AU-E2E-189"
DOCUMENT_ID = "DOC-AU-E2E-189"
PROGRAM_ID = "SKILLED_189"
SOURCE_URL = "https://immi.homeaffairs.gov.au/eligibility/skilled-independent-189"

# Frozen Home Affairs-style fixture (offline, hermetic). v1 → age < 45; v2 → age < 50.
FIXTURE_V1 = (
    b"<html><head><title>Skilled Independent (subclass 189)</title></head><body>"
    b"<h1>Eligibility criteria</h1>"
    b"<h2>Age</h2><p>You must be under 45 years of age at the time of invitation.</p>"
    b"<h2>Points</h2><p>You must score at least 65 points on the points test.</p>"
    b"<h2>Occupation</h2><p>Your occupation must be on the skilled occupation list.</p>"
    b"</body></html>"
)
FIXTURE_V2 = (
    b"<html><head><title>Skilled Independent (subclass 189)</title></head><body>"
    b"<h1>Eligibility criteria</h1>"
    b"<h2>Age</h2><p>You must be under 50 years of age at the time of invitation.</p>"
    b"<h2>Points</h2><p>You must score at least 65 points on the points test.</p>"
    b"<h2>Occupation</h2><p>Your occupation must be on the skilled occupation list.</p>"
    b"</body></html>"
)
HASH_V1 = sha256_bytes(FIXTURE_V1)
HASH_V2 = sha256_bytes(FIXTURE_V2)

REQ_V1_ID = "E2E-REQ-AGEV1"
REQ_V2_ID = "E2E-REQ-AGEV2"
RULE_V1_ID = f"RULE-{REQ_V1_ID}"
RULE_V2_ID = f"RULE-{REQ_V2_ID}"

EXPECTED_PROG = ("SKILLED_189", "SKILLED", "189", "Skilled Independent")


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(E2E_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def session(engine):
    # Idempotent start: purge any leftover sandbox rows from an aborted prior run.
    _cleanup_sandbox()
    s = get_session_factory(engine)()
    yield s
    s.close()
    _cleanup_sandbox()


# Append-only tables protected by DB triggers (fn_block_mutation). Cleanup must
# disable those triggers transactionally before removing sandbox rows, then
# re-enable them — the E2E owns these rows and returns the DB to a pristine state.
_APPEND_ONLY_TABLES = (
    "document_versions",
    "requirement_versions",
    "rule_versions",
    "assessments",
    "assessment_rule_results",
    "rulesets",
    "ruleset_rules",
)

_DELETE_STATEMENTS = (
    "DELETE FROM assessment_rule_results WHERE rule_id LIKE 'RULE-E2E-%'",
    "DELETE FROM rule_versions WHERE rule_id LIKE 'RULE-E2E-%'",
    "DELETE FROM rules WHERE rule_id LIKE 'RULE-E2E-%'",
    # Auto-extraction (fetch_document_and_store) produces real requirements whose
    # ids are REQ-SKILLED_189-<hex>; scope by the sandbox document, plus the
    # explicitly seeded E2E-REQ-* requirements.
    "DELETE FROM requirement_evidence WHERE document_id = :doc",
    "DELETE FROM requirement_references WHERE requirement_id IN "
        "(SELECT requirement_id FROM requirements WHERE extraction_run_id IN "
        "(SELECT run_id FROM extraction_runs WHERE document_id = :doc)) "
        "OR requirement_id LIKE 'E2E-REQ-%'",
    "DELETE FROM requirement_review WHERE requirement_id IN "
        "(SELECT requirement_id FROM requirements WHERE extraction_run_id IN "
        "(SELECT run_id FROM extraction_runs WHERE document_id = :doc)) "
        "OR requirement_id LIKE 'E2E-REQ-%'",
    "DELETE FROM requirement_versions WHERE requirement_id IN "
        "(SELECT requirement_id FROM requirements WHERE extraction_run_id IN "
        "(SELECT run_id FROM extraction_runs WHERE document_id = :doc)) "
        "OR requirement_id LIKE 'E2E-REQ-%'",
    "DELETE FROM requirements WHERE extraction_run_id IN "
        "(SELECT run_id FROM extraction_runs WHERE document_id = :doc) "
        "OR requirement_id LIKE 'E2E-REQ-%'",
    "DELETE FROM extraction_runs WHERE document_id = :doc",
    "DELETE FROM document_chunks WHERE document_id = :doc",
    "DELETE FROM document_versions WHERE document_id = :doc",
    "DELETE FROM government_documents WHERE document_id = :doc",
    "DELETE FROM fetch_log WHERE source_id = :src",
    "DELETE FROM pipeline_runs WHERE source_id = :src",
    "DELETE FROM government_sources WHERE source_id = :src",
)


def _cleanup_sandbox() -> None:
    """Delete every row this E2E can create (FK-safe, trigger-aware, best-effort)."""
    from sqlalchemy import create_engine
    eng = create_engine(E2E_URL, pool_pre_ping=True)
    try:
        with eng.begin() as conn:
            for t in _APPEND_ONLY_TABLES:
                conn.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER ALL"))
            for stmt in _DELETE_STATEMENTS:
                conn.execute(text(stmt), {"doc": DOCUMENT_ID, "src": SOURCE_ID})
            for t in _APPEND_ONLY_TABLES:
                conn.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER ALL"))
    finally:
        eng.dispose()


def _sandbox_fetch(payload: bytes):
    def fetch(url, source_type=None, **kw):  # pragma: no cover - exercise only
        return FetchResult(
            url=url, status=FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=payload,
            duration_ms=5, error=None, error_type=None,
        )
    return fetch


def _seed_program(session) -> None:
    if session.get(Country, "AU") is None:
        session.add(Country(
            country_id="AU", name="Australia", iso_code="AUS", status="ACTIVE",
        ))
    prog = session.get(ImmigrationProgram, PROGRAM_ID)
    if prog is None:
        session.add(ImmigrationProgram(
            program_id=PROGRAM_ID, country_id="AU", visa_category=EXPECTED_PROG[1],
            subclass_code=EXPECTED_PROG[2], name=EXPECTED_PROG[3], status="ACTIVE",
        ))
    session.flush()


def _create_sandbox_source(session) -> GovernmentSource:
    if session.get(GovernmentSource, SOURCE_ID) is None:
        src = GovernmentSource(
            source_id=SOURCE_ID, country_id="AU", program_id=PROGRAM_ID,
            visa_category="189", authority="Department of Home Affairs",
            url=SOURCE_URL, source_type="WEBPAGE", source_subtype="ELIGIBILITY_CRITERIA",
            priority="PRIMARY", monitoring_method="HASH", crawl_frequency="DAILY",
            status="ACTIVE",
        )
        session.add(src)
        session.flush()
    return session.get(GovernmentSource, SOURCE_ID)


def _approved_requirement(session, req_id: str, operator: str, value: int,
                          text_fragment: str, document_version: int,
                          req_version: str) -> None:
    run = ExtractionRun(
        run_id=f"E2E-RUN-{req_version}", document_id=DOCUMENT_ID,
        document_version=document_version, model="mock", prompt_version="v1",
        chunks_seen=1, candidate_chunks=1, llm_calls=1,
        requirements_extracted=1, validated=1, needs_review=0, rejected=0,
        status="COMPLETED",
    )
    session.add(run)
    session.flush()

    dv = (
        session.query(DocumentVersion)
        .filter(DocumentVersion.document_id == DOCUMENT_ID,
                DocumentVersion.version_number == document_version)
        .one()
    )
    chunk = (
        session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == DOCUMENT_ID,
                DocumentChunk.version_id == dv.version_id)
        .order_by(DocumentChunk.order_index)
        .first()
    )
    assert chunk is not None, "sync must have persisted chunks"

    req = Requirement(
        requirement_id=req_id, semantic_key=f"e2e-skilled189-age-{value}",
        program_id=PROGRAM_ID, country_id="AU", document_version_id=dv.version_id,
        category="AGE", name=f"Age requirement ({value})",
        description=f"Applicant must be under {value} years of age.",
        field="age", operator=operator, value_json=str(value), unit="years",
        mandatory=1, criticality="CRITICAL", status="APPROVED", current_version=1,
        extraction_run_id=run.run_id,
    )
    session.add(req)
    session.flush()

    session.add(RequirementEvidence(
        requirement_id=req_id, document_id=DOCUMENT_ID,
        document_version=document_version, document_version_id=dv.version_id,
        chunk_id=chunk.chunk_id, evidence_text=text_fragment,
        evidence_type="DIRECT_TEXT", verified=1,
        extraction_method="MOCK_EXTRACTION",
        extracted_at=datetime.now(timezone.utc),
    ))
    session.flush()


def _latest_run(session) -> PipelineRun:
    return (
        session.query(PipelineRun)
        .filter(PipelineRun.source_id == SOURCE_ID)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )


class TestPostgresPipelineLifecycle:
    def test_frozen_au_pipeline_on_postgresql(self, session, monkeypatch):
        _seed_program(session)
        source = _create_sandbox_source(session)
        session.commit()

        repo = EnhancedRuleRepo(session)
        run_repo = PipelineRunRepo(session)

        # ===================== STEP 1: government v1 =====================
        monkeypatch.setattr("fetcher.fetch", _sandbox_fetch(FIXTURE_V1))
        result_v1 = sync_source(session, SOURCE_ID, trigger_type="ON_DEMAND")
        session.expire_all()
        session.commit()

        assert result_v1.status == "changed"
        assert result_v1.document_id == DOCUMENT_ID
        assert result_v1.document_version == 1

        doc_v1 = session.get(GovernmentDocument, DOCUMENT_ID)
        assert doc_v1 is not None
        assert doc_v1.current_version == 1
        assert doc_v1.content_hash == HASH_V1
        assert doc_v1.status == "CURRENT"
        assert doc_v1.program_id == PROGRAM_ID

        versions = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.document_id == DOCUMENT_ID)
            .order_by(DocumentVersion.version_number)
            .all()
        )
        assert [v.version_number for v in versions] == [1]
        assert versions[0].change_type == "INITIAL"
        assert len(versions[0].content_hash) == 64

        chunks = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == DOCUMENT_ID)
            .all()
        )
        assert len(chunks) >= 3
        chunk_text = " ".join(c.raw_text for c in chunks)
        assert "45" in chunk_text

        logs = (
            session.query(FetchLog).filter(FetchLog.source_id == SOURCE_ID).all()
        )
        assert len(logs) == 1
        assert logs[0].status == "OK"
        assert logs[0].changed == 1
        assert logs[0].content_hash == HASH_V1

        src = session.get(GovernmentSource, SOURCE_ID)
        assert src.fetch_status == "OK"
        assert src.last_hash == HASH_V1
        assert src.last_error is None
        assert src.consecutive_failures == 0
        assert src.last_successful_fetch_at is not None
        assert src.status == "ACTIVE"

        run_v1 = _latest_run(session)
        assert run_v1.status == "COMPLETED"
        assert run_v1.metadata_json["change_detection"]["raw"] == {
            "old_hash": None, "new_hash": HASH_V1, "changed": True,
        }

        # ===================== STEP 2: approval → ACTIVE rule v1 =====================
        _approved_requirement(
            session, REQ_V1_ID, "LT", 45,
            "You must be under 45 years of age at the time of invitation.",
            document_version=1, req_version="AGEV1",
        )
        session.commit()

        compiled = compile_approved_requirements(session, program_id=PROGRAM_ID)
        assert any(r["rule_id"] == RULE_V1_ID and r["status"] == "created"
                   for r in compiled), f"compile output: {compiled}"
        session.flush()

        repo.validate_rule(RULE_V1_ID, actor="system")
        repo.approve_rule(RULE_V1_ID, actor="system")
        repo.activate_rule(RULE_V1_ID, actor="system")
        session.commit()

        active = repo.list_active(PROGRAM_ID)
        assert len(active) == 1
        assert active[0].rule_id == RULE_V1_ID
        assert active[0].status == "ACTIVE"
        assert active[0].value_json == "45"

        # ===================== STEP 3: government changes to v2 =====================
        monkeypatch.setattr("fetcher.fetch", _sandbox_fetch(FIXTURE_V2))
        result_v2 = sync_source(session, SOURCE_ID, trigger_type="ON_DEMAND")
        session.expire_all()
        session.commit()

        assert result_v2.status == "changed"
        assert result_v2.document_version == 2

        doc_v2 = session.get(GovernmentDocument, DOCUMENT_ID)
        assert doc_v2.current_version == 2
        assert doc_v2.content_hash == HASH_V2

        versions = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.document_id == DOCUMENT_ID)
            .order_by(DocumentVersion.version_number)
            .all()
        )
        assert [v.version_number for v in versions] == [1, 2]
        assert versions[1].change_type == "CHANGED"
        assert versions[1].content_hash == HASH_V2

        chunks_v2 = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == DOCUMENT_ID,
                    DocumentChunk.version_id == versions[1].version_id)
            .all()
        )
        assert len(chunks_v2) >= 3
        assert "50" in " ".join(c.raw_text for c in chunks_v2)

        src = session.get(GovernmentSource, SOURCE_ID)
        assert src.last_hash == HASH_V2

        run_v2 = _latest_run(session)
        assert run_v2.status == "COMPLETED"
        cd = run_v2.metadata_json["change_detection"]
        assert cd["raw"]["old_hash"] == HASH_V1
        assert cd["raw"]["new_hash"] == HASH_V2
        assert cd["raw"]["changed"] is True

        # Failure-safe: v2 fetched but NO new rule activated yet — v1 still ACTIVE
        active_after_fetch = repo.list_active(PROGRAM_ID)
        assert len(active_after_fetch) == 1
        assert active_after_fetch[0].rule_id == RULE_V1_ID

        # ===================== STEP 4: v2 approved → DRAFT → ACTIVE, v1 SUPERSEDED =====================
        _approved_requirement(
            session, REQ_V2_ID, "LT", 50,
            "You must be under 50 years of age at the time of invitation.",
            document_version=2, req_version="AGEV2",
        )
        session.commit()

        compiled = compile_approved_requirements(session, program_id=PROGRAM_ID)
        assert any(r["rule_id"] == RULE_V2_ID and r["status"] == "created"
                   for r in compiled), f"compile output: {compiled}"
        session.flush()

        rule_v2_orm = repo.get(RULE_V2_ID)
        assert rule_v2_orm.status == "DRAFT"

        repo.validate_rule(RULE_V2_ID, actor="admin")
        repo.approve_rule(RULE_V2_ID, actor="admin")
        repo.activate_rule(RULE_V2_ID, actor="admin")
        session.commit()

        assert repo.get(RULE_V1_ID).status == "SUPERSEDED"
        assert repo.get(RULE_V2_ID).status == "ACTIVE"
        active_final = repo.list_active(PROGRAM_ID)
        assert len(active_final) == 1
        assert active_final[0].rule_id == RULE_V2_ID
        assert active_final[0].value_json == "50"

        # ===================== STEP 5: audit trail on Postgres =====================
        runs = (
            session.query(PipelineRun)
            .filter(PipelineRun.source_id == SOURCE_ID)
            .order_by(PipelineRun.started_at.asc())
            .all()
        )
        assert len(runs) == 2
        assert all(r.status == "COMPLETED" for r in runs)
        assert runs[1].metadata_json["change_detection"]["raw"]["changed"] is True

        # Rule version history persisted
        rule_versions = (
            session.query(RuleVersion).filter(RuleVersion.rule_id == RULE_V2_ID).all()
        )
        assert len(rule_versions) >= 1

        # Evidence lineage complete: requirement → real chunk → real version
        ev = (
            session.query(RequirementEvidence)
            .filter(RequirementEvidence.requirement_id == REQ_V2_ID)
            .one()
        )
        assert ev.document_id == DOCUMENT_ID
        assert ev.chunk_id is not None
        assert "50" in ev.evidence_text