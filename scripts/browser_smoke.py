"""Hermetic Patchright/Chromium smoke test — proves a real browser launches.

This is NOT a pytest test (it launches a real Chromium) and is NOT collected.
It loads a ``data:`` URL, so it works offline and never touches a government site.

Headless only. There is deliberately no ``--headed`` flag: production runs
headless (see ``src/browser_fetcher.py``), headless needs no X server or Xvfb, and
headed mode was never shown to change any source's response.

Succeeds locally and inside the API container:

    python scripts/browser_smoke.py

Exits 0 (BROWSER_SMOKE_OK) on success, non-zero on failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from patchright.sync_api import sync_playwright

from fetch.browser_fetcher import BROWSER_LAUNCH_ARGS


def main() -> int:
    marker = "Smoke Test OK"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=list(BROWSER_LAUNCH_ARGS))
        try:
            page = browser.new_context(locale="en-AU").new_page()
            page.set_default_timeout(10_000)
            page.goto(
                f"data:text/html,<h1>{marker}</h1>", wait_until="domcontentloaded"
            )
            content = page.content()
        finally:
            browser.close()

    if marker not in content:
        print(f"BROWSER_SMOKE_FAILED: {content[:200]}")
        return 1
    print("BROWSER_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())