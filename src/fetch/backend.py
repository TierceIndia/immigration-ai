"""Which retrieval backend runs, and the one entry point everything calls.

Playwright is the backend (see fetch/playwright_client.py). ``FETCH_BACKEND``
exists only as a short-lived escape hatch so the port can be verified against
a real source side by side with the path it replaces:

    FETCH_BACKEND=playwright   (default) one browser, two mechanisms
    FETCH_BACKEND=legacy       the retired requests + patchright-escalation path

The legacy value and everything it reaches are deleted once the live check
against SRC-AU-MIGRATION-REGS-1994 passes (Phase 1d). Nothing new should be
built on it.

Every caller - the ORM sync path, source discovery, the legacy CLI - goes
through :func:`fetch` here, so there is exactly one place that decides how a
URL is retrieved and exactly one place that stamps ``acquisition``.
"""
from __future__ import annotations

import os

from fetch.fetcher import DEFAULT_TIMEOUT_SECONDS, FetchResult, FetchStatus
from fetch.politeness import HostLimiter
from logging_config import get_logger

logger = get_logger(__name__)

PLAYWRIGHT = "playwright"
LEGACY = "legacy"

# Legacy only: the one status that used to justify a second attempt through a
# different mechanism. A real 401/403/429 refusal was deliberately removed from
# this set - a browser hitting the same URL cannot change that answer, and
# changing fetch method in reaction to a refusal is exactly what this project
# does not do. Playwright-only makes the whole question moot.
_LEGACY_ESCALATION_STATUSES = {FetchStatus.REQUIRES_BROWSER}


def active_backend() -> str:
    """``"playwright"`` unless the operator explicitly asked for ``legacy``."""
    raw = (os.getenv("FETCH_BACKEND") or "").strip().lower()
    return LEGACY if raw == LEGACY else PLAYWRIGHT


def new_session():
    """A retrieval session for one sync run, or None when the backend has none.

    Both session types expose ``close()``, so callers never branch on which
    backend is active. One browser serves the whole run; each source still gets
    its own fresh context.
    """
    if active_backend() == LEGACY:
        from fetch.browser_fetcher import BrowserSession

        return BrowserSession()
    from fetch.playwright_client import PlaywrightSession

    return PlaywrightSession()


def _legacy_fetch(
    url: str, source_type: str, timeout: int, limiter: HostLimiter | None,
    etag: str | None, last_modified: str | None, session,
) -> FetchResult:
    import fetch.browser_fetcher as browser_fetcher
    import fetch.fetcher as legacy

    result = legacy.fetch(
        url, source_type=source_type, timeout=timeout, limiter=limiter,
        etag=etag, last_modified=last_modified,
    )
    result.acquisition = "http"
    if result.status in _LEGACY_ESCALATION_STATUSES:
        logger.info(
            "Legacy backend: challenge page, escalating to a browser session",
            extra={"url": url, "status": result.status.value},
        )
        result = browser_fetcher.fetch_with_browser(
            url, source_type=source_type, browser_session=session,
        )
        result.acquisition = "browser"
    return result


def fetch(
    url: str,
    source_type: str = "HTML",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    limiter: HostLimiter | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    session=None,
) -> FetchResult:
    """Retrieve one URL through the active backend.

    Returns a ``FetchResult`` with ``acquisition`` stamped, so the snapshot
    manifest records which mechanism actually produced the bytes.
    """
    if active_backend() == LEGACY:
        return _legacy_fetch(
            url, source_type, timeout, limiter, etag, last_modified, session,
        )

    from fetch.playwright_client import fetch as playwright_fetch

    return playwright_fetch(
        url, source_type=source_type, timeout=timeout, limiter=limiter,
        etag=etag, last_modified=last_modified, session=session,
    )
