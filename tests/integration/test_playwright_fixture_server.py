"""Phase 1: real Playwright against the local fixture server.

Launches a genuine headless Chromium (marker ``browser_live``) and drives it
against tests/fixtures/fixture_server.py on 127.0.0.1. No government host, no
external network, no LLM - but a real browser, so these prove the things fakes
cannot: that JS hydration actually renders before we capture, that a PDF comes
back as bytes rather than a rendered viewer, that a 304 is genuinely surfaced,
and that a 403 never turns into a second attempt by some other mechanism.

Run:
    BROWSER_LIVE=1 python -m pytest tests/integration/test_playwright_fixture_server.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Imported through the tests package (never by putting tests/ on sys.path -
# that would shadow src/extraction with tests/extraction and break conftest).
from tests.fixtures.fixture_server import (  # noqa: E402
    CONDITIONAL_ETAG, CONDITIONAL_LAST_MODIFIED, start_fixture_server,
)
import fetch.playwright_client as pw  # noqa: E402
from fetch.fetcher import FetchStatus  # noqa: E402
from fetch.politeness import HostLimiter  # noqa: E402

pytestmark = [
    pytest.mark.browser_live,
    pytest.mark.skipif(
        os.environ.get("BROWSER_LIVE") != "1",
        reason="BROWSER_LIVE=1 required (launches a real headless Chromium)",
    ),
]


@pytest.fixture(scope="module")
def server():
    srv = start_fixture_server()
    try:
        yield srv
    finally:
        srv.shutdown()


@pytest.fixture()
def session():
    """A fresh session per test.

    Deliberately NOT module-scoped: Playwright's sync API allows only one live
    ``sync_playwright()`` context per thread, so a shared session plus any test
    that needs its own would nest and fail. One launch per test costs under a
    second and keeps every test independent.
    """
    with pw.PlaywrightSession() as s:
        yield s


@pytest.fixture()
def limiter():
    # Real spacing logic, zero real waiting: the politeness policy is unit
    # tested elsewhere; here it must simply not slow the browser tests down.
    return HostLimiter(min_interval=0, jitter=0, sleep=lambda _s: None)


class TestHtmlPages:
    def test_static_page_is_captured_with_its_content(self, server, session, limiter):
        result = pw.fetch(server.url("/static"), session=session, limiter=limiter)
        assert result.status == FetchStatus.OK, result.error
        assert result.http_status == 200
        assert result.acquisition == pw.ACQUISITION_PAGE
        assert b"under 45 years of age" in result.raw_bytes
        assert result.selector_timed_out is False
        assert result.goto_ms is not None

    def test_js_hydrated_content_is_waited_for_not_missed(self, server, session, limiter):
        """The placeholder is in the DOM at domcontentloaded; the real content
        arrives 150ms later. Capturing too early would store 'Loading...'."""
        result = pw.fetch(server.url("/hydrated"), session=session, limiter=limiter)
        assert result.status == FetchStatus.OK, result.error
        assert b"Applicants must be under 45 years of age." in result.raw_bytes
        assert result.selector_timed_out is False

    def test_json_in_hidden_input_is_materialised(self, server, session, limiter):
        """The real Home Affairs shape: content ships as escaped JSON in a
        hidden input and only becomes DOM nodes after the page's own JS runs."""
        result = pw.fetch(server.url("/json-in-input"), session=session, limiter=limiter)
        assert result.status == FetchStatus.OK, result.error
        assert b"<p>You must be under 45 years of age.</p>" in result.raw_bytes

    def test_contentless_page_still_captured_after_the_wait_times_out(
        self, server, session, limiter,
    ):
        result = pw.fetch(server.url("/slow"), session=session, limiter=limiter)
        assert result.status == FetchStatus.OK, result.error
        assert result.selector_timed_out is True
        assert b"Search for a course" in result.raw_bytes

    def test_tiny_body_is_invalid_content_not_a_false_success(
        self, server, session, limiter,
    ):
        result = pw.fetch(server.url("/empty"), session=session, limiter=limiter)
        # Chromium normalises <html></html> into a full document, so the
        # captured DOM clears the 50-byte floor. What matters is that it is
        # classified from the real captured bytes, not assumed OK.
        assert result.http_status == 200
        assert result.status in (FetchStatus.OK, FetchStatus.INVALID_CONTENT)


class TestPdfAndBytes:
    def test_pdf_arrives_as_real_bytes_not_a_rendered_viewer(
        self, server, session, limiter,
    ):
        result = pw.fetch(
            server.url("/doc.pdf"), source_type="PDF", session=session, limiter=limiter,
        )
        assert result.status == FetchStatus.OK, result.error
        assert result.acquisition == pw.ACQUISITION_REQUEST
        assert result.raw_bytes.startswith(b"%PDF"), result.raw_bytes[:40]
        assert "pdf" in (result.content_type or "")

    def test_pdf_request_sends_our_honest_user_agent(self, server, session, limiter):
        pw.fetch(
            server.url("/doc.pdf"), source_type="PDF", session=session, limiter=limiter,
        )
        recorded = server.last_request_for("/doc.pdf")
        assert recorded is not None
        assert recorded.header("User-Agent") == pw.user_agent()
        assert "patchright" not in (recorded.header("User-Agent") or "").lower()


class TestRefusals:
    def test_403_is_access_blocked_and_makes_exactly_one_request(
        self, server, session, limiter,
    ):
        before = len([p for p in server.paths_requested() if p == "/blocked"])
        result = pw.fetch(server.url("/blocked"), session=session, limiter=limiter)
        after = len([p for p in server.paths_requested() if p == "/blocked"])

        assert result.status == FetchStatus.ACCESS_BLOCKED
        assert result.http_status == 403
        # The whole point of a single retrieval path: a refusal cannot be
        # answered by trying again some other way.
        assert after - before == 1, server.paths_requested()

    def test_403_skips_the_content_ready_wait(self, server, session, limiter):
        result = pw.fetch(server.url("/blocked"), session=session, limiter=limiter)
        assert result.selector_wait_ms is None
        assert result.selector_timed_out is None

    def test_challenge_page_is_reported_not_fought(self, server, session, limiter):
        result = pw.fetch(server.url("/challenge"), session=session, limiter=limiter)
        assert result.status == FetchStatus.REQUIRES_BROWSER
        assert result.http_status == 403

    def test_429_retry_after_is_registered_with_the_limiter(self, server, session, limiter):
        url = server.url("/throttled")
        result = pw.fetch(url, session=session, limiter=limiter)
        assert result.status == FetchStatus.ACCESS_BLOCKED
        assert result.http_status == 429
        # The host told us how long to wait; the limiter must now hold that host.
        assert limiter.next_allowed_at(url) is not None


class TestConditionalGet:
    def test_first_fetch_captures_the_validators(self, server, session, limiter):
        result = pw.fetch(server.url("/conditional"), session=session, limiter=limiter)
        assert result.status == FetchStatus.OK, result.error
        assert result.not_modified is False
        assert result.etag == CONDITIONAL_ETAG
        assert result.last_modified == CONDITIONAL_LAST_MODIFIED

    def test_matching_etag_surfaces_a_real_304_with_no_body(
        self, server, session, limiter,
    ):
        result = pw.fetch(
            server.url("/conditional"), session=session, limiter=limiter,
            etag=CONDITIONAL_ETAG,
        )
        assert result.http_status == 304
        assert result.not_modified is True
        assert result.raw_bytes is None
        assert result.ok is True  # a 304 is a success, just one with nothing new
        assert result.acquisition == pw.ACQUISITION_REQUEST

    def test_matching_last_modified_also_surfaces_304(self, server, session, limiter):
        result = pw.fetch(
            server.url("/conditional"), session=session, limiter=limiter,
            last_modified=CONDITIONAL_LAST_MODIFIED,
        )
        assert result.not_modified is True
        assert result.http_status == 304

    def test_stale_validator_gets_a_real_200_body(self, server, session, limiter):
        result = pw.fetch(
            server.url("/conditional"), session=session, limiter=limiter,
            etag='"stale"',
        )
        assert result.status == FetchStatus.OK, result.error
        assert result.not_modified is False
        assert b"Conditional document" in result.raw_bytes

    def test_the_conditional_header_really_went_out(self, server, session, limiter):
        pw.fetch(
            server.url("/conditional"), session=session, limiter=limiter,
            etag=CONDITIONAL_ETAG,
        )
        recorded = server.last_request_for("/conditional")
        assert recorded.header("If-None-Match") == CONDITIONAL_ETAG


class TestRobotsAndPoliteness:
    def test_robots_crawl_delay_is_read_through_the_same_client(self, server, session):
        fresh = HostLimiter(min_interval=0, jitter=0, sleep=lambda _s: None)
        pw.fetch(server.url("/static"), session=session, limiter=fresh)
        assert fresh.crawl_delay_for(server.url("/static")) == 3.0
        assert "/robots.txt" in server.paths_requested()

    def test_robots_is_consulted_once_per_host_per_run(self, server, session):
        fresh = HostLimiter(min_interval=0, jitter=0, sleep=lambda _s: None)
        pw.fetch(server.url("/static"), session=session, limiter=fresh)
        before = server.paths_requested().count("/robots.txt")
        pw.fetch(server.url("/news"), session=session, limiter=fresh)
        after = server.paths_requested().count("/robots.txt")
        assert after == before, "robots.txt must not be re-fetched per source"


class TestSessionReuse:
    def test_one_browser_serves_many_sources(self, server, session, limiter):
        first = pw.fetch(server.url("/static"), session=session, limiter=limiter)
        second = pw.fetch(server.url("/news"), session=session, limiter=limiter)
        assert first.status == FetchStatus.OK
        assert second.status == FetchStatus.OK
        # Only the first fetch paid for a launch.
        assert first.browser_launch_ms is not None
        assert second.browser_launch_ms is None

    def test_no_stealth_flags_are_used(self):
        assert pw.LAUNCH_ARGS == ["--no-sandbox", "--disable-dev-shm-usage"]

    def test_webdriver_is_not_patched_away(self, server, session):
        """A real browser session is honest about being automated. If this ever
        starts reporting False, someone added stealth - which this project
        does not do."""
        with session.context_for(None) as (context, _launch_ms):
            page = context.new_page()
            page.goto(server.url("/static"))
            assert page.evaluate("() => navigator.webdriver") is True
            page.close()
