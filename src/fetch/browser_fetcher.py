"""Browser-based fetcher for government sources that block plain HTTP requests.

Used only as an escalation path when ``fetcher.fetch()`` reports ACCESS_BLOCKED or
REQUIRES_BROWSER. Drives Chromium through **Patchright** (a maintained,
drop-in-compatible fork of Playwright that patches the runtime-level automation
leaks which make stock headless Chromium trivially fingerprintable). Production
runs **headless** by default: with Patchright there is no operational reason to
require an X server, so the Xvfb wrapper is no longer needed.

No evasive/anti-bot-bypass technique is used or will be added here, matching the
principle stated in ``fetcher.py``: no stealth plugins, no credential abuse, no
proxy/IP rotation, no CAPTCHA bypass, and no retry amplification. The single
static egress proxy from ``FETCH_PROXY_URL`` (see ``fetch/proxy.py``) is honoured
here too, for reachability â€” it is one fixed value, never rotated. Escalating to a
real browser and honestly reporting the outcome is not evasion â€” it is a genuine
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
from fetch.proxy import ProxyConfigError, browser_proxy

# Spacing/jitter between browser fetches is owned by fetch.politeness.HostLimiter
# (shared with the plain-HTTP path), not by a constant here. This module used to
# define a MIN_BROWSER_DELAY_SECONDS + _polite_delay() pair that nothing ever
# called â€” dead code the tests never caught. It is gone; every browser fetch now
# goes through the same limiter wait as every plain fetch.

# How long we let an individual page load before giving up.
BROWSER_NAVIGATION_TIMEOUT_MS = 30000

# How long we give a page's own client-side JS to render real content after
# domcontentloaded fires, before giving up and capturing whatever is there.
# Several immi.homeaffairs.gov.au pages are Angular apps that ship their real
# content as an HTML-escaped JSON blob inside a hidden <input>'s value
# attribute (hydration state) and only materialise it into real <p>/<ul>/<table>
# DOM nodes via client-side JS *after* domcontentloaded â€” with no further
# network activity, so "networkidle" would not help here. Waiting for this
# selector is not evasion: it is giving the same real page the time it already
# needs to finish its own normal rendering before looking at it. A genuinely
# contentless page (e.g. a search-interface SPA with nothing to show) times
# out here and is captured/classified exactly as it would have been before.
# Must track extractor_html.py's _find_main_content() fallback chain (main,
# [role=main], #content, #main-content, [class*=content]) â€” a page using one
# of the non-<main> fallback containers would otherwise always burn the full
# CONTENT_READY_TIMEOUT_MS even though extraction would succeed once captured.
CONTENT_READY_SELECTOR = (
    ":is(main, [role=main], #content, #main-content, [class*=content]) "
    ":is(p, h1, h2, h3, h4, h5, h6, ul, ol, table)"
)
CONTENT_READY_TIMEOUT_MS = 8000

# Chromium args. --no-sandbox/--disable-dev-shm-usage are container
# compatibility requirements; they are not fingerprint evasion. The API image
# runs as an unprivileged user, so these do not also mean running as root.
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
    limiter=None,
    browser_session: "BrowserSession | None" = None,
) -> FetchResult:
    """Fetch a URL through a real Chromium session (headless by default).

    Returns the same FetchResult shape as ``fetcher.fetch()`` so downstream code
    does not need to branch on which fetcher produced a result.

    `launch_fn` is an injectable seam (defaults to _patchright_fetch) used only
    by tests to mock the browser call chain â€” production always uses Patchright.
    `limiter` is likewise injectable; production uses the process-wide one.
    `browser_session` (item 4) lets a whole sync run share one real Chromium
    process across many sources instead of launching a fresh one per source;
    it is only honoured on the real ``_patchright_fetch`` path â€” a custom
    ``launch_fn`` (always a test seam) keeps its existing 3-arg call shape.

    The limiter wait happens *here*, before dispatching to ``launch_fn``, so
    every browser fetch is spaced by the same per-host rule as a plain fetch â€”
    including the ones a test drives through a fake ``launch_fn``.
    """
    from fetch.politeness import get_limiter

    if limiter is None:
        limiter = get_limiter()
    limiter_wait_ms = int(limiter.wait(url) * 1000)
    if launch_fn is None:
        result = _patchright_fetch(url, source_type, timeout_seconds, browser_session=browser_session)
    else:
        result = launch_fn(url, source_type, timeout_seconds)
    # launch_fn is a test seam and may return a bare sentinel rather than a
    # real FetchResult (e.g. to prove call plumbing) - only stamp the timing
    # when there is somewhere real to put it.
    if hasattr(result, "limiter_wait_ms"):
        result.limiter_wait_ms = limiter_wait_ms
    return result


class BrowserSession:
    """One real Chromium process, shared across many ``fetch_with_browser()``
    calls in a single sync run (item 4).

    A browser launch is slow (roughly a second) compared to navigating a page
    in an already-running one. Re-launching a fresh browser for every source
    that escalates in one sync run was pure waste when the whole run is going
    to tear it down again a few seconds later anyway. Each source still gets
    its own fresh *context* (cookies/storage never leak between sources) -
    only the underlying browser process is reused.

    Lazy: nothing is launched until the first ``ensure_browser()`` call, so a
    sync run that never escalates never pays for a browser at all. Always
    call ``close()`` (or use as a context manager) once the run is done.
    """

    def __init__(self):
        self._cm = None
        self.browser = None

    def ensure_browser(self):
        """Return the running browser, launching it on first use.

        Returns ``(browser, launch_ms)`` - ``launch_ms`` is the real launch
        time on the call that actually launched it, and ``None`` on every
        later call that just reused the already-running process.
        """
        if self.browser is not None:
            return self.browser, None
        from patchright.sync_api import sync_playwright

        self._cm = sync_playwright()
        playwright = self._cm.__enter__()
        launch_start = time.monotonic()
        self.browser = playwright.chromium.launch(
            headless=_is_headless(), args=BROWSER_LAUNCH_ARGS,
        )
        return self.browser, int((time.monotonic() - launch_start) * 1000)

    def close(self) -> None:
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
            self._cm = None

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False


def _browser_failure(
    url: str, start: float, error_type: str, error: str,
    status: FetchStatus = FetchStatus.FETCH_FAILED,
    browser_launch_ms: int | None = None,
    goto_ms: int | None = None,
) -> FetchResult:
    """Build a machine-classified browser failure result."""
    return FetchResult(
        url=url, status=status, http_status=None, content_type=None,
        raw_bytes=None, duration_ms=int((time.monotonic() - start) * 1000),
        error=error, error_type=error_type,
        browser_launch_ms=browser_launch_ms, goto_ms=goto_ms,
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


# Resource types that never contribute to extraction (extraction reads the
# rendered DOM/text, never binary assets) but can be a large share of a
# page's load time. Scripts, stylesheets and XHR are deliberately kept: pages
# like immi.homeaffairs.gov.au's Angular apps render their real content via
# client-side JS and XHR-fetched data (see CONTENT_READY_SELECTOR's
# docstring), so blocking those would break real extraction, not just speed.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media"})


def _route_blocking_heavy_assets(route) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


def _fetch_page(
    browser, url: str, source_type: str, timeout_seconds: int,
    proxy, start: float, browser_launch_ms: int | None, close_browser: bool,
) -> FetchResult:
    """Navigate one page in ``browser`` and classify the result.

    Shared by the per-call launch path (``close_browser=True``, the browser
    this function was handed is closed when done) and the shared
    ``BrowserSession`` path (``close_browser=False`` â€” the session owns the
    browser's lifetime across the whole sync run, so only the context this
    one page used is closed here).
    """
    context = None
    goto_ms: int | None = None
    try:
        try:
            # No automation-detectable properties are overridden. Chromium
            # is a real browser session; only the runtime leaks Patchright
            # exists to fix are addressed by the driver itself.
            context = browser.new_context(locale="en-AU", proxy=proxy)
            page = context.new_page()
            page.route("**/*", _route_blocking_heavy_assets)
        except Exception as e:
            return _browser_failure(
                url, start, "browser_context_failed",
                f"Browser context creation failed: {e}",
                browser_launch_ms=browser_launch_ms,
            )

        try:
            page.set_default_timeout(
                min(timeout_seconds * 1000, BROWSER_NAVIGATION_TIMEOUT_MS)
            )
            goto_start = time.monotonic()
            response = page.goto(url, wait_until="domcontentloaded")
            goto_ms = int((time.monotonic() - goto_start) * 1000)
        except Exception as e:
            return _browser_failure(
                url, start, _classify_browser_exception(e),
                f"Browser navigation failed: {e}",
                browser_launch_ms=browser_launch_ms,
                goto_ms=int((time.monotonic() - goto_start) * 1000),
            )

        status_code = response.status if response is not None else None
        content_type = (
            response.headers.get("content-type", "text/html")
            if response is not None else "text/html"
        )

        selector_wait_ms = None
        selector_timed_out = None
        # Item 3: an HTTP >= 400 response (a block/error page) is never going
        # to grow the CONTENT_READY_SELECTOR's structural content through
        # client-side JS - waiting the full CONTENT_READY_TIMEOUT_MS here
        # just burns time on a page we are going to classify as a failure
        # anyway.
        if status_code is None or status_code < 400:
            selector_start = time.monotonic()
            selector_timed_out = False
            try:
                page.wait_for_selector(
                    CONTENT_READY_SELECTOR, timeout=CONTENT_READY_TIMEOUT_MS
                )
            except Exception:
                # Times out for pages that never render structural content
                # (e.g. a genuinely contentless search-interface SPA) â€”
                # that is real, honest information, not an error: capture
                # whatever is there now, same as before this wait existed.
                selector_timed_out = True
            selector_wait_ms = int((time.monotonic() - selector_start) * 1000)

        body_text = page.content()
        body_bytes = body_text.encode("utf-8")
    finally:
        # Always close the context, even on the failure paths above â€” a
        # leaked context would otherwise accumulate per source. The browser
        # itself is only closed here in per-call mode; a BrowserSession owns
        # closing its own browser once, at the end of the whole sync run.
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if close_browser and browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    duration_ms = int((time.monotonic() - start) * 1000)
    result = _classify_browser_result(
        url=url,
        status_code=status_code,
        body_bytes=body_bytes,
        content_type=content_type,
        source_type=source_type,
        challenge_body=body_text,
        duration_ms=duration_ms,
    )
    result.browser_launch_ms = browser_launch_ms
    result.goto_ms = goto_ms
    result.selector_wait_ms = selector_wait_ms
    result.selector_timed_out = selector_timed_out
    return result


def _patchright_fetch(
    url: str, source_type: str, timeout_seconds: int,
    browser_session: "BrowserSession | None" = None,
) -> FetchResult:
    """Actual Patchright-backed implementation.

    With no ``browser_session``, launches and tears down one browser per
    call (the original behaviour). With one, reuses its already-running
    browser (item 4) â€” only a fresh context is opened/closed per call.
    """
    start = time.monotonic()

    # Resolve the optional static egress proxy before launching anything. A
    # malformed FETCH_PROXY_URL must fail closed here too: launching Chromium
    # directly would be exactly the silent fallback the proxy setting exists to
    # prevent. Both retrieval paths therefore share one proxy decision.
    try:
        proxy = browser_proxy()
    except ProxyConfigError as exc:
        return _browser_failure(
            url, start, "proxy_config_error", str(exc),
            status=FetchStatus.NETWORK_RESTRICTED,
        )

    if browser_session is not None:
        try:
            browser, browser_launch_ms = browser_session.ensure_browser()
        except Exception as e:
            return _browser_failure(
                url, start, "browser_launch_failed",
                f"Browser launch failed: {e}",
            )
        try:
            return _fetch_page(
                browser, url, source_type, timeout_seconds, proxy, start,
                browser_launch_ms, close_browser=False,
            )
        except Exception as e:
            return _browser_failure(
                url, start, _classify_browser_exception(e),
                f"Browser fetch failed: {e}",
            )

    try:
        from patchright.sync_api import sync_playwright
    except ImportError as e:
        return _browser_failure(
            url, start, "browser_launch_failed",
            f"patchright not installed: {e}",
        )

    try:
        with sync_playwright() as p:
            launch_start = time.monotonic()
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
            browser_launch_ms = int((time.monotonic() - launch_start) * 1000)
            return _fetch_page(
                browser, url, source_type, timeout_seconds, proxy, start,
                browser_launch_ms, close_browser=True,
            )
    except Exception as e:
        return _browser_failure(
            url, start, _classify_browser_exception(e),
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
            # try harder against it â€” report it. REQUIRES_BROWSER marks it as
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

    # Non-200 (or empty) via browser â€” classify honestly.
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


def is_same_domain(a: str, b: str) -> bool:
    """Compare two URLs by host (used to keep escalation on approved official sources)."""
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
