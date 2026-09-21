"""Hermetic Playwright/Chromium smoke test — proves a real browser launches.

This is NOT a pytest test (it launches a real Chromium) and is NOT collected.
It loads a ``data:`` URL, so it works offline and never touches a government site.

Succeeds locally and inside the API container:

    python scripts/browser_smoke.py                 # headless Chromium
    xvfb-run -a python scripts/browser_smoke.py --headed   # prod path (Xvfb)

Production uses ``src/browser_fetcher.py`` which launches Chromium with
``headless=False`` under Xvfb (headless mode is fingerprinted by AKAMAI/WAF and
returns 403 on real Home Affairs pages). ``--headed`` reproduces that launch
configuration against a harmless local page.

Exits 0 (BROWSER_SMOKE_OK) on success, non-zero on failure.
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    headless = "--headed" not in argv
    marker = "Smoke Test OK"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_context(locale="en-AU").new_page()
            page.set_default_timeout(10_000)
            page.goto(f"data:text/html,<h1>{marker}</h1>", wait_until="domcontentloaded")
            content = page.content()
        finally:
            browser.close()

    if marker not in content:
        print(f"BROWSER_SMOKE_FAILED: {content[:200]}")
        return 1
    print("BROWSER_SMOKE_OK" + ("_HEADED" if not headless else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())