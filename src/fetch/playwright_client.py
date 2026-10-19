"""Playwright-only retrieval for government sources.

This is the single retrieval path. There is no plain-``requests`` fetch and no
"escalate to a browser after a refusal" step any more: every request - HTML
page, PDF, robots.txt, conditional pre-check - leaves through one Playwright
browser, so there is structurally nothing to escalate *to*. That removes a
whole class of mistake the old two-path design kept inviting (see
fetch_document.py's history: ACCESS_BLOCKED used to trigger a second,
pointless browser attempt against a host that had just refused us).

Two mechanisms, both Playwright, recorded honestly in the snapshot manifest:

* ``playwright_page`` - a real navigation (``page.goto``) plus the
  content-ready wait, for HTML. Needed because several real sources ship
  their content as a hydration blob and only materialise it with client-side
  JS (see CONTENT_READY_SELECTOR).
* ``playwright_request`` - ``context.request.get`` (Playwright's own HTTP
  client, sharing the context's User-Agent, proxy and cookie jar), for PDFs
  and other non-HTML bodies, for robots.txt, and for conditional GET
  pre-checks. A PDF must arrive as bytes; navigating to one in Chromium hands
  back a rendered viewer, not the document we are supposed to archive.

Honesty rules this module exists to keep (see also fetch/proxy.py):

* ONE User-Agent per process, from ``FETCH_USER_AGENT``, never varied per
  request/host and never changed after a refusal.
* No stealth: no ``navigator.webdriver`` patching, no fingerprint evasion, no
  init scripts, no CDP tricks. Launch args are exactly
  ``--no-sandbox --disable-dev-shm-usage`` (container requirements, not
  evasion), headless, locale en-AU.
* No proxy pool. At most the one static ``FETCH_PROXY_URL``, fail-closed.
* A 403/429 records the host cooldown and stops. Nothing about *how* we fetch
  changes in response.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from urllib.parse import urlparse

from fetch.fetcher import (
    DEFAULT_TIMEOUT_SECONDS,
    FetchResult,
    FetchStatus,
    classify_response,
    honour_retry_after,
    user_agent,
    _parse_crawl_delay,
)
from fetch.politeness import HostLimiter, get_limiter
from fetch.proxy import ProxyConfigError, browser_proxy, redact
from logging_config import get_logger

logger = get_logger(__name__)

# Container hardening, not fingerprint evasion: Chromium cannot run as root in
# a container without --no-sandbox, and the default 64MB /dev/shm crashes it.
LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
LOCALE = "en-AU"

NAVIGATION_TIMEOUT_MS = 30000

# How long we let a page's own client-side JS render real content after
# domcontentloaded, before capturing whatever is there. Several
# immi.homeaffairs.gov.au pages are Angular apps that ship their content as an
# HTML-escaped JSON blob inside a hidden <input> and only materialise it into
# real DOM nodes after domcontentloaded, with no further network activity (so
# "networkidle" does not help). Waiting is not evasion: it gives the same real
# page the time it already needs to finish its own normal rendering. A
# genuinely contentless page times out here and is captured and classified
# exactly as it would have been without the wait.
#
# Must track extractor_html.py's _find_main_content() fallback chain, or a page
# using one of the non-<main> containers burns the full timeout for nothing.
CONTENT_READY_SELECTOR = (
    ":is(main, [role=main], #content, #main-content, [class*=content]) "
    ":is(p, h1, h2, h3, h4, h5, h6, ul, ol, table)"
)
CONTENT_READY_TIMEOUT_MS = 8000

ACQUISITION_PAGE = "playwright_page"
ACQUISITION_REQUEST = "playwright_request"

# Source types we fetch as bytes rather than navigating to. Everything else is
# treated as a page to render.
_BYTES_SOURCE_TYPES = {"PDF"}


def _headless() -> bool:
    """Headless by default.

    ``BROWSER_HEADLESS=0`` exists for local debugging against a real display;
    it is not the production path.
    """
    import os

    return (os.getenv("BROWSER_HEADLESS", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def classify_exception(exc: BaseException) -> str:
    """Map a Playwright/driver exception to a stable, machine-readable class.

    Class names are matched rather than imported so this keeps working when the
    driver is absent or moves its exception hierarchy.
    """
    name = type(exc).__name__
    text = str(exc).lower()
    if "Timeout" in name:
        return "browser_timeout"
    if "TargetClosed" in name or "BrowserClosed" in name:
        return "browser_launch_failed"
    if "net::err_name_not_resolved" in text:
        return "dns_resolution_failure"
    if "net::err_connection_refused" in text:
        return "connection_error"
    if "net::err_cert" in text or "ssl" in text:
        return "tls_error"
    if "net::err_proxy" in text:
        return "proxy_error"
    if "net::err_" in text:
        return "connection_error"
    return "browser_navigation_failed"


class PlaywrightSession:
    """One real browser for a whole sync run; a fresh context per source.

    Launching a browser costs about a second; navigating in an already-running
    one does not. Each source still gets its own context, so cookies and
    storage never leak between sources - only the browser process is shared.

    Lazy: nothing launches until the first fetch, so a run that fetches
    nothing never pays for a browser. ``close()`` (or use as a context
    manager) tears it down exactly once.
    """

    def __init__(self, launcher=None):
        # ``launcher`` is the injectable seam: a zero-arg callable returning a
        # Playwright-like object (something with .chromium.launch(...)) wrapped
        # in a context manager. Production passes None and gets real
        # Playwright; fast unit tests pass a fake.
        self._launcher = launcher
        self._cm = None
        self.browser = None

    def ensure_browser(self):
        """Return ``(browser, launch_ms)``; ``launch_ms`` is None on reuse."""
        if self.browser is not None:
            return self.browser, None
        if self._launcher is not None:
            self._cm = self._launcher()
        else:
            from playwright.sync_api import sync_playwright

            self._cm = sync_playwright()
        playwright = self._cm.__enter__()
        started = time.monotonic()
        self.browser = playwright.chromium.launch(headless=_headless(), args=LAUNCH_ARGS)
        return self.browser, int((time.monotonic() - started) * 1000)

    @contextmanager
    def context_for(self, proxy=None):
        """A fresh browser context for one source, always closed afterwards.

        No automation-detectable property is overridden here. The only things
        set are the honest User-Agent, the en-AU locale, and the operator's
        single static proxy when configured.
        """
        browser, launch_ms = self.ensure_browser()
        context = browser.new_context(
            locale=LOCALE, user_agent=user_agent(), proxy=proxy,
        )
        try:
            yield context, launch_ms
        finally:
            try:
                context.close()
            except Exception:  # a context we cannot close must not fail the fetch
                pass

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

    def __enter__(self) -> "PlaywrightSession":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False


def _conditional_headers(etag: str | None, last_modified: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


def _header(headers: dict | None, name: str) -> str | None:
    """Case-insensitive header lookup (Playwright lower-cases header names)."""
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    value = lowered.get(name.lower())
    return str(value) if value is not None else None


def _not_modified_result(
    url: str, status_code: int, headers: dict | None, started: float,
    limiter_wait_ms: int, etag: str | None, last_modified: str | None,
    acquisition: str,
) -> FetchResult:
    """A 304 is a success with no body: nothing to store, extract or re-hash."""
    duration_ms = int((time.monotonic() - started) * 1000)
    return FetchResult(
        url=url, status=FetchStatus.OK, http_status=status_code,
        content_type=_header(headers, "content-type"),
        raw_bytes=None, duration_ms=duration_ms, error=None,
        limiter_wait_ms=limiter_wait_ms, http_ms=duration_ms,
        not_modified=True,
        etag=_header(headers, "etag") or etag,
        last_modified=_header(headers, "last-modified") or last_modified,
        acquisition=acquisition,
    )


def _failure(
    url: str, started: float, error_type: str, error: str,
    status: FetchStatus = FetchStatus.FETCH_FAILED,
    limiter_wait_ms: int | None = None, browser_launch_ms: int | None = None,
    goto_ms: int | None = None, acquisition: str | None = None,
) -> FetchResult:
    return FetchResult(
        url=url, status=status, http_status=None, content_type=None, raw_bytes=None,
        duration_ms=int((time.monotonic() - started) * 1000), error=error,
        error_type=error_type, limiter_wait_ms=limiter_wait_ms,
        browser_launch_ms=browser_launch_ms, goto_ms=goto_ms, acquisition=acquisition,
    )


def honour_robots_crawl_delay(context, url: str, limiter: HostLimiter, timeout_ms: int) -> None:
    """Fetch a host's robots.txt once per run and register its Crawl-delay.

    Consulted at most once per host per limiter lifetime (the limiter owns that
    bit of state, so it is naturally scoped to one sync run). Any failure -
    network, absence, unparsable body - simply leaves the default spacing in
    force; a missing robots.txt is not an error. Goes through the same
    Playwright context as everything else, so it carries the same honest UA
    and the same single proxy.
    """
    if not limiter.mark_robots_checked(url):
        return
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = context.request.get(robots_url, timeout=timeout_ms)
        if response.status != 200:
            return
        body = response.text()
    except Exception:
        return
    if not body:
        return
    delay = _parse_crawl_delay(body, user_agent())
    if delay:
        limiter.note_crawl_delay(url, delay)


def _fetch_bytes(
    context, url: str, source_type: str, started: float, limiter: HostLimiter,
    limiter_wait_ms: int, browser_launch_ms: int | None, timeout_ms: int,
    etag: str | None, last_modified: str | None,
) -> FetchResult:
    """Fetch a non-HTML body (PDF etc.) as real bytes via Playwright's HTTP client."""
    request_started = time.monotonic()
    try:
        response = context.request.get(
            url, headers=_conditional_headers(etag, last_modified), timeout=timeout_ms,
        )
    except Exception as exc:
        return _failure(
            url, started, classify_exception(exc), f"Playwright request failed: {exc}",
            limiter_wait_ms=limiter_wait_ms, browser_launch_ms=browser_launch_ms,
            acquisition=ACQUISITION_REQUEST,
        )
    http_ms = int((time.monotonic() - request_started) * 1000)
    headers = dict(response.headers or {})
    honour_retry_after(response.status, _header(headers, "retry-after"), url, limiter)

    if response.status == 304:
        return _not_modified_result(
            url, 304, headers, started, limiter_wait_ms, etag, last_modified,
            ACQUISITION_REQUEST,
        )

    try:
        body = response.body()
    except Exception:
        body = b""

    status = classify_response(response.status, headers, body, source_type)
    error = None
    if status == FetchStatus.NETWORK_RESTRICTED:
        error = body[:300].decode("utf-8", errors="replace").strip()
    elif status != FetchStatus.OK:
        error = f"HTTP {response.status}"

    return FetchResult(
        url=response.url or url, status=status, http_status=response.status,
        content_type=_header(headers, "content-type"), raw_bytes=body,
        duration_ms=int((time.monotonic() - started) * 1000), error=error,
        limiter_wait_ms=limiter_wait_ms, http_ms=http_ms,
        browser_launch_ms=browser_launch_ms,
        etag=_header(headers, "etag"), last_modified=_header(headers, "last-modified"),
        acquisition=ACQUISITION_REQUEST,
    )


def _fetch_page(
    context, url: str, source_type: str, started: float, limiter: HostLimiter,
    limiter_wait_ms: int, browser_launch_ms: int | None, timeout_ms: int,
    etag: str | None, last_modified: str | None,
) -> FetchResult:
    """Navigate to an HTML page, let it render, and capture the real DOM.

    Conditional GET happens *before* navigation, through
    ``context.request.get``: Chromium services a 304 from its own cache and
    reports 200 to ``page.goto``, so a navigation can never surface "not
    modified" reliably. A real pre-check can, and when it answers 304 we skip
    the navigation entirely - no render, no body, nothing downstream.
    """
    if etag or last_modified:
        try:
            probe = context.request.get(
                url, headers=_conditional_headers(etag, last_modified), timeout=timeout_ms,
            )
            probe_headers = dict(probe.headers or {})
            honour_retry_after(probe.status, _header(probe_headers, "retry-after"), url, limiter)
            if probe.status == 304:
                return _not_modified_result(
                    url, 304, probe_headers, started, limiter_wait_ms,
                    etag, last_modified, ACQUISITION_REQUEST,
                )
        except Exception:
            # A failed pre-check is not a failed fetch: fall through and
            # navigate normally. The worst case is that we re-download a body
            # we already had, which is exactly the pre-v3 behaviour.
            pass

    page = context.new_page()
    goto_ms: int | None = None
    selector_wait_ms: int | None = None
    selector_timed_out: bool | None = None
    try:
        page.set_default_timeout(min(timeout_ms, NAVIGATION_TIMEOUT_MS))
        goto_started = time.monotonic()
        try:
            response = page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            return _failure(
                url, started, classify_exception(exc), f"Browser navigation failed: {exc}",
                limiter_wait_ms=limiter_wait_ms, browser_launch_ms=browser_launch_ms,
                goto_ms=int((time.monotonic() - goto_started) * 1000),
                acquisition=ACQUISITION_PAGE,
            )
        goto_ms = int((time.monotonic() - goto_started) * 1000)

        status_code = response.status if response is not None else None
        headers = dict(response.headers or {}) if response is not None else {}
        honour_retry_after(status_code, _header(headers, "retry-after"), url, limiter)

        # An HTTP >= 400 body is a block/error page; client-side JS is never
        # going to grow CONTENT_READY_SELECTOR's structural content into it, so
        # waiting the full timeout before classifying it as a failure is pure
        # waste.
        if status_code is None or status_code < 400:
            selector_started = time.monotonic()
            selector_timed_out = False
            try:
                page.wait_for_selector(CONTENT_READY_SELECTOR, timeout=CONTENT_READY_TIMEOUT_MS)
            except Exception:
                # A page that never renders structural content (e.g. a
                # contentless search-interface SPA) is real information, not an
                # error - capture whatever is there.
                selector_timed_out = True
            selector_wait_ms = int((time.monotonic() - selector_started) * 1000)

        body_text = page.content()
        body = body_text.encode("utf-8")
    finally:
        try:
            page.close()
        except Exception:
            pass

    status = classify_response(status_code, headers, body, source_type)
    error = None
    if status == FetchStatus.NETWORK_RESTRICTED:
        error = body[:300].decode("utf-8", errors="replace").strip()
    elif status != FetchStatus.OK:
        error = f"HTTP {status_code}"

    return FetchResult(
        url=(response.url if response is not None else url) or url,
        status=status, http_status=status_code,
        content_type=_header(headers, "content-type") or "text/html",
        raw_bytes=body if status == FetchStatus.OK else body,
        duration_ms=int((time.monotonic() - started) * 1000), error=error,
        limiter_wait_ms=limiter_wait_ms, http_ms=goto_ms,
        browser_launch_ms=browser_launch_ms, goto_ms=goto_ms,
        selector_wait_ms=selector_wait_ms, selector_timed_out=selector_timed_out,
        etag=_header(headers, "etag"), last_modified=_header(headers, "last-modified"),
        acquisition=ACQUISITION_PAGE,
    )


def fetch(
    url: str,
    source_type: str = "HTML",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    limiter: HostLimiter | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    session: PlaywrightSession | None = None,
) -> FetchResult:
    """Retrieve one URL through Playwright. Same contract as the legacy fetch().

    Returns a ``FetchResult`` exactly as ``fetcher.fetch`` did, so nothing
    downstream has to know which transport ran. ``session`` lets a whole sync
    run share one browser; without one, a private session is created and torn
    down for this single fetch.
    """
    started = time.monotonic()
    limiter = limiter if limiter is not None else get_limiter()

    # Resolve the single static proxy before launching anything. A malformed
    # FETCH_PROXY_URL must fail closed: going direct to a government host
    # because the operator's declared egress was unparseable is exactly what
    # the setting exists to prevent.
    try:
        proxy = browser_proxy()
    except ProxyConfigError as exc:
        return _failure(
            url, started, "proxy_config_error", str(exc),
            status=FetchStatus.NETWORK_RESTRICTED,
        )

    owned_session = session is None
    session = session if session is not None else PlaywrightSession()
    timeout_ms = max(1, int(timeout * 1000))

    try:
        with session.context_for(proxy) as (context, browser_launch_ms):
            # robots.txt first (once per host per run), then the politeness
            # wait, so a Crawl-delay the host publishes is already registered
            # before we decide how long to wait.
            honour_robots_crawl_delay(context, url, limiter, timeout_ms)
            limiter_wait_ms = int(limiter.wait(url) * 1000)

            if (source_type or "HTML").upper() in _BYTES_SOURCE_TYPES:
                return _fetch_bytes(
                    context, url, source_type, started, limiter, limiter_wait_ms,
                    browser_launch_ms, timeout_ms, etag, last_modified,
                )
            return _fetch_page(
                context, url, source_type, started, limiter, limiter_wait_ms,
                browser_launch_ms, timeout_ms, etag, last_modified,
            )
    except ImportError as exc:
        return _failure(url, started, "browser_launch_failed", f"playwright not installed: {exc}")
    except Exception as exc:
        return _failure(
            url, started, classify_exception(exc), f"Playwright fetch failed: {exc}",
        )
    finally:
        if owned_session:
            session.close()


def is_same_domain(a: str, b: str) -> bool:
    """Compare two URLs by host (keeps discovery on approved official hosts)."""
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()


def log_proxy_configuration() -> None:
    """Log the single configured proxy once, redacted. Never logs credentials."""
    from fetch.proxy import configured_proxy_url

    url = configured_proxy_url()
    if url:
        logger.info("Outbound fetches use the configured static proxy",
                    extra={"proxy": redact(url)})
