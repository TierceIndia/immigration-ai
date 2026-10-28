"""Item 5: conditional GET on the plain-HTTP fetch path.

Uses a real local HTTP server (loopback only, no outbound network, no
mocking of `requests`) so the If-None-Match/If-Modified-Since headers and
the 304 response are exercised exactly as a real government host would see
them - this is the "test with a fake server" the task asks for.
"""
from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher  # noqa: E402
from fetch.fetcher import FetchStatus  # noqa: E402
from fetch.politeness import HostLimiter  # noqa: E402

BODY = b"<html><body>" + (b"real content " * 10) + b"</body></html>"
ETAG = '"abc123"'
LAST_MODIFIED = "Wed, 01 Jan 2026 00:00:00 GMT"


class _ConditionalGetHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    received_headers: list[dict] = []

    def do_GET(self):  # noqa: N802
        self.received_headers.append(dict(self.headers.items()))
        if_none_match = self.headers.get("If-None-Match")
        if if_none_match == ETAG:
            self.send_response(304)
            self.send_header("ETag", ETAG)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("ETag", ETAG)
        self.send_header("Last-Modified", LAST_MODIFIED)
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):  # silence the server
        pass


@pytest.fixture()
def fake_server():
    _ConditionalGetHandler.received_headers = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ConditionalGetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/doc"
    finally:
        server.shutdown()
        server.server_close()


def _no_wait_limiter() -> HostLimiter:
    return HostLimiter(min_interval=0, jitter=0, sleep=lambda s: None)


class TestConditionalGetSendsValidators:
    def test_first_fetch_has_no_conditional_headers_and_captures_validators(self, fake_server):
        result = fetcher.fetch(fake_server, limiter=_no_wait_limiter())

        assert result.status == FetchStatus.OK
        assert result.not_modified is False
        assert result.raw_bytes == BODY
        assert result.etag == ETAG
        assert result.last_modified == LAST_MODIFIED

        sent = _ConditionalGetHandler.received_headers[-1]
        assert "If-None-Match" not in sent
        assert "If-Modified-Since" not in sent

    def test_second_fetch_sends_etag_and_last_modified(self, fake_server):
        fetcher.fetch(fake_server, limiter=_no_wait_limiter())
        fetcher.fetch(
            fake_server, limiter=_no_wait_limiter(),
            etag=ETAG, last_modified=LAST_MODIFIED,
        )

        sent = _ConditionalGetHandler.received_headers[-1]
        assert sent["If-None-Match"] == ETAG
        assert sent["If-Modified-Since"] == LAST_MODIFIED


class TestNotModifiedResponse:
    def test_304_is_ok_with_no_body(self, fake_server):
        result = fetcher.fetch(fake_server, limiter=_no_wait_limiter(), etag=ETAG)

        assert result.status == FetchStatus.OK
        assert result.http_status == 304
        assert result.not_modified is True
        assert result.raw_bytes is None
        assert result.ok is True  # a 304 is a success, not a failure

    def test_304_echoes_back_the_etag_the_caller_sent(self, fake_server):
        """Even if the 304 response itself omits ETag, the caller's own
        validator (proven still current) must not be lost."""
        result = fetcher.fetch(
            fake_server, limiter=_no_wait_limiter(),
            etag=ETAG, last_modified=LAST_MODIFIED,
        )
        assert result.etag == ETAG
        assert result.last_modified == LAST_MODIFIED

    def test_mismatched_etag_gets_a_real_200(self, fake_server):
        """A stale/wrong validator must not be honoured as if it matched."""
        result = fetcher.fetch(fake_server, limiter=_no_wait_limiter(), etag='"stale-value"')
        assert result.status == FetchStatus.OK
        assert result.not_modified is False
        assert result.raw_bytes == BODY
