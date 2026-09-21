"""Bug #2 lifecycle tests — sync_source always re-fetches due sources.

Proves the acceptance criteria for the scheduler no-fresh-fetch fix:
    1. First sync (H1) bootstraps document v1, sets source.last_hash, and runs
       downstream requirement extraction exactly as the legacy zero-document
       bootstrap path did.
    2. H1→H1: a second sync genuinely re-fetches (fetch is actually called),
       but produces no new version, no re-extraction, and is classified UNCHANGED.
    3. H1→H2: a second sync with different content creates a new immutable
       document_version 2 (CHANGED), leaves version 1 untouched, records the
       fresh hash, and re-triggers downstream extraction/requirement proposals.
    4. crawl_frequency gating: SCHEDULED syncs skip not-yet-due sources,
       include due sources, and MANUAL syncs always re-fetch.
All fetches are mocked so the suite stays hermetic and offline.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

import fetcher
from hashing import sha256_bytes
from db.models import (
    Base, Country, ImmigrationProgram,
    GovernmentSource, GovernmentDocument, DocumentVersion,
    ExtractionRun, Requirement, PipelineRun,
)
from db.connection import get_session_factory
from orchestration.government_sync import sync_source, sync_all_sources

HTML_V1 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 45 years of age.</p>"
    b"<p>Applicants must have a skilled occupation.</p>"
    b"</body></html>"
)
HTML_V2 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 50 years of age.</p>"
    b"<p>Applicants must have a skilled occupation.</p>"
    b"</body></html>"
)
HASH_V1 = sha256_bytes(HTML_V1)
HASH_V2 = sha256_bytes(HTML_V2)


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


def _seed_source(session, source_id="SRC-AU-189", last_checked_at=None):
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
        status="ACTIVE",
        last_checked_at=last_checked_at,
    ))
    session.commit()


def _seed_source_with_doc(session, source_id="SRC-AU-189", content_hash=HASH_V1,
                          last_hash=HASH_V1, last_checked_at=None, version=1):
    _seed_source(session, source_id, last_checked_at=last_checked_at)
    src = session.query(GovernmentSource).filter_by(source_id=source_id).first()
    src.last_hash = last_hash
    doc = GovernmentDocument(
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
        current_version=version,
        language="en",
        raw_path="fetch:DOC-AU-189:v1",
        status="CURRENT",
    )
    session.add(doc)
    session.flush()
    session.add(DocumentVersion(
        document_id="DOC-AU-189",
        version_number=version,
        content_hash=content_hash,
        retrieved_at=datetime.now(timezone.utc),
        raw_path=f"fetch:DOC-AU-189:v{version}",
        change_type="INITIAL",
    ))
    session.commit()


def _patch_fetch(monkeypatch, raw_bytes):
    """Patch fetcher.fetch to return a given HTML body; count calls made."""
    calls = {"count": 0}

    def fetch(url, source_type=None, **kw):
        calls["count"] += 1
        return fetcher.FetchResult(
            url=url, status=fetcher.FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=raw_bytes,
            duration_ms=5, error=None,
        )

    monkeypatch.setattr(fetcher, "fetch", fetch)
    return calls


def _latest_run(session):
    return (
        session.query(PipelineRun)
        .order_by(PipelineRun.run_id.desc())
        .first()
    )


class TestRefetchLifecycle:
    def test_first_sync_bootstraps_v1_and_sets_last_hash(self, session, monkeypatch):
        _seed_source(session)
        calls = _patch_fetch(monkeypatch, HTML_V1)

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert calls["count"] == 1
        assert result.status == "changed"
        assert result.document_id == "DOC-AU-189"
        assert result.document_version == 1

        doc = session.get(GovernmentDocument, "DOC-AU-189")
        assert doc is not None
        assert doc.current_version == 1
        assert doc.content_hash == HASH_V1

        versions = session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").all()
        assert len(versions) == 1
        assert versions[0].change_type == "INITIAL"
        assert versions[0].content_hash == HASH_V1

        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert source.last_hash == HASH_V1
        assert source.last_checked_at is not None

        run = _latest_run(session)
        assert run.status == "COMPLETED"
        assert run.old_hash is None
        assert run.new_hash == HASH_V1

        # Downstream requirement proposal triggered (as the legacy bootstrap path did)
        assert session.query(ExtractionRun).count() >= 1
        assert session.query(Requirement).count() >= 1
        assert result.requirements_extracted >= 1

    def test_second_sync_unchanged_refetches_no_new_version(self, session, monkeypatch):
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1)
        calls = _patch_fetch(monkeypatch, HTML_V1)

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert calls["count"] == 1  # genuinely re-fetched, not a stored-hash shortcut
        assert result.status == "unchanged"
        assert result.document_id == "DOC-AU-189"
        assert result.document_version == 1

        assert session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").count() == 1
        assert session.query(ExtractionRun).count() == 0  # no re-extraction
        assert session.query(Requirement).count() == 0

        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert source.last_hash == HASH_V1
        assert source.last_checked_at is not None

        run = _latest_run(session)
        assert run.status == "COMPLETED"
        assert run.old_hash == HASH_V1
        assert run.new_hash == HASH_V1
        assert run.metadata_json == {}  # no change detection computed

    def test_second_sync_changed_creates_new_immutable_version(self, session, monkeypatch):
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1)
        calls = _patch_fetch(monkeypatch, HTML_V2)

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert calls["count"] == 1
        assert result.status == "changed"
        assert result.document_id == "DOC-AU-189"
        assert result.document_version == 2

        versions = session.query(DocumentVersion).filter_by(document_id="DOC-AU-189").all()
        assert len(versions) == 2
        v1, v2 = sorted(versions, key=lambda v: v.version_number)
        assert v1.version_number == 1
        assert v1.change_type == "INITIAL"
        assert v1.content_hash == HASH_V1  # version 1 untouched (immutable)
        assert v2.version_number == 2
        assert v2.change_type == "CHANGED"
        assert v2.content_hash == HASH_V2

        doc = session.get(GovernmentDocument, "DOC-AU-189")
        assert doc.current_version == 2
        assert doc.content_hash == HASH_V2

        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert source.last_hash == HASH_V2  # fresh fetched hash recorded

        run = _latest_run(session)
        assert run.status == "COMPLETED"
        assert run.old_hash == HASH_V1
        assert run.new_hash == HASH_V2
        cd = run.metadata_json["change_detection"]
        assert cd["raw"]["old_hash"] == HASH_V1
        assert cd["raw"]["new_hash"] == HASH_V2
        assert cd["raw"]["changed"] is True

        # Downstream extraction/requirement proposal re-triggered on v2
        assert session.query(ExtractionRun).count() >= 1
        assert session.query(Requirement).count() >= 1
        assert result.requirements_extracted >= 1


class TestCrawlFrequencyGating:
    def test_scheduled_sync_skips_source_not_yet_due(self, session, monkeypatch):
        recent = datetime.now(timezone.utc)
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1,
                              last_checked_at=recent)
        calls = _patch_fetch(monkeypatch, HTML_V1)

        results = sync_all_sources(session, trigger_type="SCHEDULED")
        session.commit()

        assert results == []
        assert calls["count"] == 0  # politeness: not due per crawl_frequency
        assert session.query(PipelineRun).count() == 0

    def test_scheduled_sync_includes_due_source(self, session, monkeypatch):
        stale = datetime.now(timezone.utc) - timedelta(days=2)
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1,
                              last_checked_at=stale)
        calls = _patch_fetch(monkeypatch, HTML_V1)

        results = sync_all_sources(session, trigger_type="SCHEDULED")
        session.commit()

        assert len(results) == 1
        assert results[0].status == "unchanged"
        assert calls["count"] == 1
        assert session.query(PipelineRun).count() == 1

    def test_manual_sync_bypasses_crawl_gate(self, session, monkeypatch):
        recent = datetime.now(timezone.utc)
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1,
                              last_checked_at=recent)
        calls = _patch_fetch(monkeypatch, HTML_V1)

        results = sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        assert len(results) == 1  # admin can always force a genuine re-fetch
        assert calls["count"] == 1
        assert session.query(PipelineRun).count() == 1

    def test_dry_run_scheduled_is_not_gated(self, session, monkeypatch):
        recent = datetime.now(timezone.utc)
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1,
                              last_checked_at=recent)
        calls = _patch_fetch(monkeypatch, HTML_V1)

        results = sync_all_sources(session, dry_run=True, trigger_type="SCHEDULED")
        session.commit()

        # dry-run preview still processes the source; fetch must not be reached
        assert len(results) == 1
        assert calls["count"] == 0
        assert session.query(PipelineRun).count() == 1