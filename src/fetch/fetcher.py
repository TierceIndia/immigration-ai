"""HTTP fetcher for government_sources rows.

Records: HTTP status, content-type, elapsed time, raw bytes, and a *classified* fetch status.
No cleaning/parsing happens here - that's extractor_html.py / extractor_pdf.py's job.

Classification principle (see PHASE1_AUDIT.md): a non-200 response must
never be silently treated as success, and a local network-egress rejection (this sandbox's own
proxy) must never be reported as if the government source itself blocked us - those are different
facts and conflating them was the real Phase-1 defect this module fixes.

No evasive/anti-bot-bypass technique is used or will be added here: no user-agent rotation, no
proxy rotation, no CAPTCHA/anti-bot bypass, no stealth headers. Retrieval is a plain, honestly
identified HTTP GET. A single *static* egress proxy may be configured by the operator through
FETCH_PROXY_URL for reachability/egress control; it is one fixed value that is never rotated and
never changes in response to a refusal. See fetch/proxy.py.
"""
from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urljoin, urlparse

import requests

from fetch.politeness import HostLimiter, get_limiter
from fetch.proxy import ProxyConfigError, requests_proxies
from fetch.source_validation import SourceValidationError, assert_fetch_destination

DEFAULT_USER_AGENT = "AU-Immigration-Source-Monitor/0.1"


def user_agent() -> str:
    """The one honest User-Agent every retrieval path sends.

    Operator-overridable through ``FETCH_USER_AGENT`` so a deployment can
    declare a real contact string, but there is exactly one value per process
    and it is NEVER varied per request, per host, or in response to a refusal
    - that would be UA rotation, which this project does not do (see the
    module docstring and fetch/proxy.py's equivalent rule for the proxy).
    """
    raw = (os.getenv("FETCH_USER_AGENT") or "").strip()
    return raw or DEFAULT_USER_AGENT


# Back-compat module constant: the default value, for call sites and tests that
# reference fetcher.USER_AGENT directly. Prefer user_agent() so the env var is
# honoured.
USER_AGENT = DEFAULT_USER_AGENT

DEFAULT_TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2

# Heuristic markers of a JS/anti-bot challenge page (used only to *report* REQUIRES_BROWSER
# honestly - never used to attempt to defeat such a page).
_CHALLENGE_MARKERS = (
    "enable javascript", "checking your browser", "cf-browser-verification",
    "captcha", "cloudflare", "access denied by",
)


class FetchStatus(str, Enum):
    OK = "OK"
    FETCH_FAILED = "FETCH_FAILED"                    # network/timeout/connection error, no response
    ACCESS_BLOCKED = "ACCESS_BLOCKED"                 # real 401/403/429 from the origin server
    NETWORK_RESTRICTED = "NETWORK_RESTRICTED"         # local egress proxy rejected the request
    REQUIRES_BROWSER = "REQUIRES_BROWSER"             # response looks like a JS/anti-bot challenge
    REQUIRES_MANUAL_DOWNLOAD = "REQUIRES_MANUAL_DOWNLOAD"  # content-type mismatch / seeded MANUAL
    INVALID_CONTENT = "INVALID_CONTENT"               # 200 but empty/unparseable body


@dataclass
class FetchResult:
    url: str
    status: FetchStatus
    http_status: int | None
    content_type: str | None
    raw_bytes: bytes | None
    duration_ms: int
    error: str | None
    error_type: str | None = None  # machine-readable failure class, e.g. "dns_resolution_failure"
    # Stage timings for fetch/timing.py's per-source log line. None when this
    # FetchResult was built in a test/legacy path without timing, or when the
    # stage never ran (e.g. browser_launch_ms on a plain-HTTP result) — never
    # defaulted to 0, which would falsely claim the stage ran instantly.
    limiter_wait_ms: int | None = None
    http_ms: int | None = None
    browser_launch_ms: int | None = None
    goto_ms: int | None = None
    selector_wait_ms: int | None = None
    selector_timed_out: bool | None = None
    # Conditional GET (item 5). ``etag``/``last_modified`` are the origin's
    # current validators (from the response that produced this result, or
    # echoed back from the request when a 304 confirms the caller's own
    # validators are still current) — the caller persists them for next time.
    # ``not_modified`` is True only for a real 304: status stays OK (a 304 is
    # a success, just one with no body), but raw_bytes is None and nothing
    # downstream should re-store or re-extract.
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    # How this result was actually acquired, recorded verbatim in the raw
    # snapshot manifest so an archived artifact says which mechanism produced
    # it: "playwright_page" (real navigation + render) or
    # "playwright_request" (Playwright's APIRequestContext, used for PDFs,
    # robots.txt and conditional pre-checks). "http" is the legacy
    # requests-based path, retired in Phase 1d.
    acquisition: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == FetchStatus.OK


def _classify_exception(exc: BaseException) -> str:
    """Map a requests transport exception to an explicit, machine-readable class.

    All of these currently surface as ``FetchStatus.FETCH_FAILED``; ``error_type``
    is what tells operations *which* kind of network failure actually occurred.
    """
    # DNS resolution failure: ConnectionError whose underlying cause is socket.gaierror
    if isinstance(exc, requests.exceptions.ConnectionError):
        reason = getattr(getattr(exc, "args", [None])[0], "reason", None) or exc
        if isinstance(reason, socket.gaierror):
            return "dns_resolution_failure"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.exceptions.TooManyRedirects):
        return "too_many_redirects"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.RequestException):
        return "request_error"
    return "unknown"


def looks_like_challenge_body(status_code: int | None, body_sample: str) -> bool:
    """Whether a refusal body reads like a JS/anti-bot challenge page.

    Transport-agnostic (see ``classify_response``): used only to *report*
    REQUIRES_BROWSER honestly, never to attempt to defeat such a page.
    """
    if status_code not in (403, 429, 503):
        return False
    return any(marker in (body_sample or "")[:2000].lower() for marker in _CHALLENGE_MARKERS)


def classify_response(
    status_code: int | None,
    headers: dict | None,
    content: bytes | None,
    source_type: str,
) -> FetchStatus:
    """Classify one response from primitives, independent of the HTTP client.

    This is the single decision table both retrieval paths share - the
    ``requests`` path below and fetch/playwright_client.py - so a status can
    never be classified one way through one transport and another way through
    the other. Header lookup is case-insensitive because Playwright lower-cases
    header names while ``requests`` preserves them.
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    # This sandbox's egress proxy identifies itself explicitly - detect it rather than guess.
    if "x-deny-reason" in lowered:
        return FetchStatus.NETWORK_RESTRICTED
    if status_code in (401, 403, 429):
        body_sample = (content or b"")[:2000].decode("utf-8", errors="replace")
        if looks_like_challenge_body(status_code, body_sample):
            return FetchStatus.REQUIRES_BROWSER
        return FetchStatus.ACCESS_BLOCKED
    if status_code != 200:
        return FetchStatus.FETCH_FAILED
    # 200 - validate the body is actually usable
    if not content or len(content) < 50:
        return FetchStatus.INVALID_CONTENT
    content_type = str(lowered.get("content-type") or "").lower()
    if source_type == "PDF" and "pdf" not in content_type and not content.startswith(b"%PDF"):
        return FetchStatus.REQUIRES_MANUAL_DOWNLOAD
    return FetchStatus.OK


def _looks_like_network_restriction(resp: requests.Response) -> bool:
    # This sandbox's egress proxy identifies itself explicitly - detect it rather than guess.
    return "x-deny-reason" in resp.headers


def _looks_like_challenge_page(resp: requests.Response) -> bool:
    return looks_like_challenge_body(resp.status_code, resp.text or "")


def _classify_response(resp: requests.Response, source_type: str) -> FetchStatus:
    return classify_response(
        resp.status_code, dict(resp.headers), resp.content, source_type,
    )


def _parse_crawl_delay(robots_text: str, user_agent: str = USER_AGENT) -> float | None:
    """Largest ``Crawl-delay`` that applies to us, from a robots.txt body.

    Returns None when no applicable directive exists. Group handling follows
    the usual convention: a run of consecutive ``User-agent`` lines opens one
    group, whose directives run until the next group. When several applicable
    groups disagree the *larger* value wins, which is the conservative reading.
    """
    ours = (user_agent or "").lower()
    best: float | None = None
    lines = robots_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].split("#", 1)[0].strip()
        if not line.lower().startswith("user-agent"):
            i += 1
            continue
        agents: list[str] = []
        while i < len(lines):
            entry = lines[i].split("#", 1)[0].strip()
            if not entry.lower().startswith("user-agent"):
                break
            agents.append(entry.partition(":")[2].strip().lower())
            i += 1
        applies = any(
            agent == "*"
            or (agent and (ours.startswith(agent) or agent in ours))
            for agent in agents
        )
        while i < len(lines):
            entry = lines[i].split("#", 1)[0].strip()
            if not entry:
                i += 1
                continue
            if entry.lower().startswith("user-agent"):
                break
            field, _, value = entry.partition(":")
            if field.strip().lower() == "crawl-delay" and applies:
                try:
                    seconds = float(value.strip())
                except ValueError:
                    seconds = 0.0
                if seconds > 0:
                    best = seconds if best is None else max(best, seconds)
            i += 1
    return best


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


def _guarded_get(
    url: str, *, headers: dict[str, str], timeout: int,
    proxies: dict[str, str] | None, limiter: HostLimiter,
) -> tuple[requests.Response, int]:
    """GET with explicit, validated redirects and the same host spacing.

    ``requests`` would otherwise follow a ``Location`` to RFC1918 space or a
    cloud metadata endpoint before this process can inspect it. Every hop is
    checked immediately before the request is issued. The returned integer is
    the limiter wait added by redirect hops, so callers can still report the
    real total.
    """
    current = url
    redirect_wait_ms = 0
    for hop in range(_MAX_REDIRECTS + 1):
        assert_fetch_destination(current)
        if hop:
            redirect_wait_ms += int(limiter.wait(current) * 1000)
        response = requests.get(
            current,
            headers=headers,
            timeout=timeout,
            proxies=proxies,
            allow_redirects=False,
        )
        if response.status_code not in _REDIRECT_STATUSES:
            return response, redirect_wait_ms
        location = (getattr(response, "headers", {}) or {}).get("Location")
        if not location:
            return response, redirect_wait_ms
        current = urljoin(current, location)
    raise requests.TooManyRedirects(
        f"more than {_MAX_REDIRECTS} redirects while fetching {url}"
    )


def _honour_robots_crawl_delay(
    url: str, limiter: HostLimiter, timeout: int,
    proxies: dict[str, str] | None = None,
) -> None:
    """Fetch a host's robots.txt once per run and register its Crawl-delay.

    Consulted at most once per host per limiter lifetime (the limiter owns that
    bit of state, so it is naturally scoped to one sync run and reset with the
    limiter). Any failure — network, absence, unparsable body — simply leaves
    the default spacing in force; a missing robots.txt is not an error.
    """
    if not limiter.mark_robots_checked(url):
        return
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp, _ = _guarded_get(
            robots_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
            timeout=timeout,
            proxies=proxies,
            limiter=limiter,
        )
    except (requests.RequestException, SourceValidationError):
        return
    if resp.status_code != 200 or not resp.text:
        return
    crawl_delay = _parse_crawl_delay(resp.text)
    if crawl_delay:
        limiter.note_crawl_delay(url, crawl_delay)


def honour_retry_after(
    status_code: int | None, retry_after: str | None, url: str, limiter: HostLimiter,
) -> None:
    """Register a 429/503 ``Retry-After`` so this host pauses, if present.

    Only the delta-seconds form is honoured; an HTTP-date is ignored rather
    than guessed at, which keeps the pause a plain, auditable number.
    Transport-agnostic so both retrieval paths honour it identically.
    """
    if status_code not in (429, 503):
        return
    raw = (retry_after or "").strip()
    if not raw:
        return
    try:
        seconds = float(raw)
    except ValueError:
        return  # HTTP-date form: ignored on purpose (see docstring)
    if seconds > 0:
        limiter.note_retry_after(url, seconds)


def _honour_retry_after(resp: requests.Response, url: str, limiter: HostLimiter) -> None:
    honour_retry_after(resp.status_code, resp.headers.get("Retry-After"), url, limiter)


def fetch(url: str, source_type: str = "HTML", timeout: int = DEFAULT_TIMEOUT_SECONDS,
          max_retries: int = MAX_RETRIES, limiter: HostLimiter | None = None,
          etag: str | None = None, last_modified: str | None = None) -> FetchResult:
    """Plain HTTP GET, spaced and jittered per host via the shared HostLimiter.

    ``etag``/``last_modified`` (item 5): when the caller has validators from a
    previous fetch of this exact URL, they are sent as ``If-None-Match`` /
    ``If-Modified-Since`` so an unchanged origin can answer 304 with no body
    at all, instead of us re-downloading content we already have.
    """
    limiter = limiter if limiter is not None else get_limiter()

    # Resolve the optional static egress proxy *before* touching the network.
    # A misconfigured proxy must fail closed (no request, an explicit error)
    # rather than be ignored and silently retried as a direct connection.
    try:
        proxies = requests_proxies()
    except ProxyConfigError as exc:
        return FetchResult(
            url=url, status=FetchStatus.NETWORK_RESTRICTED, http_status=None,
            content_type=None, raw_bytes=None, duration_ms=0,
            error=str(exc), error_type="proxy_config_error",
        )

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    _honour_robots_crawl_delay(url, limiter, timeout, proxies)
    last_error: str | None = None
    limiter_wait_ms = 0
    for attempt in range(1, max_retries + 1):
        limiter_wait_ms += int(limiter.wait(url) * 1000)
        start = time.monotonic()
        try:
            resp, redirect_wait_ms = _guarded_get(
                url,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
                limiter=limiter,
            )
            limiter_wait_ms += redirect_wait_ms
            duration_ms = int((time.monotonic() - start) * 1000)

            _honour_retry_after(resp, url, limiter)

            if resp.status_code == 304:
                # The origin confirms our validators are still current: no
                # body at all, no browser, no extraction - this is a success
                # (status stays OK), just one with nothing new to store.
                return FetchResult(
                    url=url, status=FetchStatus.OK, http_status=304,
                    content_type=resp.headers.get("Content-Type"),
                    raw_bytes=None, duration_ms=duration_ms, error=None,
                    limiter_wait_ms=limiter_wait_ms, http_ms=duration_ms,
                    not_modified=True,
                    etag=resp.headers.get("ETag") or etag,
                    last_modified=resp.headers.get("Last-Modified") or last_modified,
                )

            if resp.status_code >= 500 and attempt < max_retries:
                last_error = f"HTTP {resp.status_code} on attempt {attempt}"
                limiter_wait_ms += int(limiter.backoff(BACKOFF_BASE_SECONDS * attempt) * 1000)
                continue

            status = _classify_response(resp, source_type)
            error = None
            if status == FetchStatus.NETWORK_RESTRICTED:
                error = resp.text.strip()[:300]
            elif status != FetchStatus.OK:
                error = f"HTTP {resp.status_code}"

            return FetchResult(
                url=url,
                status=status,
                http_status=resp.status_code,
                content_type=resp.headers.get("Content-Type"),
                raw_bytes=resp.content,
                duration_ms=duration_ms,
                error=error,
                limiter_wait_ms=limiter_wait_ms,
                http_ms=duration_ms,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
            )
        except SourceValidationError as exc:
            return FetchResult(
                url=url, status=FetchStatus.NETWORK_RESTRICTED, http_status=None,
                content_type=None, raw_bytes=None,
                duration_ms=int((time.monotonic() - start) * 1000),
                error=str(exc), error_type="blocked_destination",
                limiter_wait_ms=limiter_wait_ms,
            )
        except requests.RequestException as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            last_error = str(e)
            if attempt < max_retries:
                limiter_wait_ms += int(limiter.backoff(BACKOFF_BASE_SECONDS * attempt) * 1000)
                continue
            return FetchResult(
                url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
                content_type=None, raw_bytes=None, duration_ms=duration_ms,
                error=last_error, error_type=_classify_exception(e),
                limiter_wait_ms=limiter_wait_ms, http_ms=duration_ms,
            )
    return FetchResult(url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
                        content_type=None, raw_bytes=None, duration_ms=0,
                        error=last_error or "unknown error", error_type="unknown",
                        limiter_wait_ms=limiter_wait_ms, http_ms=0)
