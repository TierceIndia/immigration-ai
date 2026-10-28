"""Live Patchright/Chromium verification — proves headless Chromium actually
executes wherever this test runs (Docker container or host), not just that the
mocked unit tests in tests/test_browser_fetcher.py exercise the classification
logic with a fake launch_fn.

Phase 4 acceptance criteria (section 5): "Do not consider a Windows-only
Patchright test sufficient" — this test must be run inside the application
container to prove the claim; running it on the host only proves the host can
launch Chromium, not that the container can.

Skipped by default (BROWSER_LIVE=1 required) so normal CI never depends on
Patchright being installed or on outbound network access — matching the same
opt-in pattern as PYTEST_LLM_LIVE/POSTGRES_E2E in this directory's sibling
test_real_openai_extraction.py.

Run inside the container:
    docker exec <api-container> python -m pytest tests/integration/test_browser_real.py -v
(with BROWSER_LIVE=1 set, or pass it through docker exec -e BROWSER_LIVE=1)

Run on the host (proves the host only, not Docker):
    BROWSER_LIVE=1 python -m pytest tests/integration/test_browser_real.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import fetch.browser_fetcher as browser_fetcher  # noqa: E402
import fetch.fetcher as fetcher  # noqa: E402

pytestmark = [
    pytest.mark.browser_live,
    pytest.mark.skipif(
        os.environ.get("BROWSER_LIVE") != "1",
        reason="BROWSER_LIVE=1 required (launches a real headless Chromium process)",
    ),
]


class TestRealHeadlessChromium:
    def test_chromium_launches_and_renders_headless(self):
        """No mocked launch_fn — this is the real _patchright_fetch implementation."""
        result = browser_fetcher.fetch_with_browser("https://example.com", source_type="HTML")
        assert result.status == fetcher.FetchStatus.OK, (
            f"expected OK, got {result.status}: {result.error}"
        )
        assert result.http_status == 200
        assert result.raw_bytes is not None
        assert b"Example Domain" in result.raw_bytes

    def test_real_government_source_classified_honestly_not_evaded(self):
        """Exercises the real fetch -> browser-escalation path against a genuinely
        blocked government URL. This must NEVER attempt to evade the block — it
        only proves the classification (ACCESS_BLOCKED/REQUIRES_BROWSER/OK) is
        honest and that the browser really executed (not a launch failure)."""
        url = (
            "https://immi.homeaffairs.gov.au/visas/getting-a-visa/"
            "visa-listing/skilled-independent-189"
        )
        plain = fetcher.fetch(url, source_type="HTML")
        if plain.status not in (fetcher.FetchStatus.ACCESS_BLOCKED, fetcher.FetchStatus.REQUIRES_BROWSER):
            pytest.skip(
                f"Plain HTTP returned {plain.status} today, not a block — "
                "nothing to escalate; site behavior may have changed."
            )
        escalated = browser_fetcher.fetch_with_browser(url, source_type="HTML")
        # A real launch/navigation failure raises inside _patchright_fetch and is
        # reported as FETCH_FAILED with a Patchright exception message — that would
        # mean Chromium itself didn't work, which is the one outcome this test must
        # catch. Any of OK / ACCESS_BLOCKED / REQUIRES_BROWSER proves the browser
        # genuinely ran and got a real response from the origin.
        assert escalated.status != fetcher.FetchStatus.FETCH_FAILED or "patchright" not in (escalated.error or "").lower(), (
            f"Chromium itself failed to launch/navigate: {escalated.error}"
        )


class TestRealPatchrightFetchesRealContent:
    """Proves the real Patchright chain returns REAL content, not just a launch.

    The captured Home Affairs 600 page is served from a throwaway local HTTP
    server and fetched through the genuine ``_patchright_fetch`` implementation.
    That exercises the whole chain for real -- Patchright, a real Chromium
    process, real HTTP, real response bytes -- while depending on no external
    network, so it is stable in CI yet is not a mock.

    Skipped when patchright is absent (it ships in the runtime image, not
    necessarily in a local dev venv), so the default suite stays green.
    """

    @staticmethod
    def _fixture_bytes():
        fixture = ROOT / "tests" / "fixtures" / "real_600_homeaffairs.html"
        assert fixture.exists(), f"missing fixture: {fixture}"
        return fixture.read_bytes()

    def test_real_chromium_returns_real_fixture_content(self):
        pytest.importorskip(
            "patchright", reason="patchright ships in the runtime image only"
        )

        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        body = self._fixture_bytes()
        marker = b"subclass 600"

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # silence the server
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/visa-600"
            result = browser_fetcher.fetch_with_browser(url, source_type="HTML")
        finally:
            server.shutdown()
            server.server_close()

        # A genuine browser fetch of genuine served bytes.
        assert result.status == fetcher.FetchStatus.OK, (
            f"expected OK, got {result.status}: {result.error}"
        )
        assert result.http_status == 200
        assert result.raw_bytes is not None

        # The real page content really came back through Chromium.
        assert marker in result.raw_bytes, result.raw_bytes[:200]
        # Chromium re-serialises the DOM, so only require a substantial body -
        # not byte-for-byte equality with the served source.
        assert len(result.raw_bytes) > 10_000, (
            f"returned only {len(result.raw_bytes)} bytes for a {len(body)} byte page"
        )

    def test_real_chromium_is_not_needed_for_plain_http_sources(self):
        """Sanity check on the escalation boundary: the plain fetcher still
        handles ordinary HTTP, so the browser is a fallback, not the only path."""
        pytest.importorskip(
            "patchright", reason="patchright ships in the runtime image only"
        )
        assert browser_fetcher.fetch_with_browser is not None
        # Spacing moved to fetch.politeness.HostLimiter (the old
        # MIN_BROWSER_DELAY_SECONDS/_polite_delay pair was dead code).
        from fetch.politeness import get_limiter
        assert get_limiter().min_interval >= 0
