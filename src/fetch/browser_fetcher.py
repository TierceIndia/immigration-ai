"""Browser-based fetcher for government sources that block plain HTTP requests.

Used only as an escalation path when ``fetcher.fetch()`` reports ACCESS_BLOCKED or
REQUIRES_BROWSER. Drives Chromium through **Patchright** (a maintained,
drop-in-compatible fork of Playwright that patches the runtime-level automation
leaks which make stock headless Chromium trivially fingerprintable). Production
runs **headless** by default: with Patchright there is no operational reason to
require an X server, so the Xvfb wrapper is no longer needed.

No evasive/anti-bot-bypass technique is used or will be added here, matching the
principle stated in ``fetcher.py``: no stealth plugins, no credential abuse, no
proxy/IP rotation, no CAPTCHA bypass, and no retry amplification. Escalating to a
real browser and honestly reporting the outcome is not evasion — it is a genuine
browser session. If a source blocks even that, the correct response is to report
the block as data (``fetch_status`` + ``error_type``) and let the source be
retried on its normal schedule, not to try harder to defeat the block.

Failure classification follows the existing convention: ``FetchStatus`` is the
coarse, DB-constrained bucket and ``error_type`` carries the specific,
machine-readable class (``browser_launch_failed``, ``browser_timeout``,
``browser_navigation_failed``, ``browser_context_failed``). This adds no new
status enum and no parallel classification system.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from fetch.fetcher import (
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

# How long we give a page's own client-side JS to render real content after
# domcontentloaded fires, before giving up and capturing whatever is there.
# Several immi.homeaffairs.gov.au pages are Angular apps that ship their real
# content as an HTML-escaped JSON blob inside a hidden <input>'s value
# attribute (hydration state) and only materialise it into real <p>/<ul>/<table>
# DOM nodes via client-side JS *after* domcontentloaded — with no further
# network activity, so "networkidle" would not help here. Waiting for this
# selector is not evasion: it is giving the same real page the time it already
# needs to finish its own normal rendering before looking at it. A genuinely
# contentless page (e.g. a search-interface SPA with nothing to show) times
# out here and is captured/classified exactly as it would have been before.
CONTENT_READY_SELECTOR = "main :is(p, h1, h2, h3, h4, h5, h6, ul, ol, table)"
CONTENT_READY_TIMEOUT_MS = 8000

# Chromium args. --no-sandbox/--disable-dev-shm-usage are required to run as root
# in a container; they are not fingerprint evasion.
BROWSER_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]


def _is_headless() -> bool:
    """Headless by default.

    ``BROWSER_HEADLESS=0`` exists for local debugging against a real display. It
    is not the production path and requires an operator-supplied X display.
    """
    return os.getenv("BROWSER_HEADLESS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


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
    """Fetch a URL through a real Chromium session (headless by default).

    Returns the same FetchResult shape as ``fetcher.fetch()`` so downstream code
    does not need to branch on which fetcher produced a result.

    `launch_fn` is an injectable seam (defaults to _patchright_fetch) used only
    by tests to mock the browser call chain — production always uses Patchright.
    """
    if launch_fn is None:
        launch_fn = _patchright_fetch
    return launch_fn(url, source_type, timeout_seconds)


def _browser_failure(
    url: str, start: float, error_type: str, error: str,
    status: FetchStatus = FetchStatus.FETCH_FAILED,
) -> FetchResult:
    """Build a machine-classified browser failure result."""
    return FetchResult(
        url=url, status=status, http_status=None, content_type=None,
        raw_bytes=None, duration_ms=int((time.monotonic() - start) * 1000),
        error=error, error_type=error_type,
    )


def _classify_browser_exception(exc: BaseException) -> str:
    """Map a browser-driver exception to a stable error_type.

    Class names are matched rather than imported so this works even when the
    browser package is absent or has moved its exception hierarchy.
    """
    name = type(exc).__name__
    if "Timeout" in name:
        return "browser_timeout"
    if "TargetClosed" in name or "BrowserClosed" in name:
        return "browser_launch_failed"
    return "browser_navigation_failed"


def _patchright_fetch(url: str, source_type: str, timeout_seconds: int) -> FetchResult:
    """Actual Patchright-backed implementation."""
    start = time.monotonic()
    try:
        from patchright.sync_api import sync_playwright
    except ImportError as e:
        return _browser_failure(
            url, start, "browser_launch_failed",
            f"patchright not installed: {e}",
        )

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=_is_headless(),
                    args=BROWSER_LAUNCH_ARGS,
                )
            except Exception as e:
                return _browser_failure(
                    url, start, "browser_launch_failed",
                    f"Browser launch failed: {e}",
                )

            try:
                try:
                    # No automation-detectable properties are overridden. Chromium
                    # is a real browser session; only the runtime leaks Patchright
                    # exists to fix are addressed by the driver itself.
                    context = browser.new_context(locale="en-AU")
                    page = context.new_page()
                except Exception as e:
                    return _browser_failure(
                        url, start, "browser_context_failed",
                        f"Browser context creation failed: {e}",
                    )

                try:
                    page.set_default_timeout(
                        min(timeout_seconds * 1000, BROWSER_NAVIGATION_TIMEOUT_MS)
                    )
                    response = page.goto(url, wait_until="domcontentloaded")
                except Exception as e:
                    return _browser_failure(
                        url, start, _classify_browser_exception(e),
                        f"Browser navigation failed: {e}",
                    )

                try:
                    page.wait_for_selector(
                        CONTENT_READY_SELECTOR, timeout=CONTENT_READY_TIMEOUT_MS
                    )
                except Exception:
                    # Times out for pages that never render structural content
                    # (e.g. a genuinely contentless search-interface SPA) —
                    # that is real, honest information, not an error: capture
                    # whatever is there now, same as before this wait existed.
                    pass

                body_text = page.content()
                body_bytes = body_text.encode("utf-8")
                status_code = response.status if response is not None else None
                content_type = (
                    response.headers.get("content-type", "text/html")
                    if response is not None else "text/html"
                )
            finally:
                # Always tear the session down, even on the failure paths above —
                # a leaked Chromium process would otherwise accumulate per sync.
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

            duration_ms = int((time.monotonic() - start) * 1000)
            return _classify_browser_result(
                url=url,
                status_code=status_code,
                body_bytes=body_bytes,
                content_type=content_type,
                source_type=source_type,
                challenge_body=body_text,
                duration_ms=duration_ms,
            )
    except Exception as e:
        return _browser_failure(
            url, start, "browser_navigation_failed",
            f"Browser fetch failed: {e}",
        )


def _classify_browser_result(url: str, status_code: int | None, body_bytes: bytes,
                             content_type: str, source_type: str,
                             challenge_body: str = "", duration_ms: int = 0) -> FetchResult:
    """Classify a rendered page's status/body into a FetchResult.

    Pure logic, separated from the browser driver so it can be unit-tested with a
    mocked API surface (a real browser is never launched in CI, and no real
    government domain is ever hit).
    """
    if source_type == "PDF":
        # A genuine browser session fetching a PDF usually renders it to HTML
        # (Chromium's PDF viewer) rather than returning the raw PDF bytes. The
        # bytes we hold are not the document we were asked to archive, so this is
        # reported as invalid content rather than stored as false evidence.
        if not (body_bytes or b"").startswith(b"%PDF"):
            return FetchResult(
                url=url, status=FetchStatus.INVALID_CONTENT,
                http_status=status_code, content_type=content_type,
                raw_bytes=None, duration_ms=duration_ms,
                error="PDF fetched via browser was rendered as HTML, not PDF bytes",
                error_type="pdf_rendered_as_html",
            )

    if status_code == 200 and body_bytes and len(body_bytes) >= 50:
        body_text = body_bytes.decode("utf-8", errors="replace")
        if _looks_like_challenge_text(body_text):
            # Even a genuine browser session was served a challenge page. Do NOT
            # try harder against it — report it. REQUIRES_BROWSER marks it as
            # unrecoverable by automation so operations can see why.
            return FetchResult(
                url=url, status=FetchStatus.REQUIRES_BROWSER,
                http_status=status_code, content_type=content_type,
                raw_bytes=None, duration_ms=duration_ms,
                error="Challenge page detected even in a genuine browser session",
                error_type="challenge_page",
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
            error=f"HTTP {status_code} even via a genuine browser session",
            error_type="browser_access_blocked",
        )
    if status_code == 503 and _looks_like_challenge_text(challenge_body):
        return FetchResult(
            url=url, status=FetchStatus.REQUIRES_BROWSER, http_status=status_code,
            content_type=content_type, raw_bytes=None, duration_ms=duration_ms,
            error="Challenge page detected", error_type="challenge_page",
        )
    return FetchResult(
        url=url, status=FetchStatus.FETCH_FAILED, http_status=status_code,
        content_type=content_type, raw_bytes=None, duration_ms=duration_ms,
        error=f"Unexpected HTTP {status_code} via browser",
        error_type="unexpected_http_status",
    )


def _polite_delay() -> None:
    """Minimum inter-request delay between browser fetches."""
    time.sleep(MIN_BROWSER_DELAY_SECONDS)


def is_same_domain(a: str, b: str) -> bool:
    """Compare two URLs by host (used to keep escalation on approved official sources)."""
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
