"""Contract tests for ``run_requirement_extraction``.

Verifies the Phase-2 extraction contract:
* ExtractionRun row is created with correct model/status/counters.
* Requirements are persisted when chunks contain extractable text.
* Empty chunks / missing document return well-defined error dicts.

The deterministic MockLLMClient is injected by tests/conftest.py, so no real
LLM call or OPENAI_API_KEY is involved; the run row still records the resolved
OpenAI default model (gpt-4o-mini).
"""
from __future__ import annotations

import sys
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from db.models import (
    Base, Country, ImmigrationProgram,
    GovernmentSource, GovernmentDocument, DocumentVersion,
    DocumentChunk, ExtractionRun, Requirement, RequirementEvidence,
)
from utils.fetch_document import run_requirement_extraction
from db.connection import get_session_factory


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
    s = get_session_factory(engine)()
    yield s
    s.close()


def _seed_doc(session, chunks_text):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.add(GovernmentSource(
        source_id="SRC-AU-189",
        country_id="AU",
        program_id="SKILLED_189",
        visa_category="SKILLED",
        authority="Home Affairs",
        url="https://example.com",
        source_type="HTML",
        priority="PRIMARY",
        monitoring_method="WEB",
        crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.flush()
    session.add(GovernmentDocument(
        document_id="DOC-AU-189",
        source_id="SRC-AU-189",
        country_id="AU",
        program_id="SKILLED_189",
        visa_category="SKILLED",
        document_type="GENERAL",
        url="https://example.com",
        title="Test",
        retrieved_at=datetime.now(timezone.utc),
        content_hash="abc123",
        current_version=1,
        language="en",
        raw_path="fetch:doc:v1",
        status="CURRENT",
    ))
    session.flush()
    session.add(DocumentVersion(
        document_id="DOC-AU-189",
        version_number=1,
        content_hash="abc123",
        retrieved_at=datetime.now(timezone.utc),
        raw_path="fetch:doc:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion)
        .filter_by(document_id="DOC-AU-189", version_number=1)
        .one()
        .version_id
    )
    for i, text in enumerate(chunks_text):
        session.add(DocumentChunk(
            document_id="DOC-AU-189",
            version_id=version_id,
            chunk_type="PARAGRAPH",
            heading_path="Eligibility",
            order_index=i,
            raw_text=text,
        ))
    session.commit()
    return session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()


class TestRunRequirementExtractionBasic:
    def test_missing_document_returns_error(self, session):
        source = _seed_doc(session, ["some text"])
        result = run_requirement_extraction(session, source, "NONEXISTENT", 1)
        assert result["status"] == "DOCUMENT_NOT_FOUND"

    def test_no_chunks_returns_no_chunks(self, session):
        source = _seed_doc(session, [])
        result = run_requirement_extraction(session, source, "DOC-AU-189", 1)
        assert result["status"] == "NO_CHUNKS"


class TestRunRequirementExtractionWithMockLLM:
    def test_creates_extraction_run_and_persists(self, session):
        source = _seed_doc(session, [
            "Applicants must be under 45 years of age.",
            "Applicants must have a skilled occupation.",
        ])

        result = run_requirement_extraction(session, source, "DOC-AU-189", 1)

        assert result["status"] == "COMPLETED"
        assert result["requirements_extracted"] >= 1
        assert result["model"] == "gpt-4o-mini"

        run = session.query(ExtractionRun).filter_by(document_id="DOC-AU-189").one()
        assert run.status == "COMPLETED"
        assert run.model == "gpt-4o-mini"
        assert run.chunks_seen >= 2
        assert run.requirements_extracted >= 1

        reqs = session.query(Requirement).filter_by(document_id="DOC-AU-189").all() if hasattr(Requirement, 'document_id') else session.query(Requirement).all()
        assert len(reqs) >= 1

    def test_requirements_have_evidence(self, session):
        source = _seed_doc(session, [
            "Applicants must be under 45 years of age.",
        ])
        result = run_requirement_extraction(session, source, "DOC-AU-189", 1)

        req_ids = [r.requirement_id for r in session.query(Requirement).all()]
        if req_ids:
            evidence = (
                session.query(RequirementEvidence)
                .filter(RequirementEvidence.requirement_id.in_(req_ids))
                .all()
            )
            assert len(evidence) >= 1

    def test_all_counters_populated(self, session):
        source = _seed_doc(session, [
            "Applicants must be under 45 years of age.",
            "Must have competent english.",
        ])
        result = run_requirement_extraction(session, source, "DOC-AU-189", 1)

        for key in ("chunks_seen", "candidate_chunks", "llm_calls",
                     "requirements_extracted", "validated", "needs_review",
                     "rejected", "evidence_failures", "schema_failures"):
            assert key in result, f"missing counter: {key}"
            assert isinstance(result[key], int)
