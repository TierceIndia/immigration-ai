"""PDF sources: page-numbered text, and an honest answer for scanned ones.

A PDF's page number is the only anchor a reader has for "where did this
requirement come from", so every chunk carries it. A scanned (image-only) PDF
has nothing to read: OCR is out of scope, so the honest outcome is "fetched
fine, nothing extractable" - recorded as OK_EMPTY_CONTENT with the reason
SCANNED_PDF, never as a plain success and never as a fetch failure.

The PDFs are the local fixture server's own bytes; nothing is fetched.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db.connection import get_session_factory  # noqa: E402
from db.models import (  # noqa: E402
    Base, Country, DocumentChunk, GovernmentDocument, GovernmentSource,
    ImmigrationProgram,
)
from fetch.extractor_pdf import extract_pdf  # noqa: E402
from fetch.fetcher import FetchResult, FetchStatus  # noqa: E402
from tests.fixtures.fixture_server import minimal_pdf, scanned_pdf  # noqa: E402
from utils.fetch_document import (  # noqa: E402
    EMPTY_NO_STATIC_CONTENT, EMPTY_SCANNED_PDF, OK_EMPTY_CONTENT,
    fetch_document_and_store,
)

PDF_URL = "https://www.legislation.gov.au/F1996B03551/text/original/pdf/2"


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    s = get_session_factory(engine)()
    yield s
    s.close()


def _two_page_pdf() -> bytes:
    """One real PDF repeated, so there is a page 2 to attribute text to."""
    writer = PdfWriter()
    for _ in range(2):
        writer.append(PdfReader(io.BytesIO(minimal_pdf())))
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _seed_pdf_source(session, source_id="SRC-AU-REGS", **overrides):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    fields = dict(
        source_id=source_id, country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Federal Register of Legislation",
        url=PDF_URL, source_type="PDF", priority="PRIMARY",
        monitoring_method="WEB", crawl_frequency="MONTHLY", status="ACTIVE",
        extract_requirements=False,
    )
    fields.update(overrides)
    session.add(GovernmentSource(**fields))
    session.commit()
    return session.get(GovernmentSource, source_id)


def _patch_fetch(monkeypatch, body: bytes, content_type="application/pdf"):
    monkeypatch.setattr(
        "fetch.backend.fetch",
        lambda url, **kw: FetchResult(
            url=url, status=FetchStatus.OK, http_status=200,
            content_type=content_type, raw_bytes=body, duration_ms=5,
            error=None, acquisition="playwright_request",
        ),
    )


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------
class TestPageNumberedText:
    def test_a_text_pdf_yields_one_chunk_per_page_with_its_page_number(self):
        result = extract_pdf(_two_page_pdf())

        assert result.page_count == 2
        assert result.needs_ocr is False
        assert [c.page_number for c in result.chunks] == [1, 2]
        assert [c.order_index for c in result.chunks] == [1, 2]
        assert all("under 45 years" in c.raw_text for c in result.chunks)

    def test_the_page_is_the_chunks_heading_path(self):
        """A page number is the only anchor a PDF gives a reader."""
        result = extract_pdf(_two_page_pdf())
        assert [c.heading_path for c in result.chunks] == ["Page 1", "Page 2"]

    def test_the_clean_text_keeps_every_page(self):
        result = extract_pdf(_two_page_pdf())
        assert result.clean_text.count("under 45 years") == 2


class TestScannedPdf:
    def test_no_text_layer_means_needs_ocr_and_no_invented_chunks(self):
        result = extract_pdf(scanned_pdf())

        assert result.page_count >= 1
        assert result.needs_ocr is True
        assert result.chunks == []
        assert result.clean_text == ""

    def test_ocr_is_never_run(self, monkeypatch):
        """OCR is out of scope: a scanned PDF must report that, not reach for
        an OCR engine that is not a declared dependency."""
        import fetch.extractor_pdf as module

        assert not hasattr(module, "ocr")
        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("pytesseract", "tesseract", "easyocr", "paddleocr"):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# The sync path
# ---------------------------------------------------------------------------
class TestSyncOfAPdfSource:
    def test_page_numbers_are_persisted_on_the_chunks(self, session, monkeypatch):
        source = _seed_pdf_source(session)
        _patch_fetch(monkeypatch, _two_page_pdf())

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "OK"
        chunks = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == outcome.document_id)
            .order_by(DocumentChunk.order_index)
            .all()
        )
        assert [c.page_number for c in chunks] == [1, 2]
        assert [c.heading_path for c in chunks] == ["Page 1", "Page 2"]
        assert source.fetch_status == "OK"
        assert outcome.empty_content is False
        assert outcome.empty_content_reason is None

    def test_a_scanned_pdf_is_ok_empty_content_not_a_failure(
        self, session, monkeypatch,
    ):
        source = _seed_pdf_source(session)
        _patch_fetch(monkeypatch, scanned_pdf())

        outcome = fetch_document_and_store(session, source)
        session.commit()

        # Retrieval worked, so this is not a fetch failure: the document and
        # its raw snapshot exist and the source stays ACTIVE.
        assert outcome.document_id is not None
        assert outcome.content_hash is not None
        assert source.status == "ACTIVE"
        # But it is not an ordinary success either.
        assert source.fetch_status == OK_EMPTY_CONTENT
        assert outcome.empty_content is True
        assert outcome.empty_content_reason == EMPTY_SCANNED_PDF

        document = session.get(GovernmentDocument, outcome.document_id)
        assert document.status == "NEEDS_OCR"
        assert document.raw_path, "the original bytes are still kept as evidence"
        assert session.query(DocumentChunk).count() == 0

    def test_an_empty_page_reports_a_different_reason_than_a_scanned_pdf(
        self, session, monkeypatch,
    ):
        """The two kinds of "nothing to extract" need different human actions,
        so they must not be collapsed into one message."""
        source = _seed_pdf_source(
            session, source_id="SRC-AU-SPA", source_type="HTML",
            url="https://immi.homeaffairs.gov.au/spa",
        )
        _patch_fetch(
            monkeypatch,
            b"<html><body><div id='app'>Search for a course</div></body></html>",
            content_type="text/html",
        )

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert source.fetch_status == OK_EMPTY_CONTENT
        assert outcome.empty_content_reason == EMPTY_NO_STATIC_CONTENT

    def test_the_sync_warning_names_which_kind_of_empty_it_was(self):
        from orchestration.government_sync import _empty_content_warning

        scanned = _empty_content_warning(EMPTY_SCANNED_PDF)
        page = _empty_content_warning(EMPTY_NO_STATIC_CONTENT)

        assert "no text layer" in scanned
        assert "OCR" in scanned
        assert scanned != page
        assert "zero" in page
