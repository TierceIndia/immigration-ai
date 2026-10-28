"""A local fixture HTTP server for fetch-path tests.

Every fetch test in this project runs against this, never against a real
government host. It serves one route per behaviour the retrieval path has to
get right, on 127.0.0.1 with an ephemeral port:

    /static            plain HTML, content already in the DOM
    /hydrated          content injected by client-side JS after DOMContentLoaded
    /json-in-input     the real Home Affairs shape: escaped JSON in a hidden
                       <input>, materialised into DOM nodes by JS
    /news              a news/update listing (Phase 2 NEWS_LIST parsing)
    /doc.pdf           real PDF bytes (%PDF header)
    /scanned.pdf       a PDF with no extractable text layer
    /empty             200 with a body under the 50-byte usability floor
    /blocked           403, plain refusal body
    /challenge         403, body that reads like an anti-bot challenge page
    /throttled         429 with Retry-After: 7
    /slow              HTML that never renders structural content
    /conditional       ETag + Last-Modified, answers 304 to a matching
                       If-None-Match / If-Modified-Since
    /robots.txt        Crawl-delay for our User-Agent

It records every request (path, method, headers) so a test can assert what was
actually sent - which User-Agent, whether conditional headers went out, and
how many requests a run really made.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_HTML = (
    b"<html><head><title>Skilled Independent visa (subclass 189)</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<main>"
    b"<h2>Age</h2><p>You must be under 45 years of age at the time of invitation.</p>"
    b"<h2>Points</h2><p>You must score at least 65 points on the points test.</p>"
    b"<h2>English</h2><p>You must have competent English or higher.</p>"
    b"</main>"
    b"</body></html>"
)

# The content only exists after the page's own JS runs. A fetch that captures
# the DOM at domcontentloaded sees the placeholder; one that waits for
# structural content sees the real text.
HYDRATED_HTML = (
    b"<html><head><title>Hydrated page</title></head><body>"
    b"<main id='root'><span>Loading...</span></main>"
    b"<script>"
    b"document.addEventListener('DOMContentLoaded', function () {"
    b"  setTimeout(function () {"
    b"    document.getElementById('root').innerHTML ="
    b"      '<h2>Age</h2><p>Applicants must be under 45 years of age.</p>';"
    b"  }, 150);"
    b"});"
    b"</script>"
    b"</body></html>"
)

# The real immi.homeaffairs.gov.au shape: the content ships as HTML-escaped
# JSON inside a hidden input's value and is materialised by client-side JS.
JSON_IN_INPUT_HTML = (
    b"<html><head><title>JSON hydration</title></head><body>"
    b"<input type='hidden' id='state' value='"
    b"{&quot;eligibility&quot;:&quot;You must be under 45 years of age.&quot;}'>"
    b"<main id='root'></main>"
    b"<script>"
    b"document.addEventListener('DOMContentLoaded', function () {"
    b"  var raw = document.getElementById('state').value;"
    b"  var data = JSON.parse(raw);"
    b"  document.getElementById('root').innerHTML ="
    b"    '<h2>Eligibility</h2><p>' + data.eligibility + '</p>';"
    b"});"
    b"</script>"
    b"</body></html>"
)

NEWS_HTML = (
    b"<html><head><title>Latest updates</title></head><body>"
    b"<h1>Latest updates</h1>"
    b"<main>"
    b"<ul class='news-list'>"
    b"<li><a href='/news/points-change'>Points test updated</a>"
    b"<time datetime='2026-10-01'>1 October 2026</time>"
    b"<p>The points test threshold changed on 1 October 2026.</p></li>"
    b"<li><a href='/news/occupation-list'>Occupation list revised</a>"
    b"<time datetime='2026-09-15'>15 September 2026</time>"
    b"<p>Two occupations were added to the list.</p></li>"
    b"</ul>"
    b"</main>"
    b"</body></html>"
)

# A page that loads but never renders anything matching the content-ready
# selector - a real pattern (a search-interface SPA with nothing to show).
SLOW_HTML = (
    b"<html><head><title>Course search</title></head><body>"
    b"<div id='app'>Search for a course to see results</div>"
    b"</body></html>"
)

ROBOTS_TXT = (
    b"User-agent: *\n"
    b"Crawl-delay: 3\n"
    b"Disallow: /private\n"
)

CONDITIONAL_ETAG = '"fixture-etag-v1"'
CONDITIONAL_LAST_MODIFIED = "Wed, 01 Jan 2026 00:00:00 GMT"
CONDITIONAL_HTML = (
    b"<html><head><title>Conditional</title></head><body><main>"
    b"<h1>Conditional document</h1>"
    b"<p>This body is only sent when the caller's validators do not match.</p>"
    b"</main></body></html>"
)


def minimal_pdf(text: str = "Applicants must be under 45 years of age.") -> bytes:
    """A tiny, real PDF with one page of extractable text.

    Hand-built rather than generated with a library so the bytes are stable
    across pypdf versions and the test does not depend on a writer API.
    """
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def scanned_pdf() -> bytes:
    """A PDF with a page but no text-drawing operators - i.e. a scan.

    pypdf extracts an empty string from this, which is exactly the case that
    must be reported honestly instead of silently storing a chunkless document.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup.

        Playwright's HTTP client lower-cases request header names while a
        navigation sends them capitalised, so a test asserting on what we sent
        must not care which.
        """
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None


@dataclass
class FixtureServer:
    """A running fixture server. Use :func:`fixture_server` to get one."""
    base_url: str
    requests: list[RecordedRequest] = field(default_factory=list)
    _server: object = None
    _thread: object = None

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def paths_requested(self) -> list[str]:
        return [r.path for r in self.requests]

    def last_request_for(self, path: str) -> RecordedRequest | None:
        for recorded in reversed(self.requests):
            if recorded.path == path:
                return recorded
        return None

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def _build_handler(recorder: list[RecordedRequest]):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _send_empty(self, status: int, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
            recorder.append(RecordedRequest("GET", self.path, dict(self.headers.items())))
            path = self.path.split("?", 1)[0]

            if path == "/static":
                return self._send(200, STATIC_HTML, "text/html; charset=utf-8")
            if path == "/hydrated":
                return self._send(200, HYDRATED_HTML, "text/html; charset=utf-8")
            if path == "/json-in-input":
                return self._send(200, JSON_IN_INPUT_HTML, "text/html; charset=utf-8")
            if path == "/news":
                return self._send(200, NEWS_HTML, "text/html; charset=utf-8")
            if path == "/slow":
                return self._send(200, SLOW_HTML, "text/html; charset=utf-8")
            if path == "/doc.pdf":
                return self._send(200, minimal_pdf(), "application/pdf")
            if path == "/scanned.pdf":
                return self._send(200, scanned_pdf(), "application/pdf")
            if path == "/empty":
                return self._send(200, b"<html></html>", "text/html; charset=utf-8")
            if path == "/blocked":
                return self._send(403, b"Forbidden. Your request was refused.", "text/plain")
            if path == "/challenge":
                return self._send(
                    403,
                    b"<html><body>Checking your browser before accessing this site."
                    b" Please enable JavaScript.</body></html>",
                    "text/html; charset=utf-8",
                )
            if path == "/throttled":
                return self._send(
                    429, b"Too many requests.", "text/plain", {"Retry-After": "7"},
                )
            if path == "/robots.txt":
                return self._send(200, ROBOTS_TXT, "text/plain")
            if path == "/conditional":
                validators = {
                    "ETag": CONDITIONAL_ETAG,
                    "Last-Modified": CONDITIONAL_LAST_MODIFIED,
                }
                if self.headers.get("If-None-Match") == CONDITIONAL_ETAG:
                    return self._send_empty(304, validators)
                if self.headers.get("If-Modified-Since") == CONDITIONAL_LAST_MODIFIED:
                    return self._send_empty(304, validators)
                return self._send(
                    200, CONDITIONAL_HTML, "text/html; charset=utf-8", validators,
                )
            return self._send(404, b"not found", "text/plain")

        def log_message(self, *args):  # keep the test output clean
            pass

    return _Handler


def start_fixture_server() -> FixtureServer:
    """Start the fixture server on an ephemeral loopback port."""
    recorder: list[RecordedRequest] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _build_handler(recorder))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    return FixtureServer(
        base_url=f"http://{host}:{port}", requests=recorder,
        _server=server, _thread=thread,
    )
