"""Browser-based fetcher for government sources that block plain HTTP requests.

Used only as an escalation path when fetcher.fetch() reports ACCESS_BLOCKED or
REQUIRES_BROWSER. Drives Chromium in headless mode via Playwright so it runs in
the production Docker container without an X server.

No evasive/anti-bot-bypass technique is used or will be added here, matching the
same principle stated in fetcher.py: no stealth plugins, no automation-signal
patching, no proxy/IP rotation, no CAPTCHA bypass. If a source blocks or challenges
even this browser session, the correct response is to report
NEEDS_MANUAL_CAPTURE, not to try harder to defeat the block.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from fetcher import (
    DEFAULT_TIMEOUT_SECONDS,
    FetchResult,
    FetchStatus,
    _CHALLENGE_MARKERS,
)

# A modest minimum inter-request delay so a genuine browser session still behaves
# politely and does not hammer a government origin.
MIN_BROWSER_DELAY_SECONDS = 2.0

# How long we let an individual page load before giving up.
BROWSER_NAVIGATION_TIMEOUT_MS = 30000


def _looks_like_challenge_text(body: str) -> bool:
    """Detect challenge markers in rendered page text (honest reporting, not bypass)."""
    sample = (body or "")[:2000].lower()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


def fetch_with_browser(
    url: str,
    source_type: str = "HTML",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    launch_fn=None,
) -> FetchResult:
    """Fetch a URL through a headless Chromium session.

    Returns the same FetchResult shape as fetcher.fetch() so downstream code does
    not need to branch on which fetcher produced a result.

    `launch_fn` is an injectable seam (defaults to _playwright_fetch) used only by
    tests to mock the Playwright call chain — production always uses Playwright.
    """
    if launch_fn is None:
        launch_fn = _playwright_fetch
    return launch_fn(url, source_type, timeout_seconds)


def _playwright_fetch(url: str, source_type: str, timeout_seconds: int) -> FetchResult:
    """Actual Playwright-backed implementation."""
    start = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return FetchResult(
            url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
            content_type=None, raw_bytes=None,
            duration_ms=int((time.monotonic() - start) * 1000),
            error=f"playwright not installed: {e}",
        )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = browser.new_context(
                    # No automation-detectable properties are overridden. Chromium is
                    # launched in headless mode with no stealth
                    # patching.
                    locale="en-AU",
                )
                page = context.new_page()
                page.set_default_timeout(
                    min(timeout_seconds * 1000, BROWSER_NAVIGATION_TIMEOUT_MS)
                )
                response = page.goto(url, wait_until="domcontentloaded")
                body_bytes = page.content().encode("utf-8")
                status_code = response.status if response is not None else None
                content_type = (
                    response.headers.get("content-type", "text/html")
                    if response is not None else "text/html"
                )
                challenge_body = page.content()  # reused by 503 challenge detection
            finally:
                context.close()
                browser.close()

            duration_ms = int((time.monotonic() - start) * 1000)
            return _classify_browser_result(
                url=url,
                status_code=status_code,
                body_bytes=body_bytes,
                content_type=content_type,
                source_type=source_type,
                challenge_body=challenge_body,
                duration_ms=duration_ms,
            )
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return FetchResult(
            url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
            content_type=None, raw_bytes=None, duration_ms=duration_ms,
            error=f"Playwright browser fetch failed: {e}",
        )


def _classify_browser_result(url: str, status_code: int | None, body_bytes: bytes,
                             content_type: str, source_type: str,
                             challenge_body: str = "", duration_ms: int = 0) -> FetchResult:
    """Classify a rendered page's status/body into a FetchResult.

    Pure logic, separated from Playwright driving so it can be unit-tested with a
    mocked Playwright API surface (a real browser is never launched in CI, and no
    real government domain is ever hit).
    """
    if source_type == "PDF":
        # A genuine browser session fetching a PDF usually renders it to HTML
        # (Chromium's PDF viewer) rather than returning the raw PDF bytes. If that
        # happens, surface it honestly as REQUIRES_MANUAL_DOWNLOAD so a manual PDF
        # capture path can obtain the true binary.
        if not (body_bytes or b"").startswith(b"%PDF"):
            return FetchResult(
                url=url, status=FetchStatus.REQUIRES_MANUAL_DOWNLOAD,
                http_status=status_code, content_type=content_type,
                raw_bytes=None, duration_ms=duration_ms,
                error="PDF fetched via browser rendered as HTML; manual PDF capture required",
            )

    if status_code == 200 and body_bytes and len(body_bytes) >= 50:
        body_text = body_bytes.decode("utf-8", errors="replace")
        if _looks_like_challenge_text(body_text):
            # Even a genuine browser session was served a challenge
            # page. Do NOT try harder against it; route to manual capture downstream
            # (NEEDS_MANUAL_CAPTURE). We surface REQUIRES_BROWSER so the orchestrator
            # treats it as unrecoverable by automation and escalates.
            return FetchResult(
                url=url, status=FetchStatus.REQUIRES_BROWSER,
                http_status=status_code, content_type=content_type,
                raw_bytes=None, duration_ms=duration_ms,
                error="Challenge page detected even in genuine browser session; manual capture required",
            )
        return FetchResult(
            url=url, status=FetchStatus.OK, http_status=status_code,
            content_type=content_type, raw_bytes=body_bytes,
            duration_ms=duration_ms, error=None,
        )

    # Non-200 (or empty) via browser — classify honestly.
    if status_code in (401, 403, 429):
        return FetchResult(
            url=url, status=FetchStatus.ACCESS_BLOCKED, http_status=status_code,
            content_type=content_type, raw_bytes=None, duration_ms=duration_ms,
            error=f"HTTP {status_code} even via genuine browser session",
        )
    if status_code == 503 and _looks_like_challenge_text(challenge_body):
        return FetchResult(
            url=url, status=FetchStatus.REQUIRES_BROWSER, http_status=status_code,
            content_type=content_type, raw_bytes=None, duration_ms=duration_ms,
            error="Challenge page detected",
        )
    return FetchResult(
        url=url, status=FetchStatus.FETCH_FAILED, http_status=status_code,
        content_type=content_type, raw_bytes=None, duration_ms=duration_ms,
        error=f"Unexpected HTTP {status_code} via browser",
    )


def _polite_delay() -> None:
    """Minimum inter-request delay between browser fetches."""
    time.sleep(MIN_BROWSER_DELAY_SECONDS)


def is_same_domain(a: str, b: str) -> bool:
    """Compare two URLs by host (used to keep escalation on approved official sources)."""
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
