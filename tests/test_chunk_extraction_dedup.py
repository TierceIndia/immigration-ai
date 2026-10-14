"""Item 5: persistent chunk-level extraction cache.

History: this file originally confirmed chunk-level dedup did NOT exist (see
git history) - extract_from_document() selected candidates by heuristics
only, with nothing comparing a candidate's content hash against chunks
already processed, and every new DocumentVersion got entirely fresh
DocumentChunk rows even for byte-identical text, so even the llm_calls
counter (which counts candidates *processed*, not real network calls, and
still does - see item 6) couldn't reveal whether a real call happened.

Item 5 added CachingLLMClient (src/extraction/llm_client.py), a persistent
cache in extraction_cache keyed by (chunk_content_hash, prompt_version,
model) and wired into run_requirement_extraction. This file now proves the
opposite: an unchanged chunk's REAL LLM call is skipped on re-extraction,
confirmed by spying on the real client's own extract() method - not by
reading a counter that was never measuring that in the first place.
"""
from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher
from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource, DocumentChunk, ExtractionCache,
)
from db.connection import get_session_factory
from orchestration.government_sync import sync_source
from extraction.llm_client import MockLLMClient
import utils.fetch_document as fetch_document_mod

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


class _CountingMockLLMClient(MockLLMClient):
    """The real MockLLMClient, but counts every genuine extract() call -
    the thing a persistent cache is supposed to make rarer, not a proxy
    counter like llm_calls (see item 6: that counts attempts, not real calls)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = []

    def extract(self, input_context):
        self.calls.append(input_context["primary_chunk"]["raw_text"])
        return super().extract(input_context)


class TestChunkLevelExtractionCacheExists:
    def test_unchanged_paragraph_skips_the_real_llm_call_on_v2(self, session, monkeypatch):
        _seed_source(session)
        counting_client = _CountingMockLLMClient()
        monkeypatch.setattr(fetch_document_mod, "get_client", lambda *a, **kw: counting_client)

        _patch_fetch(monkeypatch, HTML_V1)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        v1_calls = len(counting_client.calls)
        assert any(_UNCHANGED_TEXT in t for t in counting_client.calls), (
            "fixture assumption broken: the shared paragraph was not even a call target"
        )

        # v2 gets an entirely new set of DocumentChunk rows (no chunk_id reuse,
        # even for identical text) - the cache must therefore key on content,
        # not chunk_id, to catch this at all.
        v1_chunk_ids = {c.chunk_id for c in session.query(DocumentChunk).all()}

        _patch_fetch(monkeypatch, HTML_V2)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        v2_chunk_ids = {c.chunk_id for c in session.query(DocumentChunk).all()} - v1_chunk_ids
        assert v2_chunk_ids, "v2 should have minted its own fresh chunk rows"

        v2_calls = counting_client.calls[v1_calls:]
        # The real finding: the unchanged paragraph's text never appears
        # among v2's real LLM calls - only the genuinely new/changed text does.
        assert not any(_UNCHANGED_TEXT in t for t in v2_calls), (
            f"the unchanged paragraph still reached a real LLM call on v2: {v2_calls}"
        )
        assert any("50 years" in t for t in v2_calls), "the genuinely changed paragraph must still be called"

    def test_nine_of_ten_identical_chunks_makes_exactly_one_llm_call(self, session, monkeypatch):
        """The exact scenario item 5 specifies: a changed document with 9 of
        10 identical chunks makes exactly 1 LLM call on re-extraction."""
        _seed_source(session)
        counting_client = _CountingMockLLMClient()
        monkeypatch.setattr(fetch_document_mod, "get_client", lambda *a, **kw: counting_client)

        paragraphs_v1 = [f"<p>Applicants must have at least {n} years of work experience.</p>".encode()
                          for n in range(1, 11)]
        html_v1 = b"<html><body><h1>Eligibility</h1>" + b"".join(paragraphs_v1) + b"</body></html>"

        _patch_fetch(monkeypatch, html_v1)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()
        assert len(counting_client.calls) == 10, "fixture assumption: all 10 are real candidates"

        # Change exactly one of the ten paragraphs.
        paragraphs_v2 = list(paragraphs_v1)
        paragraphs_v2[0] = b"<p>Applicants must have at least 99 years of work experience.</p>"
        html_v2 = b"<html><body><h1>Eligibility</h1>" + b"".join(paragraphs_v2) + b"</body></html>"

        v1_call_count = len(counting_client.calls)
        _patch_fetch(monkeypatch, html_v2)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        v2_calls = counting_client.calls[v1_call_count:]
        assert len(v2_calls) == 1, f"expected exactly 1 real LLM call on v2, got {len(v2_calls)}: {v2_calls}"
        assert "99 years" in v2_calls[0]

    def test_a_new_prompt_version_invalidates_the_cache(self, session, monkeypatch):
        """Changing prompt_version is a different cache key entirely - the
        unchanged chunk must be re-sent, not served a stale cached response
        from the old prompt contract."""
        _seed_source(session)
        counting_client = _CountingMockLLMClient()
        monkeypatch.setattr(fetch_document_mod, "get_client", lambda *a, **kw: counting_client)

        _patch_fetch(monkeypatch, HTML_V1)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()
        v1_calls = len(counting_client.calls)

        monkeypatch.setattr(fetch_document_mod, "PROMPT_VERSION", "requirement_extraction_v99_test")
        _patch_fetch(monkeypatch, HTML_V2)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        v2_calls = counting_client.calls[v1_calls:]
        # Both the changed AND the "unchanged" paragraph are called again -
        # the new prompt_version means neither has a cache entry yet.
        assert any(_UNCHANGED_TEXT in t for t in v2_calls), (
            "a new prompt_version must invalidate the cache for the unchanged chunk too"
        )

    def test_cache_rows_are_persisted(self, session, monkeypatch):
        _seed_source(session)
        counting_client = _CountingMockLLMClient()
        monkeypatch.setattr(fetch_document_mod, "get_client", lambda *a, **kw: counting_client)

        _patch_fetch(monkeypatch, HTML_V1)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        rows = session.query(ExtractionCache).all()
        assert len(rows) >= 1
        assert all(r.model == counting_client.model_name for r in rows)
