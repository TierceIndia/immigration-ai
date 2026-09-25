"""Live Playwright/Chromium verification — proves headless Chromium actually
executes wherever this test runs (Docker container or host), not just that the
mocked unit tests in tests/test_browser_fetcher.py exercise the classification
logic with a fake launch_fn.

Phase 4 acceptance criteria (section 5): "Do not consider a Windows-only
Playwright test sufficient" — this test must be run inside the application
container to prove the claim; running it on the host only proves the host can
launch Chromium, not that the container can.

Skipped by default (BROWSER_LIVE=1 required) so normal CI never depends on
Playwright being installed or on outbound network access — matching the same
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

import browser_fetcher  # noqa: E402
import fetcher  # noqa: E402

pytestmark = [
    pytest.mark.browser_live,
    pytest.mark.skipif(
        os.environ.get("BROWSER_LIVE") != "1",
        reason="BROWSER_LIVE=1 required (launches a real headless Chromium process)",
    ),
]


class TestRealHeadlessChromium:
    def test_chromium_launches_and_renders_headless(self):
        """No mocked launch_fn — this is the real _playwright_fetch implementation."""
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
        # A real launch/navigation failure raises inside _playwright_fetch and is
        # reported as FETCH_FAILED with a Playwright exception message — that would
        # mean Chromium itself didn't work, which is the one outcome this test must
        # catch. Any of OK / ACCESS_BLOCKED / REQUIRES_BROWSER proves the browser
        # genuinely ran and got a real response from the origin.
        assert escalated.status != fetcher.FetchStatus.FETCH_FAILED or "playwright" not in (escalated.error or "").lower(), (
            f"Chromium itself failed to launch/navigate: {escalated.error}"
        )
