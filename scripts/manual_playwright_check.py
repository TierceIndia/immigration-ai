"""Manual debugging script — NOT a pytest test.

Moved out of tests/ (previously tests/test_playwright.py) because it launches a
real Chromium + live network request at module import time, which crashed the
whole suite at collection ('1 error during collection') when the environment
could not start a browser. pytest never collects files under scripts/, so the
suite is immune to this file regardless of environment.

Usage:
    python scripts/manual_playwright_check.py
        Launches Chromium headless (safe default; works on servers/CI too).

    PLAYWRIGHT_HEADLESS=false python scripts/manual_playwright_check.py
        Launches a visible window for interactive debugging.

Note on headless vs 403: if you are checking whether Home Affairs returns 403
(false) as opposed to 200, use the production path src/browser_fetcher.py —
it deliberately launches headless=False because headless mode is fingerprinted
and returns HTTP 403 on real Home Affairs pages.
"""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


def main() -> None:
    headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        resp = page.goto(
            "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189"
        )
        print(resp.status)
        browser.close()


if __name__ == "__main__":
    main()