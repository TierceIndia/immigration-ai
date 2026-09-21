"""Integration tests that hit the real OpenAI API.

Run with:  PYTEST_LLM_LIVE=1 pytest tests/integration/test_real_openai_extraction.py -v

Every test is skipped automatically when OPENAI_API_KEY is absent (conftest strips
it unless PYTEST_LLM_LIVE=1 is exported). Tests marked ``integration`` bypass the
unit-test autouse fixture that swaps get_client → MockLLMClient, so production
extraction hits the real gpt-4o-mini endpoint. The corresponding real-chunks
fixture lives at ``data/real_validation/skilled_189_real_chunks.json``.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "phase2"))

from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource,
    GovernmentDocument, DocumentVersion, DocumentChunk, ExtractionRun,
    Requirement, RequirementEvidence,
)
from db.connection import get_session_factory
from llm_client import get_client, MockLLMClient, OpenAILLMClient
from requirement_extractor import extract_from_document
from utils.fetch_document import run_requirement_extraction

REAL_CHUNKS_PATH = ROOT / "data" / "real_validation" / "skilled_189_real_chunks.json"
SOURCE_ID = "SRC-AU-189-MAIN"
DOCUMENT_ID = "DOC-AU-189-MAIN"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set. Export it and re-run with PYTEST_LLM_LIVE=1 to hit the real OpenAI API.",
    ),
]


def _real_chunks():
    return json.loads(REAL_CHUNKS_PATH.read_text(encoding="utf-8"))


def _seed_document(session):
    """Seed a GovernmentDocument + DocumentVersion with real chunk data."""
    real = _real_chunks()
    chunks = real["chunks"]
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.add(GovernmentSource(
        source_id=SOURCE_ID, country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs", url="https://example.com",
        source_type="HTML", priority="PRIMARY", monitoring_method="WEB",
        crawl_frequency="DAILY", status="ACTIVE",
    ))
    session.flush()
    session.add(GovernmentDocument(
        document_id=DOCUMENT_ID, source_id=SOURCE_ID, country_id="AU",
        program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
        url="https://example.com", title=real["title"],
        retrieved_at=datetime.now(timezone.utc), content_hash="integration-hash-v1",
        current_version=1, language="en", raw_path="integration:v1", status="CURRENT",
    ))
    session.flush()
    session.add(DocumentVersion(
        document_id=DOCUMENT_ID, version_number=1, content_hash="integration-hash-v1",
        retrieved_at=datetime.now(timezone.utc), raw_path="integration:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion)
        .filter_by(document_id=DOCUMENT_ID, version_number=1)
        .one().version_id
    )
    for c in chunks:
        session.add(DocumentChunk(
            document_id=DOCUMENT_ID, version_id=version_id,
            chunk_type=c["chunk_type"], heading_path=c.get("heading_path", ""),
            order_index=c["order_index"], raw_text=c["raw_text"],
        ))
    session.commit()


# ------------------------------------------------------------------
# Part A: direct extraction contract
# ------------------------------------------------------------------
@pytest.mark.integration
class TestOpenAILLMContract:
    """Verify OpenAILLMClient is real, produces non-fabricated requirements,
    and logs governance usage."""

    def test_client_is_real_openai(self):
        client = get_client("gpt-4o-mini", visa_category="SKILLED")
        assert isinstance(client, OpenAILLMClient)
        assert not isinstance(client, MockLLMClient)
        assert client.model_name == "gpt-4o-mini"

    def test_extraction_no_fabricated_evidence(self):
        real = _real_chunks()
        client = get_client("gpt-4o-mini", visa_category="SKILLED")
        source_ctx = {"source_id": SOURCE_ID, "authority": "Home Affairs", "program_id": "SKILLED_189"}
        document_ctx = {"document_id": DOCUMENT_ID, "version": 1, "title": real["title"]}
        outcomes = extract_from_document(
            source=source_ctx, document=document_ctx,
            all_chunks=real["chunks"], llm_client=client, context_window=0,
        )
        all_chunks_by_id = {c["chunk_id"]: c["raw_text"] for c in real["chunks"]}
        all_reqs = []
        for o in outcomes:
            all_reqs.extend(o.accepted_requirements)
            for item in o.needs_review_requirements:
                all_reqs.extend([item["requirement"]])

        assert len(all_reqs) >= 1, "real LLM should extract at least one requirement from 189 chunks"
        for req in all_reqs:
            assert "evidence" in req
            for ev in req["evidence"]:
                chunk_text = all_chunks_by_id.get(ev["chunk_id"], "")
                assert ev["evidence_text"].strip() in chunk_text, (
                    f"fabricated evidence in {req['category']}: {ev['evidence_text']!r} "
                    f"not found in chunk {ev['chunk_id']}"
                )

    def test_governance_logs_usage(self):
        real = _real_chunks()
        client = get_client("gpt-4o-mini", visa_category="SKILLED")
        extract_from_document(
            source={"source_id": SOURCE_ID, "authority": "Home Affairs", "program_id": "SKILLED_189"},
            document={"document_id": DOCUMENT_ID, "version": 1, "title": real["title"]},
            all_chunks=real["chunks"], llm_client=client, context_window=0,
        )
        totals = client.governance.usage_log.totals()
        assert totals["requests"] >= 1, "governance must record at least one real request"
        assert totals["cached"] == 0, "first real extraction should not be served from cache"


# ------------------------------------------------------------------
# Part B: full ORM persistence path
# ------------------------------------------------------------------
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


@pytest.mark.integration
class TestOpenAIPersistence:
    """Verify run_requirement_extraction persists ExtractionRun, Requirements,
    and RequirementEvidence to the database using the real LLM."""

    def test_extraction_run_row_created(self, session):
        _seed_document(session)
        source = session.query(GovernmentSource).filter_by(source_id=SOURCE_ID).first()
        result = run_requirement_extraction(session, source, DOCUMENT_ID, 1)

        assert result["status"] == "COMPLETED"
        assert result["model"] == "gpt-4o-mini"
        assert result["requirements_extracted"] >= 1

        run = session.query(ExtractionRun).filter_by(document_id=DOCUMENT_ID).one()
        assert run.status == "COMPLETED"
        assert run.model == "gpt-4o-mini"
        assert run.chunks_seen >= 19
        assert run.requirements_extracted >= 1
        assert run.completed_at is not None

    def test_requirements_and_evidence_persisted(self, session):
        _seed_document(session)
        source = session.query(GovernmentSource).filter_by(source_id=SOURCE_ID).first()
        run_requirement_extraction(session, source, DOCUMENT_ID, 1)

        reqs = session.query(Requirement).filter_by(document_id=DOCUMENT_ID).all()
        assert len(reqs) >= 1
        for req in reqs:
            assert req.status in ("CURRENT", "NEEDS_REVIEW")
            assert req.created_at is not None

        evidence_rows = (
            session.query(RequirementEvidence)
            .join(Requirement, RequirementEvidence.requirement_id == Requirement.requirement_id)
            .filter(Requirement.document_id == DOCUMENT_ID)
            .all()
        )
        assert len(evidence_rows) >= 1
        for ev in evidence_rows:
            assert ev.chunk_id is not None
            assert len(ev.evidence_text.strip()) > 0
