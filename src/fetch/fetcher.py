"""HTTP fetcher for government_sources rows.

Records: HTTP status, content-type, elapsed time, raw bytes, and a *classified* fetch status.
No cleaning/parsing happens here - that's extractor_html.py / extractor_pdf.py's job.

Classification principle (see PHASE1_AUDIT.md): a non-200 response must
never be silently treated as success, and a local network-egress rejection (this sandbox's own
proxy) must never be reported as if the government source itself blocked us - those are different
facts and conflating them was the real Phase-1 defect this module fixes.

No evasive/anti-bot-bypass technique is used or will be added here: no user-agent rotation, no
proxy rotation, no CAPTCHA/anti-bot bypass, no stealth headers. Retrieval is a plain, honestly
identified HTTP GET.
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

import requests

from fetch.politeness import HostLimiter, get_limiter

USER_AGENT = "AU-Immigration-Source-Monitor/0.1"

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


def _looks_like_network_restriction(resp: requests.Response) -> bool:
    # This sandbox's egress proxy identifies itself explicitly - detect it rather than guess.
    return "x-deny-reason" in resp.headers


def _looks_like_challenge_page(resp: requests.Response) -> bool:
    if resp.status_code not in (403, 429, 503):
        return False
    body_sample = (resp.text or "")[:2000].lower()
    return any(marker in body_sample for marker in _CHALLENGE_MARKERS)


def _classify_response(resp: requests.Response, source_type: str) -> FetchStatus:
    if _looks_like_network_restriction(resp):
        return FetchStatus.NETWORK_RESTRICTED
    if resp.status_code in (401, 403, 429):
        if _looks_like_challenge_page(resp):
            return FetchStatus.REQUIRES_BROWSER
        return FetchStatus.ACCESS_BLOCKED
    if resp.status_code != 200:
        return FetchStatus.FETCH_FAILED
    # 200 - validate the body is actually usable
    if not resp.content or len(resp.content) < 50:
        return FetchStatus.INVALID_CONTENT
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if source_type == "PDF" and "pdf" not in content_type and not resp.content.startswith(b"%PDF"):
        return FetchStatus.REQUIRES_MANUAL_DOWNLOAD
    return FetchStatus.OK


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


def _honour_robots_crawl_delay(url: str, limiter: HostLimiter, timeout: int) -> None:
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
        resp = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
            timeout=timeout,
        )
    except requests.RequestException:
        return
    if resp.status_code != 200 or not resp.text:
        return
    crawl_delay = _parse_crawl_delay(resp.text)
    if crawl_delay:
        limiter.note_crawl_delay(url, crawl_delay)


def _honour_retry_after(resp: requests.Response, url: str, limiter: HostLimiter) -> None:
    """Register a 429/503 ``Retry-After`` so this host pauses, if present.

    Only the delta-seconds form is honoured; an HTTP-date is ignored rather
    than guessed at, which keeps the pause a plain, auditable number.
    """
    if resp.status_code not in (429, 503):
        return
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return
    try:
        seconds = float(raw)
    except ValueError:
        return  # HTTP-date form: ignored on purpose (see docstring)
    if seconds > 0:
        limiter.note_retry_after(url, seconds)


def fetch(url: str, source_type: str = "HTML", timeout: int = DEFAULT_TIMEOUT_SECONDS,
          max_retries: int = MAX_RETRIES, limiter: HostLimiter | None = None) -> FetchResult:
    """Plain HTTP GET, spaced and jittered per host via the shared HostLimiter."""
    limiter = limiter if limiter is not None else get_limiter()
    _honour_robots_crawl_delay(url, limiter, timeout)
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        limiter.wait(url)
        start = time.monotonic()
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"},
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            _honour_retry_after(resp, url, limiter)

            if resp.status_code >= 500 and attempt < max_retries:
                last_error = f"HTTP {resp.status_code} on attempt {attempt}"
                limiter.backoff(BACKOFF_BASE_SECONDS * attempt)
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
            )
        except requests.RequestException as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            last_error = str(e)
            if attempt < max_retries:
                limiter.backoff(BACKOFF_BASE_SECONDS * attempt)
                continue
            return FetchResult(
                url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
                content_type=None, raw_bytes=None, duration_ms=duration_ms,
                error=last_error, error_type=_classify_exception(e),
            )
    return FetchResult(url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
                        content_type=None, raw_bytes=None, duration_ms=0,
                        error=last_error or "unknown error", error_type="unknown")
