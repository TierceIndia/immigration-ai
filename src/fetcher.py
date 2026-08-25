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

import time
from dataclasses import dataclass
from enum import Enum

import requests

USER_AGENT = (
    "AU-Immigration-Source-Monitor/0.1 "
    "(Phase-1 prototype; contact: platform-team@example.invalid)"
)

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

    @property
    def ok(self) -> bool:
        return self.status == FetchStatus.OK


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


def fetch(url: str, source_type: str = "HTML", timeout: int = DEFAULT_TIMEOUT_SECONDS,
          max_retries: int = MAX_RETRIES) -> FetchResult:
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        start = time.monotonic()
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"},
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code >= 500 and attempt < max_retries:
                last_error = f"HTTP {resp.status_code} on attempt {attempt}"
                time.sleep(BACKOFF_BASE_SECONDS * attempt)
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
                time.sleep(BACKOFF_BASE_SECONDS * attempt)
                continue
            return FetchResult(
                url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
                content_type=None, raw_bytes=None, duration_ms=duration_ms, error=last_error,
            )
    return FetchResult(url=url, status=FetchStatus.FETCH_FAILED, http_status=None,
                        content_type=None, raw_bytes=None, duration_ms=0,
                        error=last_error or "unknown error")
