"""Tests for the single retrieval path in pipeline.process_source (legacy CLI).

Retrieval is Playwright-only: process_source makes exactly ONE fetch attempt
per source and has nothing to escalate to. These tests pin that invariant and
the bookkeeping that replaces escalation - a refusal or a challenge page parks
the source for a human instead of being retried by another mechanism.

Mocks fetch.backend.fetch - never makes a real network call and never launches
a real browser. Uses an in-memory SQLite DB seeded with the FK rows
government_sources requires.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from db import SCHEMA_PATH, PHASE2_SCHEMA_PATH  # noqa: E402
from fetch.fetcher import FetchResult, FetchStatus  # noqa: E402


OK_BYTES = (
    b"<html><body><h1>Skilled Visa 189</h1><p>under 45 years of age, skilled occupation</p></body></html>"
)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(PHASE2_SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute("INSERT INTO countries (country_id, name, iso_code) VALUES ('AU', 'Australia', 'AUS')")
    c.execute(
        "INSERT INTO immigration_programs (program_id, country_id, visa_category, subclass_code, name, status) "
        "VALUES ('SKILLED_189', 'AU', 'SKILLED', '189', 'Skilled Independent', 'ACTIVE')"
    )
    c.commit()
    yield c
    c.close()


def _seed_source(conn):
    conn.execute(
        """
        INSERT INTO government_sources
            (source_id, country_id, program_id, visa_category, authority, url,
             source_type, priority, monitoring_method, crawl_frequency, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("SRC-AU-TEST", "AU", "SKILLED_189", "SKILLED", "Home Affairs",
         "https://immi.homeaffairs.gov.au/test", "HTML", "PRIMARY", "WEB", "DAILY", "ACTIVE"),
    )
    conn.commit()


def _source_row(conn):
    return conn.execute(
        "SELECT * FROM government_sources WHERE source_id = 'SRC-AU-TEST'"
    ).fetchone()


def _blocked_result(url):
    return FetchResult(
        url=url, status=FetchStatus.ACCESS_BLOCKED, http_status=403,
        content_type=None, raw_bytes=None, duration_ms=100, error="HTTP 403",
        acquisition="playwright_page",
    )


def _requires_browser_result(url):
    return FetchResult(
        url=url, status=FetchStatus.REQUIRES_BROWSER, http_status=200,
        content_type="text/html", raw_bytes=None, duration_ms=100,
        error="challenge page served to a real browser",
        acquisition="playwright_page",
    )


def _failed_result(url):
    return FetchResult(
        url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
        content_type=None, raw_bytes=None, duration_ms=50,
        error="net::ERR_CONNECTION_RESET", acquisition="playwright_page",
    )


def _ok_result(url):
    return FetchResult(
        url=url, status=FetchStatus.OK, http_status=200,
        content_type="text/html", raw_bytes=OK_BYTES, duration_ms=100, error=None,
        acquisition="playwright_page",
    )


def _count_fetches(monkeypatch, result_for):
    """Patch the one retrieval entry point and record every URL it is given."""
    calls: list[str] = []

    def _fetch(url, source_type="HTML", **kw):
        calls.append(url)
        return result_for(url)

    monkeypatch.setattr("fetch.backend.fetch", _fetch)
    return calls


def _trap_legacy_browser(monkeypatch):
    """The retired escalation entry point must never be reached."""
    reached = {"count": 0}

    def _trap(url, source_type="HTML", **kw):
        reached["count"] += 1
        return _ok_result(url)

    monkeypatch.setattr("fetch.browser_fetcher.fetch_with_browser", _trap)
    return reached


def test_a_refusal_is_one_attempt_and_parks_the_source(conn, monkeypatch):
    """403/429 means the origin said no. One attempt, no second mechanism, and
    the source is parked in NEEDS_MANUAL_CAPTURE so the scheduler stops
    hammering it."""
    _seed_source(conn)
    import pipeline as pl

    calls = _count_fetches(monkeypatch, _blocked_result)
    legacy = _trap_legacy_browser(monkeypatch)

    report = pl.process_source(conn, _source_row(conn))

    assert calls == ["https://immi.homeaffairs.gov.au/test"], calls
    assert legacy["count"] == 0, "the retired escalation path must never run"
    assert report["status"] == "ACCESS_BLOCKED", report
    row = conn.execute(
        "SELECT status FROM government_sources WHERE source_id = 'SRC-AU-TEST'"
    ).fetchone()
    assert row["status"] == "NEEDS_MANUAL_CAPTURE", f"got {row['status']}"


def test_a_challenge_page_is_one_attempt_and_parks_the_source(conn, monkeypatch):
    """REQUIRES_BROWSER now means "a real browser already ran and was still
    served a challenge page" - unrecoverable by automation, so it is handed to
    a human rather than retried."""
    _seed_source(conn)
    import pipeline as pl

    calls = _count_fetches(monkeypatch, _requires_browser_result)
    legacy = _trap_legacy_browser(monkeypatch)

    report = pl.process_source(conn, _source_row(conn))

    assert len(calls) == 1, calls
    assert legacy["count"] == 0
    assert report["status"] == "REQUIRES_BROWSER", report
    row = conn.execute(
        "SELECT status FROM government_sources WHERE source_id = 'SRC-AU-TEST'"
    ).fetchone()
    assert row["status"] == "NEEDS_MANUAL_CAPTURE", f"got {row['status']}"


def test_a_transient_fault_does_not_park_the_source(conn, monkeypatch):
    """A plain network fault is not a human problem - it is marked BROKEN, not
    NEEDS_MANUAL_CAPTURE, so it is retried on the next run."""
    _seed_source(conn)
    import pipeline as pl

    calls = _count_fetches(monkeypatch, _failed_result)
    legacy = _trap_legacy_browser(monkeypatch)

    report = pl.process_source(conn, _source_row(conn))

    assert len(calls) == 1, calls
    assert legacy["count"] == 0
    assert report["status"] == "FETCH_FAILED", report
    row = conn.execute(
        "SELECT status FROM government_sources WHERE source_id = 'SRC-AU-TEST'"
    ).fetchone()
    assert row["status"] == "BROKEN", f"got {row['status']}"


def test_a_successful_fetch_uses_exactly_one_attempt(conn, monkeypatch):
    _seed_source(conn)
    import pipeline as pl

    calls = _count_fetches(monkeypatch, _ok_result)
    legacy = _trap_legacy_browser(monkeypatch)

    report = pl.process_source(conn, _source_row(conn))

    assert report["status"] == "OK", report
    assert len(calls) == 1, calls
    assert legacy["count"] == 0


def test_scheduler_skips_needs_manual_capture_sources(conn, monkeypatch):
    _seed_source(conn)
    import pipeline as pl

    # One ACTIVE source (must be processed) + one NEEDS_MANUAL_CAPTURE source (must be skipped).
    conn.execute(
        "UPDATE government_sources SET status = 'NEEDS_MANUAL_CAPTURE' WHERE source_id = 'SRC-AU-TEST'"
    )
    conn.execute(
        "INSERT INTO government_sources "
        "(source_id, country_id, program_id, visa_category, authority, url, "
        " source_type, priority, monitoring_method, crawl_frequency, status) "
        "VALUES ('SRC-AU-ACTIVE', 'AU', 'SKILLED_189', 'SKILLED', 'Home Affairs', "
        "        'https://immi.homeaffairs.gov.au/active', 'HTML', 'PRIMARY', 'WEB', 'DAILY', 'ACTIVE')"
    )
    conn.commit()

    # Exercise the REAL scheduler/pipeline selection path (run_pipeline) against this DB.
    monkeypatch.setattr(pl, "init_schema", lambda *a, **kw: conn)
    fetched = _count_fetches(monkeypatch, _ok_result)

    report = pl.run_pipeline()

    results = report["results"]
    # The ACTIVE source IS fetched; the NEEDS_MANUAL_CAPTURE source is NOT.
    assert "https://immi.homeaffairs.gov.au/active" in fetched, fetched
    assert "https://immi.homeaffairs.gov.au/test" not in fetched, fetched
    assert all(r["source_id"] != "SRC-AU-TEST" for r in results), "NEEDS_MANUAL_CAPTURE source must be skipped"
    assert any(r["source_id"] == "SRC-AU-ACTIVE" for r in results)
