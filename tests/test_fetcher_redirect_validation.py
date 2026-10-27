"""The legacy requests path must not follow unvalidated redirects."""
from __future__ import annotations

import requests

import fetch.fetcher as fetcher
from fetch.politeness import HostLimiter
from fetch.source_validation import SourceValidationError


def _limiter() -> HostLimiter:
    return HostLimiter(min_interval=0, jitter=0, sleep=lambda _s: None)


class _Response:
    def __init__(self, status_code=200, location=None, body=b"x" * 80):
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html"}
        if location:
            self.headers["Location"] = location
        self.content = body
        self.text = body.decode("utf-8", errors="replace")

    @property
    def ok(self):
        return self.status_code < 400


def test_redirect_target_is_validated_before_it_is_requested(monkeypatch):
    requested: list[str] = []
    validated: list[str] = []

    def guard(url):
        validated.append(url)
        if "127.0.0.1" in url:
            raise SourceValidationError("private destination")
        return url

    def fake_get(url, **kwargs):
        requested.append(url)
        if url.endswith("/robots.txt"):
            return _Response(status_code=404)
        if url == "https://example.gov.au/start":
            return _Response(status_code=302, location="http://127.0.0.1/secret")
        raise AssertionError(f"unexpected request to {url}")

    monkeypatch.setattr(fetcher, "assert_fetch_destination", guard)
    monkeypatch.setattr(fetcher.requests, "get", fake_get)

    result = fetcher.fetch("https://example.gov.au/start", limiter=_limiter(), max_retries=1)

    assert result.status == fetcher.FetchStatus.NETWORK_RESTRICTED
    assert result.error_type == "blocked_destination"
    assert "http://127.0.0.1/secret" in validated
    assert "http://127.0.0.1/secret" not in requested


def test_automatic_redirect_following_is_off(monkeypatch):
    seen: list[dict] = []

    def fake_get(url, **kwargs):
        seen.append(kwargs)
        return _Response()

    monkeypatch.setattr(fetcher.requests, "get", fake_get)
    fetcher.fetch("https://example.gov.au/page", limiter=_limiter(), max_retries=1)

    assert seen
    assert all(call["allow_redirects"] is False for call in seen)


def test_redirect_loop_is_reported_as_too_many_redirects(monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _Response(status_code=404)
        return _Response(status_code=302, location=url)

    monkeypatch.setattr(fetcher.requests, "get", fake_get)
    result = fetcher.fetch(
        "https://example.gov.au/loop", limiter=_limiter(), max_retries=1,
    )

    assert result.status == fetcher.FetchStatus.FETCH_FAILED
    assert result.error_type == "too_many_redirects"


def test_relative_redirects_are_resolved_and_followed(monkeypatch):
    requested: list[str] = []

    def fake_get(url, **kwargs):
        requested.append(url)
        if url.endswith("/robots.txt"):
            return _Response(status_code=404)
        if url == "https://example.gov.au/start":
            return _Response(status_code=301, location="/final")
        return _Response(status_code=200)

    monkeypatch.setattr(fetcher.requests, "get", fake_get)
    result = fetcher.fetch(
        "https://example.gov.au/start", limiter=_limiter(), max_retries=1,
    )

    assert result.status == fetcher.FetchStatus.OK
    assert "https://example.gov.au/final" in requested


def test_requests_exception_still_maps_to_a_transport_failure(monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("/robots.txt"):
            return _Response(status_code=404)
        raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(fetcher.requests, "get", fake_get)
    result = fetcher.fetch(
        "https://example.gov.au/page", limiter=_limiter(), max_retries=1,
    )

    assert result.status == fetcher.FetchStatus.FETCH_FAILED
    assert result.error_type == "read_timeout"
