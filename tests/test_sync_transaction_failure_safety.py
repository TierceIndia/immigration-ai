"""Transaction failure-safety regression tests for the source sync pipeline.

Phase 4 acceptance criteria:
    1. When a database error aborts the transaction mid-sync (PostgreSQL
       ``InFailedSqlTransaction``), the failure handler must roll back before it
       writes the FAILED PipelineRun — it must never crash with an unhandled
       exception and must never lose the failure record.
    2. One failed source must not poison the remaining sources: A fails, but
       B/C/D still proceed and complete, and every source gets an audit row.
    3. A genuine DB failure during requirement compilation is recorded as FAILED
       (never PARTIAL, never silently swallowed), and old ACTIVE rules stay
       untouched.

These run hermetically on SQLite by *simulating* the PostgreSQL aborted-transaction
behaviour (a failure handler whose first statement raises); the real constraint-
violation proof runs on PostgreSQL in the Phase 4 E2E suite.
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
    Base, Country, ImmigrationProgram,
    GovernmentSource, GovernmentDocument, DocumentVersion,
    ExtractionRun, Requirement, PipelineRun,
)
from db.connection import get_session_factory
from db.repositories import PipelineRunRepo
import orchestration.government_sync as gov_sync
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


class InFailedSqlTransactionSim(Exception):
    """Simulates psycopg2.errors.InFailedSqlTransaction (PostgreSQL aborted tx)."""


class FailFirstAbortRepo(PipelineRunRepo):
    """A PipelineRunRepo whose first ``fail``/``partial`` raises like a statement
    executed on a PostgreSQL transaction aborted by an earlier DB error."""

    fail_calls = 0
    partial_calls = 0

    def fail(self, run_id, error):
        type(self).fail_calls += 1
        if getattr(type(self), "poison_fail", False) and type(self).fail_calls == 1:
            raise InFailedSqlTransactionSim("current transaction is aborted")
        return super().fail(run_id, error)

    def partial(self, run_id, error, **metrics):
        type(self).partial_calls += 1
        if type(self).partial_calls == 1 and getattr(
            type(self), "poison_partial", False
        ):
            raise InFailedSqlTransactionSim("current transaction is aborted")
        return super().partial(run_id, error, **metrics)


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


def _seed_source(session, source_id="SRC-AU-189", url="https://immi.homeaffairs.gov.au/189"):
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
        url=url,
        source_type="HTML",
        priority="PRIMARY",
        monitoring_method="WEB",
        crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.commit()


def _seed_approved_requirement(session, source_id="SRC-AU-189"):
    """Seed doc v1 + an APPROVED requirement so compilation genuinely runs.

    The sync then re-fetches different content (HTML_V2) → CHANGED → the compile
    step sees ``approved_count > 0`` and executes.
    """
    now = datetime.now(timezone.utc)
    doc = GovernmentDocument(
        document_id="DOC-AU-189",
        source_id=source_id,
        country_id="AU",
        program_id="SKILLED_189",
        visa_category="SKILLED",
        document_type="GENERAL",
        url="https://immi.homeaffairs.gov.au/189",
        title="Skilled 189",
        retrieved_at=now,
        content_hash=HASH_V1,
        current_version=1,
        language="en",
        raw_path="fetch:DOC-AU-189:v1",
        status="CURRENT",
    )
    session.add(doc)
    session.flush()
    dv = DocumentVersion(
        document_id="DOC-AU-189",
        version_number=1,
        content_hash=HASH_V1,
        retrieved_at=now,
        raw_path="fetch:DOC-AU-189:v1",
        change_type="INITIAL",
    )
    session.add(dv)
    session.flush()
    er = ExtractionRun(
        run_id="ER-TXNFIX-1",
        document_id="DOC-AU-189",
        document_version=1,
        document_version_id=dv.version_id,
        model="mock-llm-v1",
        prompt_version="t",
        status="COMPLETED",
    )
    session.add(er)
    session.flush()
    session.add(Requirement(
        requirement_id="REQ-TXNFIX-1",
        program_id="SKILLED_189",
        country_id="AU",
        semantic_key="skilled_189.age",
        category="ELIGIBILITY",
        name="Age",
        field="age",
        operator="LESSTHAN",
        value_json='{"value": 45, "unit": "years"}',
        mandatory=1,
        status="APPROVED",
        current_version=1,
        extraction_run_id="ER-TXNFIX-1",
    ))
    session.commit()


def _patch_fetch(monkeypatch, fail_urls=(), raw_bytes=HTML_V1):
    """Patch fetcher.fetch: OK for most URLs, raises ConnectionError for fail_urls."""
    calls = {"count": 0}

    def fetch(url, source_type=None, **kw):
        calls["count"] += 1
        if url in fail_urls:
            raise ConnectionError("network down")
        return fetcher.FetchResult(
            url=url, status=fetcher.FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=raw_bytes,
            duration_ms=5, error=None,
        )

    monkeypatch.setattr("fetch.backend.fetch", fetch)
    return calls


def _runs(session):
    return session.query(PipelineRun).order_by(PipelineRun.started_at).all()


class TestAbortedTransactionRecovery:
    def test_failure_recorder_rolls_back_then_persists_failed_run(self, session, monkeypatch):
        """A FAILED record must be persisted even when the first ``fail`` statement
        simulates running on a PostgreSQL-aborted transaction."""
        _seed_source(session)
        _patch_fetch(monkeypatch, fail_urls={"https://immi.homeaffairs.gov.au/189"})

        FailFirstAbortRepo.poison_fail = True
        FailFirstAbortRepo.fail_calls = 0
        monkeypatch.setattr(gov_sync, "PipelineRunRepo", FailFirstAbortRepo)
        try:
            result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
            session.commit()
        finally:
            FailFirstAbortRepo.poison_fail = False

        assert result.status == "error"
        assert "network down" in result.errors[0]
        runs = _runs(session)
        assert len(runs) == 1
        assert runs[0].status == "FAILED"
        assert any("network down" in e for e in runs[0].errors)

        # The session is left in a healthy state — the caller can keep working.
        session.add(GovernmentSource(
            source_id="SRC-AU-999",
            country_id="AU", program_id="SKILLED_189", visa_category="SKILLED",
            authority="Home Affairs", url="https://x.example/999",
            source_type="HTML", priority="PRIMARY", monitoring_method="WEB",
            crawl_frequency="DAILY", status="ACTIVE",
        ))
        session.commit()
        assert session.query(GovernmentSource).filter_by(source_id="SRC-AU-999").count() == 1

    def test_db_error_during_compilation_is_failed_never_partial(self, session, monkeypatch):
        """A DB error aborts the tx mid-compile: the PARTIAL handler would itself
        fail, so the run must be recorded FAILED after a rollback — not PARTIAL,
        and never an unhandled exception."""
        _seed_source(session)
        _seed_approved_requirement(session)
        _patch_fetch(monkeypatch, raw_bytes=HTML_V2)  # CHANGED → compilation runs

        # The DB error during compilation simulates the "constraint violation"
        # from the Phase 4 report; the partial/fail handlers are then poisoned
        # exactly like statements on a PostgreSQL aborted transaction.
        def _compile_that_aborts_tx(session, program_id, dry_run=False):
            raise RuntimeError("duplicate key value violates unique constraint")

        monkeypatch.setattr(gov_sync, "compile_approved_requirements", _compile_that_aborts_tx)
        FailFirstAbortRepo.poison_partial = True
        FailFirstAbortRepo.poison_fail = True
        FailFirstAbortRepo.partial_calls = 0
        FailFirstAbortRepo.fail_calls = 0
        monkeypatch.setattr(gov_sync, "PipelineRunRepo", FailFirstAbortRepo)
        try:
            result = sync_source(session, "SRC-AU-189", trigger_type="MANUAL")
            session.commit()
        finally:
            FailFirstAbortRepo.poison_partial = False
            FailFirstAbortRepo.poison_fail = False

        assert result.status == "error"
        runs = _runs(session)
        assert len(runs) == 1
        assert runs[0].status == "FAILED"
        assert any("violates unique constraint" in e for e in runs[0].errors)
        # No active rules were created — old knowledge untouched.
        from orchestration.government_sync import get_review_queue
        queue = get_review_queue(session, program_id="SKILLED_189")
        assert queue["counts"]["active"] == 0
        assert queue["counts"]["drafts"] == 0


class TestSourceIsolation:
    def test_one_failed_source_does_not_poison_the_batch(self, session, monkeypatch):
        """Source A explosions (exception escapes mid-run) must not stop B."""
        _seed_source(session, "SRC-AU-189", url="https://immi.homeaffairs.gov.au/189-fail")
        _seed_source(session, "SRC-AU-190",
                     url="https://immi.homeaffairs.gov.au/190")
        _patch_fetch(monkeypatch, fail_urls={"https://immi.homeaffairs.gov.au/189-fail"})

        monkeypatch.setattr(gov_sync, "_record_run_failure", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("failure handler itself crashed")
        ))
        results = sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        assert len(results) == 2
        by_id = {r.source_id: r for r in results}
        assert by_id["SRC-AU-189"].status == "error"
        assert by_id["SRC-AU-190"].status in ("changed", "unchanged")
        assert by_id["SRC-AU-190"].errors == []

        runs = _runs(session)
        assert len(runs) == 2
        assert {r.status for r in runs} == {"FAILED", "COMPLETED"}
        failed = next(r for r in runs if r.status == "FAILED")
        assert failed.source_id == "SRC-AU-189"
        completed = next(r for r in runs if r.status == "COMPLETED")
        assert completed.source_id == "SRC-AU-190"
        assert any("failure handler itself crashed" in e for e in failed.errors)

    def test_fetch_failure_records_failed_and_other_source_completes(self, session, monkeypatch):
        """The ordinary python-exception path already records FAILED and leaves the
        next source untouched."""
        _seed_source(session, "SRC-AU-189", url="https://immi.homeaffairs.gov.au/189-fail")
        _seed_source(session, "SRC-AU-190",
                     url="https://immi.homeaffairs.gov.au/190")
        _patch_fetch(monkeypatch, fail_urls={"https://immi.homeaffairs.gov.au/189-fail"})

        results = sync_all_sources(session, trigger_type="MANUAL")
        session.commit()

        assert [r.status for r in results] == ["error", "changed"]
        runs = _runs(session)
        assert {r.source_id: r.status for r in runs} == {
            "SRC-AU-189": "FAILED", "SRC-AU-190": "COMPLETED",
        }