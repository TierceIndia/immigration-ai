"""Tests for the browser-fetch escalation path in pipeline.process_source.

Mocks fetch() and browser_fetcher.fetch_with_browser() — never makes a real
network call and never launches a real browser. Uses an in-memory SQLite DB seeded
with the FK rows government_sources requires.
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
    )


def _ok_result(url):
    return FetchResult(
        url=url, status=FetchStatus.OK, http_status=200,
        content_type="text/html", raw_bytes=OK_BYTES, duration_ms=100, error=None,
    )


def test_escalates_to_browser_when_plain_fetch_blocked(conn, monkeypatch):
    _seed_source(conn)
    import pipeline as pl

    monkeypatch.setattr(pl, "fetch", lambda url, source_type="HTML", **kw: _blocked_result(url))

    browser_called = {}
    monkeypatch.setattr(
        "fetch.browser_fetcher.fetch_with_browser",
        lambda url, source_type="HTML": (browser_called.setdefault("url", url), _ok_result(url))[1],
    )

    report = pl.process_source(conn, _source_row(conn))

    assert browser_called.get("url") == "https://immi.homeaffairs.gov.au/test"
    assert report["status"] == "OK", report
    row = conn.execute(
        "SELECT status FROM government_sources WHERE source_id = 'SRC-AU-TEST'"
    ).fetchone()
    assert row["status"] == "ACTIVE"


def test_no_escalation_when_plain_fetch_succeeds(conn, monkeypatch):
    _seed_source(conn)
    import pipeline as pl

    monkeypatch.setattr(pl, "fetch", lambda url, source_type="HTML", **kw: _ok_result(url))

    called = {}
    monkeypatch.setattr(
        "fetch.browser_fetcher.fetch_with_browser",
        lambda url, source_type="HTML": called.setdefault("called", True) or _ok_result(url),
    )

    report = pl.process_source(conn, _source_row(conn))
    assert report["status"] == "OK", report
    assert "called" not in called, "browser_fetcher should NOT be called"


def test_sets_needs_manual_capture_when_browser_also_blocked(conn, monkeypatch):
    _seed_source(conn)
    import pipeline as pl

    monkeypatch.setattr(pl, "fetch", lambda url, source_type="HTML", **kw: _blocked_result(url))
    monkeypatch.setattr(
        "fetch.browser_fetcher.fetch_with_browser",
        lambda url, source_type="HTML": _blocked_result(url),
    )

    report = pl.process_source(conn, _source_row(conn))

    assert report["status"] == "ACCESS_BLOCKED", report
    row = conn.execute(
        "SELECT status FROM government_sources WHERE source_id = 'SRC-AU-TEST'"
    ).fetchone()
    assert row["status"] == "NEEDS_MANUAL_CAPTURE", f"got {row['status']}"


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
    captured = {}
    monkeypatch.setattr(pl, "fetch", lambda url, source_type="HTML", **kw: (captured.setdefault("urls", []).append(url), _ok_result(url))[1])

    report = pl.run_pipeline()

    fetched = captured.get("urls", [])
    results = report["results"]
    # The ACTIVE source IS fetched; the NEEDS_MANUAL_CAPTURE source is NOT.
    assert "https://immi.homeaffairs.gov.au/active" in fetched, fetched
    assert "https://immi.homeaffairs.gov.au/test" not in fetched, fetched
    assert all(r["source_id"] != "SRC-AU-TEST" for r in results), "NEEDS_MANUAL_CAPTURE source must be skipped"
    assert any(r["source_id"] == "SRC-AU-ACTIVE" for r in results)
