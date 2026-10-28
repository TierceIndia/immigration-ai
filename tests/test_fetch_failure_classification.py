"""Fetch failure classification — explicit, machine-readable failure classes.

Phase 4 acceptance criteria (fetch failure classification):
    DNS resolution failure, network timeout, TLS error, HTTP status code and bot
    challenge must each be recorded as an explicit error type with metadata
    (http_status, error, consecutive_failures) on the source and in fetch_log —
    not collapsed into a single opaque FETCH_FAILED string.
"""
from __future__ import annotations

import socket
import sys
from datetime import datetime, timezone

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher
from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource, FetchLog,
)
from db.connection import get_session_factory
from utils.fetch_document import fetch_document_and_store


class _FakeMaxRetryError(Exception):
    """Mimics urllib3.exceptions.MaxRetryError's ``.reason`` attribute."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _dns_error() -> requests.exceptions.ConnectionError:
    # Real-world shape: requests wraps urllib3 MaxRetryError whose .reason is gaierror.
    return requests.exceptions.ConnectionError(
        _FakeMaxRetryError(socket.gaierror(-2, "Name or service not known"))
    )


def _patch_requests(monkeypatch, exc):
    def get(*a, **k):
        raise exc

    monkeypatch.setattr(requests, "get", get)


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


# ---------------------------------------------------------------------------
# fetcher.fetch classification of transport exceptions
# ---------------------------------------------------------------------------
class TestTransportExceptionClassification:
    def test_dns_resolution_failure_is_classified(self, monkeypatch):
        _patch_requests(monkeypatch, _dns_error())
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.status == fetcher.FetchStatus.FETCH_FAILED
        assert res.error_type == "dns_resolution_failure"
        assert res.http_status is None
        assert res.error  # human-readable detail preserved

    def test_connect_timeout_is_classified(self, monkeypatch):
        _patch_requests(monkeypatch, requests.exceptions.ConnectTimeout("boom"))
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.error_type == "connect_timeout"

    def test_read_timeout_is_classified(self, monkeypatch):
        _patch_requests(monkeypatch, requests.exceptions.ReadTimeout("boom"))
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.error_type == "read_timeout"

    def test_tls_error_is_classified(self, monkeypatch):
        _patch_requests(monkeypatch, requests.exceptions.SSLError("certificate verify failed"))
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.error_type == "tls_error"

    def test_generic_connection_error_is_classified(self, monkeypatch):
        _patch_requests(monkeypatch, requests.exceptions.ConnectionError("peer reset"))
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.error_type == "connection_error"

    def test_too_many_redirects_is_classified(self, monkeypatch):
        _patch_requests(monkeypatch, requests.exceptions.TooManyRedirects("loop"))
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.error_type == "too_many_redirects"


# ---------------------------------------------------------------------------
# Response-classified failures stay distinct (error_type is for the network layer)
# ---------------------------------------------------------------------------
class TestResponseClassificationUnaffected:
    def test_http_403_remains_access_blocked_with_status(self, monkeypatch):
        class Resp:
            status_code = 403
            content = b"<html>denied</html>"
            text = "<html>denied</html>"
            headers = {"Content-Type": "text/html"}

        monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.status == fetcher.FetchStatus.ACCESS_BLOCKED
        assert res.http_status == 403
        assert res.error_type is None  # HTTP-class failures already carry http_status

    def test_bot_challenge_is_classified_via_fetch_status(self, monkeypatch):
        class Resp:
            status_code = 403
            content = b"checking your browser..."
            text = "checking your browser..."
            headers = {"Content-Type": "text/html"}

        monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")
        assert res.status == fetcher.FetchStatus.REQUIRES_BROWSER


# ---------------------------------------------------------------------------
# Persistence in source metadata + fetch_log
# ---------------------------------------------------------------------------
class TestFailureMetadataPersistence:
    def test_dns_failure_metadata_stored_on_source_and_fetch_log(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()

        def fetch(url, source_type=None, **kw):
            from fetch.fetcher import _classify_exception
            exc = _dns_error()
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.FETCH_FAILED, http_status=None,
                content_type=None, raw_bytes=None, duration_ms=10,
                error="Failed to resolve", error_type=_classify_exception(exc),
            )

        monkeypatch.setattr("fetch.backend.fetch", fetch)
        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "FETCH_FAILED"
        assert outcome.error_type == "dns_resolution_failure"

        src = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert src.fetch_status == "FETCH_FAILED"
        assert src.last_error_type == "dns_resolution_failure"
        assert src.last_error == "Failed to resolve"
        assert src.consecutive_failures == 1

        log = session.query(FetchLog).one()
        assert log.error_type == "dns_resolution_failure"
        assert log.status == "FETCH_FAILED"

    def test_success_resets_error_type(self, session, monkeypatch):
        _seed_source(session)
        source = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        source.last_error_type = "dns_resolution_failure"
        source.fetch_status = "FETCH_FAILED"
        session.commit()

        ok_bytes = (
            b"<html><head><title>Skilled 189</title></head><body>"
            b"<h1>Eligibility</h1><p>Applicants must be under 45.</p></body></html>"
        )

        def fetch(url, source_type=None, **kw):
            return fetcher.FetchResult(
                url=url, status=fetcher.FetchStatus.OK, http_status=200,
                content_type="text/html", raw_bytes=ok_bytes, duration_ms=5,
                error=None, error_type=None,
            )

        monkeypatch.setattr("fetch.backend.fetch", fetch)
        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "OK"
        src = session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()
        assert src.fetch_status == "OK"
        assert src.last_error_type is None
        assert src.last_error is None
        assert src.consecutive_failures == 0