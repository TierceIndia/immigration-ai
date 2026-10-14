"""Item 6: confirm (or refute) that LLM extraction skips a chunk whose
content hash was already extracted.

Document-level dedup already exists and is correct: fetch_document_and_store
only calls run_requirement_extraction when change_type != "UNCHANGED" (see
TestFetchDocumentAndStorePlainOK::test_second_sync_unchanged_returns_unchanged
in test_fetch_flow.py). This file checks the narrower, CHUNK-level claim: on
a CHANGED document (some paragraphs edited, others byte-identical to the
previous version), does the identical paragraph still trigger a fresh LLM
call, or is it skipped because its content hash was already extracted?

Finding: there is no chunk-level dedup anywhere in the extraction path.
extract_from_document() (src/extraction/requirement_extractor.py) selects
candidates by chunk_type/heading/modal-term heuristics only
(chunk_selector.select_candidates) - nothing compares a candidate's
raw_text/hash against chunks already processed in an earlier extraction run.
Every new DocumentVersion also gets entirely fresh DocumentChunk rows (see
fetch_document_and_store), so even byte-identical text has no persisted link
back to its prior chunk_id. The test below proves this with real content:
one paragraph is unchanged between v1 and v2, yet the v2 extraction run
still spends an LLM call on it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher
from change_detection.hashing import sha256_bytes
from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource, DocumentChunk, ExtractionRun,
)
from db.connection import get_session_factory
from orchestration.government_sync import sync_source

_UNCHANGED_TEXT = "Applicants must have a skilled occupation."
UNCHANGED_PARAGRAPH = f"<p>{_UNCHANGED_TEXT}</p>".encode()

HTML_V1 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 45 years of age.</p>"
    + UNCHANGED_PARAGRAPH +
    b"</body></html>"
)
HTML_V2 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 50 years of age.</p>"
    + UNCHANGED_PARAGRAPH +
    b"</body></html>"
)


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


def _seed_source(session):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.add(GovernmentSource(
        source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs",
        url="https://immi.homeaffairs.gov.au/189", source_type="HTML",
        priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.commit()


def _patch_fetch(monkeypatch, raw_bytes):
    def fetch(url, source_type=None, **kw):
        return fetcher.FetchResult(
            url=url, status=fetcher.FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=raw_bytes, duration_ms=5, error=None,
        )
    monkeypatch.setattr(fetcher, "fetch", fetch)


class TestChunkLevelExtractionDedupDoesNotExist:
    def test_unchanged_paragraph_still_costs_a_fresh_llm_call_on_v2(self, session, monkeypatch):
        _seed_source(session)

        _patch_fetch(monkeypatch, HTML_V1)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        v1_run = session.query(ExtractionRun).order_by(ExtractionRun.run_id).first()
        v1_chunks = {c.raw_text: c.chunk_id for c in session.query(DocumentChunk).all()}
        assert any(_UNCHANGED_TEXT in t for t in v1_chunks), (
            f"fixture assumption broken: the shared paragraph was not even chunked - got {sorted(v1_chunks)}"
        )

        _patch_fetch(monkeypatch, HTML_V2)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        runs = session.query(ExtractionRun).order_by(ExtractionRun.run_id).all()
        assert len(runs) == 2
        v2_run = runs[1]

        # v2 gets an entirely new set of DocumentChunk rows (confirmed: no
        # chunk_id is reused across versions, even for identical text).
        v2_chunk_ids = {
            c.chunk_id for c in session.query(DocumentChunk).all()
            if c.raw_text == _UNCHANGED_TEXT
        }
        assert len(v2_chunk_ids) == 2, (
            "expected one chunk row per version for the identical paragraph "
            "(no reuse) - if this ever becomes 1, chunk reuse exists and "
            "this test's premise needs revisiting"
        )

        # The real finding: v2's run still spent an LLM call count equal to
        # its full candidate count - the unchanged paragraph was not skipped.
        # candidate_chunks counts every chunk selected for extraction; if
        # content-hash dedup existed, llm_calls would be candidate_chunks - 1
        # (or candidate_chunks - N for however many chunks repeat).
        assert v2_run.llm_calls == v2_run.candidate_chunks, (
            "if this ever fails because llm_calls < candidate_chunks, "
            "chunk-level content-hash dedup has been implemented - update "
            "this test's assertion and docstring to match"
        )
        assert v2_run.candidate_chunks >= 1
