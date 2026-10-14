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

import fetch.fetcher as fetcher
from change_detection.hashing import sha256_bytes
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


class TestEmptyContentIsVisiblePartial:
    """A source that fetches real 200 OK content that genuinely changed, but
    whose extractor produces zero document chunks (e.g. a JS-rendered SPA shell
    with no static content — the real SRC-AU-CRICOS-REGISTER symptom), must
    record a visible PARTIAL PipelineRun, not a silent COMPLETED that looks
    identical to a genuine "nothing to extract" success."""

    def test_zero_chunk_change_is_partial_not_completed(self, session, monkeypatch):
        _seed_source(session)
        _patch_fetch(monkeypatch, b"<html><body></body></html>")

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert result.status == "changed"  # fetch + content change were real
        assert result.requirements_extracted == 0
        run = _latest_run(session)
        assert run.status == "PARTIAL"
        assert any("zero document chunks" in e for e in run.errors)

    def test_zero_chunk_second_fetch_after_real_content_is_also_partial(self, session, monkeypatch):
        """Content that changes FROM real text TO an empty shell (not just a
        bootstrap case) must be caught too."""
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1)
        _patch_fetch(monkeypatch, b"<html><body></body></html>")

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert result.status == "changed"
        run = _latest_run(session)
        assert run.status == "PARTIAL"

    def test_normal_content_with_chunks_stays_completed(self, session, monkeypatch):
        """Sanity check: the fix must not turn every successful sync into PARTIAL."""
        _seed_source(session)
        _patch_fetch(monkeypatch, HTML_V1)

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert result.status == "changed"
        run = _latest_run(session)
        assert run.status == "COMPLETED"


class TestFailedFetchDoesNotPoisonKnownGoodKnowledge:
    """A later failure must never damage knowledge that already worked.

    These are genuine failure injections: the fetch result or the extraction/LLM
    call actually raises, and the assertions then check that the previously
    ACTIVE rule, the immutable document versions and the source row survive.
    """

    HTML_MULTI = (
        b"<html><head><title>Skilled 189</title></head><body>"
        b"<h1>Age</h1><p>Applicants must be under 45 years of age.</p>"
        b"<h1>English</h1><p>Applicants must have competent English.</p>"
        b"<h1>Experience</h1>"
        b"<p>Applicants must have at least 3 years of work experience.</p>"
        b"</body></html>"
    )

    @staticmethod
    def _make_active_rule(session):
        from rules.rule_repository import EnhancedRuleRepo

        EnhancedRuleRepo(session).create(
            rule_id="RULE-AGE-189", requirement_id="REQ-AGE-189",
            program_id="SKILLED_189", category="age", field="age",
            operator="LT", value_json="45", mandatory=True, critical=True,
            message="Age must be under 45", status="ACTIVE", current_version=1,
            source_id="SRC-AU-189", document_id="DOC-AU-189",
            document_version=1, chunk_id=1, evidence_text="Under 45",
        )
        session.commit()

    def test_failure_preserves_document_versions_and_source(self, session, monkeypatch):
        """A failed re-fetch must not create a version or clear last_hash.

        Losing last_hash would make the next sync re-bootstrap the document and
        duplicate knowledge; losing a version would destroy immutable history.
        """
        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1)
        self._make_active_rule(session)

        def _fetch_fails(url, source_type=None, **kw):
            raise RuntimeError("injected network failure")

        monkeypatch.setattr(fetcher, "fetch", _fetch_fails)

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert result.status == "error"
        assert any("injected network failure" in e for e in result.errors)

        # Exactly the one pre-existing version - no new version invented.
        versions = session.query(DocumentVersion).filter_by(
            document_id="DOC-AU-189").all()
        assert len(versions) == 1
        assert versions[0].version_number == 1
        assert versions[0].content_hash == HASH_V1

        # The source keeps its known-good hash and stays in the sync set.
        src = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert src.last_hash == HASH_V1
        assert src.status == "ACTIVE"

    def test_failure_after_good_sync_does_not_touch_activated_rules(self, session, monkeypatch):
        """Do the good sync, then fail the next one; the rule must be untouched."""
        from rules.rule_repository import EnhancedRuleRepo

        _seed_source(session)
        _patch_fetch(monkeypatch, HTML_V1)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        # Plant an activated rule as the governance stage would have.
        src = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        doc = session.query(GovernmentDocument).filter_by(document_id="DOC-AU-189").first()
        assert doc is not None and src.last_hash == HASH_V1
        self._make_active_rule(session)

        def _fetch_fails(url, source_type=None, **kw):
            raise RuntimeError("injected outage on second sync")

        monkeypatch.setattr(fetcher, "fetch", _fetch_fails)
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        active = EnhancedRuleRepo(session).list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-AGE-189"
        assert active[0].status == "ACTIVE"
        assert active[0].document_version == 1
        assert active[0].value_json == "45"

    def test_extraction_failure_keeps_previously_active_rule(self, session, monkeypatch):
        """The extraction entry point itself raising must not disturb rules."""
        from rules.rule_repository import EnhancedRuleRepo

        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1)
        self._make_active_rule(session)

        # Genuinely changed content so extraction is actually reached.
        _patch_fetch(monkeypatch, HTML_V2)

        def _extract_raises(*a, **k):
            raise RuntimeError("injected extraction failure")

        monkeypatch.setattr(
            "utils.fetch_document.run_requirement_extraction", _extract_raises
        )

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        assert result.status == "error"
        assert any("injected extraction failure" in e for e in result.errors)

        active = EnhancedRuleRepo(session).list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-AGE-189"
        assert active[0].status == "ACTIVE"
        assert active[0].document_version == 1

    def test_llm_failure_preserves_active_rule(self, session, monkeypatch):
        """A real LLM failure must not disturb the previously ACTIVE rule.

        This genuinely raises from the client factory the extraction path calls
        (``utils.fetch_document.get_client``), rather than asserting an untouched
        row is still untouched.
        """
        from rules.rule_repository import EnhancedRuleRepo

        _seed_source_with_doc(session, content_hash=HASH_V1, last_hash=HASH_V1)
        EnhancedRuleRepo(session).create(
            rule_id="RULE-AGE-189", requirement_id="REQ-AGE-189",
            program_id="SKILLED_189", category="age", field="age",
            operator="LT", value_json="45", mandatory=True, critical=True,
            message="Age must be under 45", status="ACTIVE", current_version=1,
            source_id="SRC-AU-189", document_id="DOC-AU-189",
            document_version=1, chunk_id=1, evidence_text="Under 45",
        )
        session.commit()

        # A genuinely changed document arrives (hermetic - never hits the network).
        _patch_fetch(monkeypatch, HTML_V2)

        def _llm_explodes(*a, **k):
            raise RuntimeError("injected LLM provider failure")

        monkeypatch.setattr("utils.fetch_document.get_client", _llm_explodes)

        result = sync_source(session, "SRC-AU-189", trigger_type="SCHEDULED")
        session.commit()

        assert result.status == "error"
        assert any("injected LLM provider failure" in e for e in result.errors)
        assert _latest_run(session).status == "FAILED"

        # Still exactly one ACTIVE rule, unchanged, still traced to v1.
        active = EnhancedRuleRepo(session).list_active("SKILLED_189")
        assert len(active) == 1
        assert active[0].rule_id == "RULE-AGE-189"
        assert active[0].status == "ACTIVE"
        assert active[0].document_version == 1


class TestPartialExtractionIsDurable:
    """Requirements already extracted must survive a later provider failure.

    The live Migration Regulations 1994 run made 139 real LLM calls, then hit a
    provider quota wall. Persisting only after the whole loop meant all 139
    calls were discarded and the FAILED run looked as though it had produced
    nothing. Each candidate is now persisted as it completes.
    """

    HTML_MULTI = (
        b"<html><head><title>Skilled 189</title></head><body>"
        b"<h1>Age</h1><p>Applicants must be under 45 years of age.</p>"
        b"<h1>English</h1><p>Applicants must have competent English.</p>"
        b"<h1>Experience</h1>"
        b"<p>Applicants must have at least 3 years of work experience.</p>"
        b"</body></html>"
    )

    def test_requirements_before_failure_are_kept(self, session, monkeypatch):
        from extraction.llm_client import MockLLMClient
        from db.models import ExtractionRun

        _seed_source(session)
        _patch_fetch(monkeypatch, self.HTML_MULTI)

        real = MockLLMClient()
        state = {"calls": 0}

        class _FailOnThird:
            """Delegates to the deterministic mock, then fails on the 3rd call."""

            def get_system_prompt(self, visa_category=None):
                return ""

            def extract(self, input_context):
                state["calls"] += 1
                if state["calls"] >= 3:
                    raise RuntimeError("injected quota exhaustion mid-document")
                return real.extract(input_context)

        monkeypatch.setattr(
            "utils.fetch_document.get_client", lambda *a, **k: _FailOnThird()
        )

        result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        # The failure is surfaced honestly, never swallowed.
        assert result.status == "error"
        assert any("quota exhaustion" in e for e in result.errors)
        assert state["calls"] == 3

        run = (
            session.query(ExtractionRun)
            .order_by(ExtractionRun.run_id.desc())
            .first()
        )
        assert run.status == "FAILED"
        assert "quota exhaustion" in (run.error or "")

        # The run must not report zero llm_calls: real calls were made before
        # the provider gave up.
        assert int(run.llm_calls) == 2

        # The requirement extracted from the second candidate is still durable.
        requirements = session.query(Requirement).all()
        assert len(requirements) >= 1
        assert any(
            "English" in (r.description or "") for r in requirements
        ), [r.description for r in requirements]

    def test_stale_running_run_is_abandoned_before_a_new_run(self, session, monkeypatch):
        """A RUNNING row left by a killed process must not stay RUNNING forever."""
        from db.models import DocumentVersion, ExtractionRun

        _seed_source(session)
        _patch_fetch(monkeypatch, self.HTML_MULTI)

        # First, plant a RUNNING run the way a hard-killed process would leave it.
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        version = (
            session.query(DocumentVersion)
            .filter_by(document_id="DOC-AU-189", version_number=1)
            .first()
        )
        stale = ExtractionRun(
            run_id="RUN-STALE000000000001",
            document_id="DOC-AU-189",
            document_version=1,
            document_version_id=version.version_id,
            model="gemini-flash-lite-latest",
            prompt_version="v1",
            status="RUNNING",
            dry_run=0,
        )
        session.add(stale)
        session.commit()
        assert stale.status == "RUNNING"

        # A later extraction over the same document must reconcile it.
        _patch_fetch(monkeypatch, self.HTML_MULTI.replace(b"45", b"50"))
        sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
        session.commit()

        session.refresh(stale)
        assert stale.status == "FAILED"
        assert "abandoned" in (stale.error or "")

        # Only the stale row was reconciled; the completed run is untouched.
        statuses = {
            r.run_id: r.status
            for r in session.query(ExtractionRun).all()
        }
        assert statuses["RUN-STALE000000000001"] == "FAILED"
        assert "COMPLETED" in statuses.values()


class TestSyncTimingSummary:
    """Item 1: a structured per-source timing line plus one summary table
    logged at the end of a sync. Fake-driven — no network, no browser."""

    def test_sync_all_sources_logs_a_timing_summary_table(self, session, monkeypatch, caplog):
        session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        session.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        for source_id, path in (("SRC-AU-A", "a"), ("SRC-AU-B", "b")):
            session.add(GovernmentSource(
                source_id=source_id, country_id="AU", program_id="SKILLED_189",
                visa_category="SKILLED", authority="Home Affairs",
                url=f"https://immi.homeaffairs.gov.au/{path}", source_type="HTML",
                priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
                status="ACTIVE",
            ))
        session.commit()
        _patch_fetch(monkeypatch, HTML_V1)

        import logging
        with caplog.at_level(logging.INFO, logger="orchestration.government_sync"):
            results = sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        assert len(results) == 2
        assert all(r.timing is not None for r in results)

        summary_records = [r for r in caplog.records if "Sync timing summary" in r.getMessage()]
        assert len(summary_records) == 1
        table = summary_records[0].getMessage()
        assert "SRC-AU-A" in table
        assert "SRC-AU-B" in table
        assert "TOTAL" in table

    def test_timing_row_has_the_fields_item_1_asks_for(self, session, monkeypatch):
        _seed_source(session, "SRC-AU-189")
        _patch_fetch(monkeypatch, HTML_V1)

        results = sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        timing = results[0].timing
        assert timing.source_id == "SRC-AU-189"
        assert timing.method == "http"
        assert timing.total_ms >= 0
        assert timing.limiter_wait_ms >= 0
        assert timing.extract_ms is not None
        # llm_calls/llm_wait_ms are present even when 0 (no LLM key configured in tests).
        assert timing.llm_calls >= 0
        assert timing.llm_wait_ms >= 0


class TestSharedBrowserAcrossASyncRun:
    """Item 4: sync_all_sources() shares one real browser across every
    escalating source in the run, instead of each launching its own.
    Drives the real BrowserSession/_patchright_fetch path through a fake
    patchright.sync_api module - no real browser, no network."""

    @staticmethod
    def _install_fake_patchright(monkeypatch, status=200):
        import sys
        import types

        sink = []

        class _FakeResponse:
            def __init__(self):
                self.status = status
                self.headers = {"content-type": "text/html"}

        class _FakePage:
            def set_default_timeout(self, ms):
                pass

            def route(self, pattern, handler):
                pass

            def goto(self, url, wait_until=None):
                return _FakeResponse()

            def wait_for_selector(self, selector, timeout=None):
                pass

            def content(self):
                return "<html><body>" + ("blocked " * 10) + "</body></html>"

        class _FakeContext:
            def new_page(self):
                return _FakePage()

            def close(self):
                sink.append("context.close")

        class _FakeBrowser:
            def new_context(self, **kwargs):
                return _FakeContext()

            def close(self):
                sink.append("browser.close")

        class _FakeChromium:
            def launch(self, **kwargs):
                sink.append("launch")
                return _FakeBrowser()

        class _FakePatchright:
            chromium = _FakeChromium()

        class _FakeCM:
            def __enter__(self):
                return _FakePatchright()

            def __exit__(self, *exc_info):
                return False

        sync_api = types.ModuleType("patchright.sync_api")
        sync_api.sync_playwright = lambda: _FakeCM()
        patchright_pkg = types.ModuleType("patchright")
        patchright_pkg.sync_api = sync_api
        monkeypatch.setitem(sys.modules, "patchright", patchright_pkg)
        monkeypatch.setitem(sys.modules, "patchright.sync_api", sync_api)
        return sink

    def test_two_escalating_sources_share_one_browser_launch(self, session, monkeypatch):
        session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        session.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        for source_id, path in (("SRC-AU-A", "a"), ("SRC-AU-B", "b")):
            session.add(GovernmentSource(
                source_id=source_id, country_id="AU", program_id="SKILLED_189",
                visa_category="SKILLED", authority="Home Affairs",
                url=f"https://immi.homeaffairs.gov.au/{path}", source_type="HTML",
                priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
                status="ACTIVE",
            ))
        session.commit()

        def requires_browser_fetch(url, source_type="HTML", **kw):
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.REQUIRES_BROWSER, http_status=403,
                content_type="text/html", raw_bytes=None, duration_ms=5, error="challenge",
            )

        monkeypatch.setattr(fetcher, "fetch", requires_browser_fetch)
        # The browser's own response is a genuine 200 (not another block) -
        # otherwise the first ACCESS_BLOCKED would pause the whole host and
        # the second source would never even be attempted, which would not
        # test browser reuse at all.
        sink = self._install_fake_patchright(monkeypatch, status=200)

        results = sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        assert len(results) == 2
        assert sink.count("launch") == 1, f"expected exactly one browser launch, got: {sink}"
        assert sink.count("context.close") == 2
        assert sink.count("browser.close") == 1

    def test_browser_never_launched_when_no_source_escalates(self, session, monkeypatch):
        _seed_source(session, "SRC-AU-189")
        _patch_fetch(monkeypatch, HTML_V1)
        sink = self._install_fake_patchright(monkeypatch)

        sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        assert sink == [], "a plain-HTTP-only run must never launch a browser"
