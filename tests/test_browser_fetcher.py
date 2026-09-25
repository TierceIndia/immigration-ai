"""Unit tests for browser_fetcher classification/escalation logic.

The Playwright call chain is never launched for real in CI and no real government
domain is ever hit — we test the pure classification logic (_classify_browser_result)
and the playwright-missing error path.
"""
from __future__ import annotations

import builtins

import pytest

from browser_fetcher import (
    _classify_browser_result,
    _looks_like_challenge_text,
    _playwright_fetch,
    is_same_domain,
)
from fetcher import FetchStatus


def test_is_same_domain():
    assert is_same_domain(
        "https://immi.homeaffairs.gov.au/visas",
        "https://immi.homeaffairs.gov.au/visas/482",
    )
    assert not is_same_domain(
        "https://immi.homeaffairs.gov.au/visas",
        "https://legislation.gov.au/foo",
    )


def test_challenge_marker_detection():
    assert _looks_like_challenge_text("Checking your browser before accessing")
    assert _looks_like_challenge_text("Access denied by Cloudflare")
    assert not _looks_like_challenge_text("A real policy page about skilled visas")


def test_classify_ok_html():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/page",
        status_code=200,
        body_bytes=b"<html><body>visa 482 skilled worker requirements</body></html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.OK
    assert res.raw_bytes is not None


def test_classify_access_blocked_403():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/page",
        status_code=403,
        body_bytes=b"<html>access denied</html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.ACCESS_BLOCKED
    assert res.raw_bytes is None


def test_classify_challenge_page_even_in_browser():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/page",
        status_code=200,
        body_bytes=b"<html>Enable javascript and checking your browser...</html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.REQUIRES_BROWSER
    assert res.raw_bytes is None


def test_classify_pdf_rendered_as_html_requires_manual_download():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/doc",
        status_code=200,
        body_bytes=b"<html>PDF viewer rendering</html>",
        content_type="text/html",
        source_type="PDF",
    )
    assert res.status == FetchStatus.REQUIRES_MANUAL_DOWNLOAD


def test_classify_pdf_ok():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/doc",
        status_code=200,
        body_bytes=b"%PDF-1.4 %some fake pdf body content here for test",
        content_type="application/pdf",
        source_type="PDF",
    )
    assert res.status == FetchStatus.OK


def test_classify_unexpected_status_fetch_failed():
    res = _classify_browser_result(
        url="https://example.gov/page",
        status_code=500,
        body_bytes=b"<html>server error</html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.FETCH_FAILED


def test_classify_503_challenge():
    res = _classify_browser_result(
        url="https://example.gov/page",
        status_code=503,
        body_bytes=b"<html>unavailable</html>",
        content_type="text/html",
        source_type="HTML",
        challenge_body="Checking your browser before accessing",
    )
    assert res.status == FetchStatus.REQUIRES_BROWSER


def test_playwright_missing_returns_fetch_failed(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    res = _playwright_fetch("https://example.gov/page", "HTML", 20)
    assert res.status == FetchStatus.FETCH_FAILED
    assert "playwright not installed" in (res.error or "")


def test_chromium_launches_with_headless_false(monkeypatch):
    """Production must launch Chromium headed (headless=False) — headless mode
    is fingerprinted and blocked (HTTP 403) by the Home Affairs WAF. This test
    never launches a real browser: it mocks the Playwright call chain and
    inspects the exact kwargs passed to chromium.launch()."""
    import playwright.sync_api as pw_sync_api

    launch_calls = []

    class _Resp:
        status = 200
        headers = {"content-type": "text/html"}

    class _Page:
        def set_default_timeout(self, ms):
            pass

        def goto(self, url, wait_until=None):
            return _Resp()

        def content(self):
            return "<html><body>" + ("OK " * 20) + "</body></html>"

    class _Context:
        def new_page(self):
            return _Page()

        def close(self):
            pass

    class _Browser:
        def new_context(self, **kwargs):
            return _Context()

        def close(self):
            pass

    class _Chromium:
        def launch(self, **kwargs):
            launch_calls.append(kwargs)
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

    class _SyncPlaywrightCM:
        def __enter__(self):
            return _Playwright()

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(pw_sync_api, "sync_playwright", lambda: _SyncPlaywrightCM())

    result = _playwright_fetch("https://immi.homeaffairs.gov.au/page", "HTML", 20)

    assert len(launch_calls) == 1
    assert launch_calls[0]["headless"] is False
    assert result.status == FetchStatus.OK
