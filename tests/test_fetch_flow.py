"""End-to-end fetch → store → extract flow tests for the ORM sync path.

Covers ``fetch_document_and_store`` under plain-fetch OK, browser escalation,
fetch failure, unchanged hash, and the two-sync contract.
"""
from __future__ import annotations

import sys
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher
import fetch.browser_fetcher as browser_fetcher
from db.models import (
    Base, Country, ImmigrationProgram,
    GovernmentSource, GovernmentDocument, DocumentVersion,
    DocumentChunk, FetchLog, ExtractionRun, Requirement,
)
from utils.fetch_document import fetch_document_and_store, FetchStoreOutcome
from db.connection import get_session_factory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
HTML_OK = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 45 years of age.</p>"
    b"<p>Applicants must have a skilled occupation.</p>"
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


def _seed_source(session, source_id="SRC-AU-189", status="ACTIVE"):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.add(GovernmentSource(
        source_id=source_id,
        country_id="AU",
        program_id="SKILLED_189",
        visa_category="SKILLED",
        authority="Home Affairs",
        url="https://immi.homeaffairs.gov.au/189",
        source_type="HTML",
        priority="PRIMARY",
        monitoring_method="WEB",
        crawl_frequency="DAILY",
        status=status,
    ))
    session.commit()


def _seed_source_with_doc(session, source_id="SRC-AU-189", content_hash="existing_hash_v1"):
    _seed_source(session, source_id)
    src = session.query(GovernmentSource).filter_by(source_id=source_id).first()
    src.last_hash = content_hash
    session.add(GovernmentDocument(
        document_id="DOC-AU-189",
        source_id=source_id,
        country_id="AU",
        program_id="SKILLED_189",
        visa_category="SKILLED",
        document_type="GENERAL",
        url="https://immi.homeaffairs.gov.au/189",
        title="Skilled 189",
        retrieved_at=datetime.now(timezone.utc),
        content_hash=content_hash,
        current_version=1,
        language="en",
        raw_path="fetch:DOC-AU-189:v1",
        status="CURRENT",
    ))
    session.flush()
    session.add(DocumentVersion(
        document_id="DOC-AU-189",
        version_number=1,
        content_hash=content_hash,
        retrieved_at=datetime.now(timezone.utc),
        raw_path="fetch:DOC-AU-189:v1",
        change_type="INITIAL",
    ))
    session.commit()


def _ok_result(url):
    return fetcher.FetchResult(
        url=url, status=fetcher.FetchStatus.OK, http_status=200,
        content_type="text/html", raw_bytes=HTML_OK, duration_ms=50, error=None,
    )


def _blocked_result(url):
    return fetcher.FetchResult(
        url=url, status=fetcher.FetchStatus.ACCESS_BLOCKED, http_status=403,
        content_type=None, raw_bytes=None, duration_ms=100, error="403 Forbidden",
    )


def _requires_browser_result(url):
    return fetcher.FetchResult(
        url=url, status=fetcher.FetchStatus.REQUIRES_BROWSER, http_status=403,
        content_type="text/html", raw_bytes=None, duration_ms=100,
        error="Challenge page detected",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestFetchDocumentAndStorePlainOK:
    def test_first_sync_stores_everything(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url))

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "OK"
        assert outcome.document_id == "DOC-AU-189"
        assert outcome.document_version == 1
        assert outcome.error is None
        assert outcome.demoted is None

        assert outcome.timing is not None
        assert outcome.timing.source_id == "SRC-AU-189"
        assert outcome.timing.method == "http"
        assert outcome.timing.total_ms >= 0
        assert outcome.timing.extract_ms is not None
        # Browser never ran on a clean plain-HTTP fetch.
        assert outcome.timing.browser_launch_ms is None

        doc = session.get(GovernmentDocument, "DOC-AU-189")
        assert doc is not None
        assert doc.current_version == 1
        assert doc.status == "CURRENT"

        dv = session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").one()
        assert dv.change_type == "INITIAL"
        assert dv.version_number == 1

        chunks = session.query(DocumentChunk).filter_by(document_id="DOC-AU-189").all()
        assert len(chunks) >= 2

        logs = session.query(FetchLog).filter_by(source_id="SRC-AU-189").all()
        assert len(logs) == 1
        assert logs[0].status == "OK"
        assert logs[0].changed == 1

        runs = session.query(ExtractionRun).all()
        assert len(runs) == 1
        assert runs[0].status == "COMPLETED"
        assert runs[0].requirements_extracted >= 1

        reqs = session.query(Requirement).all()
        assert len(reqs) >= 1

        assert source.fetch_status == "OK"
        assert source.status == "ACTIVE"
        assert source.last_hash is not None
        assert source.consecutive_failures == 0

    def test_failed_fetch_still_produces_a_timing_row(self, session, monkeypatch):
        """Item 1: one structured timing line per source, success or not."""
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        def failing_fetch(url, **kw):
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.FETCH_FAILED, http_status=504,
                content_type=None, raw_bytes=None, duration_ms=5000, error="timeout",
                limiter_wait_ms=30, http_ms=5000,
            )

        monkeypatch.setattr(fetcher, "fetch", failing_fetch)
        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.document_id is None
        assert outcome.timing is not None
        assert outcome.timing.method == "http"
        assert outcome.timing.limiter_wait_ms == 30
        assert outcome.timing.http_ms == 5000
        # A failed fetch never stores a document.
        assert session.get(GovernmentDocument, "DOC-AU-189") is None

    def test_second_sync_unchanged_returns_unchanged(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url))

        outcome1 = fetch_document_and_store(session, source)
        session.commit()
        content_hash1 = outcome1.content_hash

        outcome2 = fetch_document_and_store(session, source)
        session.commit()

        assert outcome2.status == "UNCHANGED"
        assert outcome2.document_version == 1
        assert outcome2.requirements_extracted == 0

        assert session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").count() == 1
        assert session.query(ExtractionRun).count() == 1

        logs = session.query(FetchLog).filter_by(source_id="SRC-AU-189").all()
        assert logs[-1].changed == 0


class TestExtractRequirementsScopeFlag:
    """Item 3: a reference-only source (extract_requirements=False) is still
    fetched/hashed/diffed/stored as evidence - only the LLM extraction step
    is skipped. No rows are deleted; this only gates one step."""

    def test_scoped_out_source_stores_document_but_skips_extraction(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        source.extract_requirements = False
        session.commit()
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url))

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "OK"
        assert outcome.document_id == "DOC-AU-189"
        assert outcome.requirements_extracted == 0

        # Fetched, hashed, diffed, and stored as evidence exactly as a normal source.
        doc = session.get(GovernmentDocument, "DOC-AU-189")
        assert doc is not None
        assert session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").count() == 1
        assert session.query(DocumentChunk).filter_by(document_id="DOC-AU-189").count() >= 1

        # But no extraction ever ran.
        assert session.query(ExtractionRun).count() == 0
        assert session.query(Requirement).count() == 0

    def test_default_true_still_extracts(self, session, monkeypatch):
        """The flag defaults true - every existing source keeps today's behavior."""
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert source.extract_requirements is True
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url))

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.requirements_extracted >= 1
        assert session.query(ExtractionRun).count() == 1

    def test_scoped_out_source_still_detects_changes_on_resync(self, session, monkeypatch):
        """Reference-only does not mean frozen - a real content change still
        creates a new immutable version, just never gets extracted."""
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        source.extract_requirements = False
        session.commit()
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url))
        fetch_document_and_store(session, source)
        session.commit()

        changed_html = HTML_OK.replace(b"under 45", b"under 50")
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: fetcher.FetchResult(
            url=url, status=fetcher.FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=changed_html, duration_ms=50, error=None,
        ))
        outcome2 = fetch_document_and_store(session, source)
        session.commit()

        assert outcome2.status == "OK"
        assert outcome2.document_version == 2
        assert session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").count() == 2
        assert session.query(ExtractionRun).count() == 0


class TestConditionalGetOrchestration:
    """Item 5: fetch_document_and_store persists validators from a real
    fetch and short-circuits cleanly on a 304 (no re-store, no re-extraction)."""

    def test_successful_http_fetch_persists_etag_and_last_modified(self, session, monkeypatch):
        def ok_with_validators(url, **kw):
            res = _ok_result(url)
            res.etag = '"abc123"'
            res.last_modified = "Wed, 01 Jan 2026 00:00:00 GMT"
            return res

        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        monkeypatch.setattr(fetcher, "fetch", ok_with_validators)

        fetch_document_and_store(session, source)
        session.commit()

        assert source.etag == '"abc123"'
        assert source.last_modified == "Wed, 01 Jan 2026 00:00:00 GMT"

    def test_not_modified_fetch_sends_the_stored_validators(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        source.etag = '"abc123"'
        source.last_modified = "Wed, 01 Jan 2026 00:00:00 GMT"
        session.commit()

        seen = {}

        def capture_fetch(url, etag=None, last_modified=None, **kw):
            seen["etag"] = etag
            seen["last_modified"] = last_modified
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.OK, http_status=304,
                content_type=None, raw_bytes=None, duration_ms=10, error=None,
                not_modified=True, etag=etag, last_modified=last_modified,
            )

        monkeypatch.setattr(fetcher, "fetch", capture_fetch)
        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert seen["etag"] == '"abc123"'
        assert seen["last_modified"] == "Wed, 01 Jan 2026 00:00:00 GMT"
        assert outcome.status == "UNCHANGED"

    def test_not_modified_never_stores_a_document_or_extracts(self, session, monkeypatch):
        """304 = unchanged with no body, no browser, no extraction (item 5)."""
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        def not_modified_fetch(url, **kw):
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.OK, http_status=304,
                content_type=None, raw_bytes=None, duration_ms=10, error=None,
                not_modified=True,
            )

        browser_called = {"called": False}

        def fake_browser(url, **kw):
            browser_called["called"] = True
            return _ok_result(url)

        monkeypatch.setattr(fetcher, "fetch", not_modified_fetch)
        monkeypatch.setattr(browser_fetcher, "fetch_with_browser", fake_browser)

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "UNCHANGED"
        assert outcome.document_id is None
        assert outcome.requirements_extracted == 0
        assert browser_called["called"] is False
        assert session.query(GovernmentDocument).count() == 0
        assert session.query(DocumentVersion).count() == 0
        assert session.query(ExtractionRun).count() == 0

        # Diagnostics still refresh - a 304 is a real, successful contact.
        assert source.status == "ACTIVE"
        assert source.fetch_status == "OK"
        assert source.consecutive_failures == 0

        logs = session.query(FetchLog).filter_by(source_id="SRC-AU-189").all()
        assert len(logs) == 1
        assert logs[0].status == "OK"
        assert logs[0].changed == 0
        assert logs[0].http_status == 304

    def test_not_modified_preserves_an_existing_document_version(self, session, monkeypatch):
        _seed_source_with_doc(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        source.etag = '"abc123"'
        session.commit()

        def not_modified_fetch(url, **kw):
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.OK, http_status=304,
                content_type=None, raw_bytes=None, duration_ms=10, error=None,
                not_modified=True,
            )

        monkeypatch.setattr(fetcher, "fetch", not_modified_fetch)
        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "UNCHANGED"
        assert outcome.document_id == "DOC-AU-189"
        assert outcome.document_version == 1
        # The existing version row is untouched - no new row, no re-extraction.
        assert session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").count() == 1


class TestFetchDocumentAndStoreBrowserEscalation:
    def test_access_blocked_does_not_escalate_to_browser(self, session, monkeypatch):
        """Item 2 / B4: a real 401/403/429 refusal is the origin telling us
        "no", not "render me properly" - a browser hitting the same URL
        cannot change that answer, so it must never even be tried. Only
        REQUIRES_BROWSER (a rendering problem a browser can plausibly fix)
        escalates."""
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _blocked_result(url))
        browser_called = {"called": False}

        def fake_browser(url, source_type="HTML", **kw):
            browser_called["called"] = True
            return _ok_result(url)

        monkeypatch.setattr(browser_fetcher, "fetch_with_browser", fake_browser)

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert browser_called["called"] is False, "ACCESS_BLOCKED must make zero browser calls"
        assert outcome.status == "ACCESS_BLOCKED"
        assert outcome.document_id is None

        assert source.status == "ACTIVE"
        assert source.fetch_status == "ACCESS_BLOCKED"

    def test_requires_browser_does_escalate(self, session, monkeypatch):
        """A genuine rendering problem (JS/anti-bot challenge, empty SPA
        shell) is exactly what browser escalation exists for."""
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _requires_browser_result(url))
        browser_called = {"called": False}

        def fake_browser(url, source_type="HTML", **kw):
            browser_called["called"] = True
            return _ok_result(url)

        monkeypatch.setattr(browser_fetcher, "fetch_with_browser", fake_browser)

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert browser_called["called"] is True
        assert outcome.status == "OK"
        assert outcome.document_id is not None
        assert outcome.demoted is None

        assert source.status == "ACTIVE"
        assert source.fetch_status == "OK"


class TestFetchDocumentAndStoreFailure:
    def test_fetch_error_updates_diagnostics(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        def failing_fetch(url, **kw):
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.FETCH_FAILED, http_status=504,
                content_type=None, raw_bytes=None, duration_ms=5000, error="timeout",
            )

        monkeypatch.setattr(fetcher, "fetch", failing_fetch)
        monkeypatch.setattr(browser_fetcher, "fetch_with_browser",
                            lambda url, **kw: _blocked_result(url))

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.document_id is None
        assert source.fetch_status == "FETCH_FAILED"
        assert source.last_error == "timeout"
        assert source.consecutive_failures == 1

        logs = session.query(FetchLog).filter_by(source_id="SRC-AU-189").all()
        assert len(logs) == 1
        assert logs[0].status == "FETCH_FAILED"

    def test_blocked_source_stays_in_the_sync_set(self, session, monkeypatch):
        """A blocked fetch records diagnostics but must NOT remove the source.

        Demoting to NEEDS_MANUAL_CAPTURE used to look like escalation, but
        sync_all_sources() selects on status == 'ACTIVE', so the demotion
        silently and permanently excluded the source from every future run.
        """
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        browser_called = {"called": False}

        def fake_browser(url, **kw):
            browser_called["called"] = True
            return _blocked_result(url)

        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _blocked_result(url))
        monkeypatch.setattr(browser_fetcher, "fetch_with_browser", fake_browser)

        outcome = fetch_document_and_store(session, source)
        session.commit()

        # ACCESS_BLOCKED never escalates (item 2) - exactly one fetch attempt.
        assert browser_called["called"] is False

        # Still ACTIVE, still in the sync set, still retried on schedule.
        assert source.status == "ACTIVE"
        assert outcome.demoted is None

        # The failure is still fully visible via diagnostics + FetchLog.
        assert outcome.status == "ACCESS_BLOCKED"
        assert source.fetch_status == "ACCESS_BLOCKED"
        assert source.http_status == 403
        assert source.last_error
        assert source.consecutive_failures == 1
        assert session.query(FetchLog).filter_by(source_id="SRC-AU-189").count() == 1

        # And it really is still selectable for scheduled syncs.
        due = session.query(GovernmentSource).filter_by(status="ACTIVE").all()
        assert [s.source_id for s in due] == ["SRC-AU-189"]


class TestFetchDocumentAndStoreUnchanged:
    def test_matching_hash_creates_no_new_version(self, session, monkeypatch):
        from change_detection.hashing import sha256_bytes
        content_hash = sha256_bytes(HTML_OK)
        _seed_source_with_doc(session, content_hash=content_hash)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url))

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "UNCHANGED"
        assert session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").count() == 1
        assert session.query(ExtractionRun).count() == 0


class TestFetchDocumentAndStoreNoChunks:
    def test_empty_document_skips_extraction(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        # Return empty HTML — extractor produces no chunks
        monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: fetcher.FetchResult(
            url=url, status=fetcher.FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=b"<html><body></body></html>",
            duration_ms=10, error=None,
        ))

        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "OK"
        assert outcome.requirements_extracted == 0
        assert session.query(ExtractionRun).count() == 0
        # Real fetch, real content change, zero chunks: the caller must be able
        # to tell this apart from a genuine "nothing to extract" success.
        assert outcome.empty_content is True
