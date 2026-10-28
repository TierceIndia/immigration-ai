"""Evidence citations: every quote points at a checkable place in the source.

A PDF requirement's evidence must say which page it came from ("p. 12"); an
HTML requirement's evidence must name the heading path it was extracted
under. The anchor is derived from the chunk on every read, so it cannot
drift from the chunk it points at.

The evidence-verification contract is unchanged: the quote itself must still
be an exact substring of the chunk (extraction/evidence_validator.py).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.main import app  # noqa: E402
from db.connection import get_session_factory  # noqa: E402
from db.models import (  # noqa: E402
    Base, Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, DocumentChunk, ExtractionRun, Requirement, RequirementEvidence,
)
from extraction.citations import citation_for_chunk, format_citation  # noqa: E402


class TestFormatCitation:
    def test_pdf_page_is_anchored_as_p_n(self):
        assert format_citation(page_number=12) == "p. 12"

    def test_page_one_is_not_treated_as_missing(self):
        # A falsy check here would silently turn page 1 into a heading-path
        # citation, which would point a human at the wrong place.
        assert format_citation(page_number=1) == "p. 1"

    def test_heading_path_is_used_when_there_is_no_page(self):
        assert format_citation(heading_path="Division 2 > Age") == "Division 2 > Age"

    def test_page_wins_over_heading_path(self):
        # A PDF page is a precise locator; a heading path is only as good as
        # the headings the publisher happened to use that day.
        assert format_citation(page_number=7, heading_path="Part B") == "p. 7"

    def test_empty_heading_path_falls_through_to_the_chunk_id(self):
        assert format_citation(heading_path="   ", chunk_id=42) == "chunk #42"

    def test_nothing_to_anchor_returns_none(self):
        assert format_citation() is None

    def test_a_non_numeric_page_is_ignored_rather_than_crashing(self):
        assert format_citation(page_number="not a page", heading_path="Part B") == "Part B"


class TestCitationForChunk:
    def test_reads_an_orm_shaped_chunk(self):
        class _Chunk:
            chunk_id = 5
            page_number = 3
            heading_path = "Page 3"

        assert citation_for_chunk(_Chunk()) == "p. 3"

    def test_reads_a_dict_shaped_chunk(self):
        assert citation_for_chunk(
            {"chunk_id": 5, "page_number": 9, "heading_path": "Page 9"}
        ) == "p. 9"

    def test_none_chunk_is_none(self):
        assert citation_for_chunk(None) is None

    def test_chunk_with_no_anchor_at_all_falls_back_to_the_chunk_id(self):
        assert citation_for_chunk({"chunk_id": 1}) == "chunk #1"


# ---------------------------------------------------------------------------
# API level: the review feed and the rules feed both carry the anchor.
# ---------------------------------------------------------------------------

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
def client(engine):
    app.state.db_engine = engine
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "citation-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(client.app.state.db_engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "citation-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "citation-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed_chain(session):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.merge(GovernmentSource(
        source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs",
        url="https://example.gov.au/189", source_type="HTML",
        priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.merge(GovernmentDocument(
        document_id="DOC-AU-189", source_id="SRC-AU-189", country_id="AU",
        program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
        url="https://example.gov.au/189", title="Skilled 189 eligibility",
        retrieved_at=datetime.now(timezone.utc), content_hash="abc123",
        current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
    ))
    session.flush()
    session.merge(DocumentVersion(
        document_id="DOC-AU-189", version_number=1, content_hash="abc123",
        retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion)
        .filter_by(document_id="DOC-AU-189", version_number=1)
        .one()
        .version_id
    )
    run = ExtractionRun(
        run_id="RUN-CITATION-1", document_id="DOC-AU-189", document_version=1,
        document_version_id=version_id, model="mock", prompt_version="v1",
        status="COMPLETED",
    )
    session.add(run)
    session.flush()
    session.commit()
    return version_id


def _make_chunk(session, version_id, chunk_id, *, page_number=None, heading_path=None):
    session.add(DocumentChunk(
        chunk_id=chunk_id,
        document_id="DOC-AU-189",
        version_id=version_id,
        chunk_type="PARAGRAPH",
        heading_path=heading_path,
        order_index=1,
        raw_text="Applicants must be under 45 years of age.",
        page_number=page_number,
    ))
    session.flush()


def _make_requirement(session, requirement_id, chunk_id, run_id, version_id):
    req = Requirement(
        requirement_id=requirement_id,
        semantic_key=f"semkey-{requirement_id}",
        program_id="SKILLED_189",
        country_id="AU",
        category="AGE",
        name="Age requirement",
        description="Applicants must meet the age requirement.",
        field="applicant.age",
        operator="LT",
        value_json="45",
        unit="years",
        mandatory=1,
        confidence=0.92,
        status="NEEDS_REVIEW",
        current_version=1,
        extraction_run_id=run_id,
        document_version_id=version_id,
    )
    session.add(req)
    session.flush()
    session.add(RequirementEvidence(
        requirement_id=requirement_id,
        document_id="DOC-AU-189",
        document_version=1,
        chunk_id=chunk_id,
        evidence_text="Applicants must be under 45 years of age.",
        evidence_type="DIRECT_TEXT",
        extracted_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return req


class TestTheReviewFeedCarriesTheCitation:
    def test_a_pdf_evidence_entry_cites_its_page(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            version_id = _seed_chain(s)
            _make_chunk(s, version_id, 101, page_number=12)
            _make_requirement(s, "REQ-PDF", 101, "RUN-CITATION-1", version_id)

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=NEEDS_REVIEW",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        evidence = resp.json()["requirements"][0]["evidence"]
        assert evidence[0]["citation"] == "p. 12"

    def test_an_html_evidence_entry_cites_its_heading_path(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            version_id = _seed_chain(s)
            _make_chunk(s, version_id, 102, heading_path="Division 2 > Age")
            _make_requirement(s, "REQ-HTML", 102, "RUN-CITATION-1", version_id)

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=NEEDS_REVIEW",
            headers=admin_headers,
        )
        evidence = resp.json()["requirements"][0]["evidence"]
        assert evidence[0]["citation"] == "Division 2 > Age"

    def test_evidence_verification_is_untouched_by_the_citation(self, client, admin_headers, engine):
        # The anchor is a locator for a human reader; the quote itself is
        # still verified as an exact substring of the chunk.
        with get_session_factory(engine)() as s:
            version_id = _seed_chain(s)
            _make_chunk(s, version_id, 103, page_number=4)
            req = _make_requirement(s, "REQ-VERBATIM", 103, "RUN-CITATION-1", version_id)
            assert req.requirement_id == "REQ-VERBATIM"

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=NEEDS_REVIEW",
            headers=admin_headers,
        )
        evidence = resp.json()["requirements"][0]["evidence"][0]
        assert evidence["evidence_text"] == "Applicants must be under 45 years of age."
        assert evidence["citation"] == "p. 4"
