"""Unit tests for browser_fetcher classification/escalation logic.

A real browser is never launched here and no real government domain is ever hit:
we exercise the pure classification logic (_classify_browser_result) and drive
the Patchright call chain through an injected fake module.

Patchright is headless by default. There is no fingerprint evasion, no
automation-detectable override, and no retry-until-it-works: a challenge page is
reported honestly (REQUIRES_BROWSER / challenge_page) rather than fought.
"""
from __future__ import annotations

import sys
import types

import pytest

from fetch.browser_fetcher import (
    _classify_browser_exception,
    _classify_browser_result,
    _is_headless,
    _looks_like_challenge_text,
    _patchright_fetch,
    BROWSER_LAUNCH_ARGS,
    BrowserSession,
    fetch_with_browser,
    is_same_domain,
)
from fetch.fetcher import FetchStatus


# ---------------------------------------------------------------------------
# Fake Patchright call chain
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {"content-type": "text/html"}


class _FakePage:
    def __init__(self, body, response=None, goto_exc=None,
                 body_before_render=None, wait_for_selector_exc=None):
        self._body = body
        self._response = response or _FakeResponse()
        self._goto_exc = goto_exc
        # When set, content() returns this until wait_for_selector() is called
        # (simulating a page whose real content only exists after client-side
        # JS finishes rendering it) — proves the fetcher actually waits before
        # capturing, rather than just tolerating a wait_for_selector call.
        self._body_before_render = body_before_render
        self._rendered = body_before_render is None
        self.wait_for_selector_calls = []
        self._wait_for_selector_exc = wait_for_selector_exc
        self.route_handler = None

    def set_default_timeout(self, ms):
        self.timeout_ms = ms

    def route(self, pattern, handler):
        # Fakes never issue real sub-requests, so there is nothing to route -
        # this only records the handler so a test can assert it was wired up.
        self.route_handler = handler

    def goto(self, url, wait_until=None):
        if self._goto_exc is not None:
            raise self._goto_exc
        return self._response

    def wait_for_selector(self, selector, timeout=None):
        self.wait_for_selector_calls.append((selector, timeout))
        if self._wait_for_selector_exc is not None:
            raise self._wait_for_selector_exc
        self._rendered = True

    def content(self):
        if not self._rendered:
            return self._body_before_render
        return self._body


class _FakeContext:
    def __init__(self, page, sink):
        self._page = page
        self._sink = sink

    def new_page(self):
        return self._page

    def close(self):
        self._sink.append("context.close")


class _FakeBrowser:
    def __init__(self, page, sink, context_exc=None):
        self._page = page
        self._sink = sink
        self._context_exc = context_exc

    def new_context(self, **kwargs):
        if self._context_exc is not None:
            raise self._context_exc
        self._sink.append(("new_context", kwargs))
        return _FakeContext(self._page, self._sink)

    def close(self):
        self._sink.append("browser.close")


class _FakeChromium:
    def __init__(self, browser, sink, launch_exc=None):
        self._browser = browser
        self._sink = sink
        self._launch_exc = launch_exc

    def launch(self, **kwargs):
        self._sink.append(("launch", kwargs))
        if self._launch_exc is not None:
            raise self._launch_exc
        return self._browser


class _FakePatchright:
    def __init__(self, chromium):
        self.chromium = chromium


class _FakeSyncPatchrightCM:
    def __init__(self, patchright):
        self._pw = patchright

    def __enter__(self):
        return self._pw

    def __exit__(self, *exc_info):
        return False


def _install_fake_patchright(
    monkeypatch,
    body="<html><body>" + ("OK " * 20) + "</body></html>",
    response=None,
    launch_exc=None,
    goto_exc=None,
    context_exc=None,
    body_before_render=None,
    wait_for_selector_exc=None,
):
    """Install a fake `patchright.sync_api` and return the call-recording sink.

    patchright is only present in the runtime image, not necessarily in a local
    dev venv, so the module is synthesised here rather than imported.
    """
    sink = []
    page = _FakePage(body, response=response, goto_exc=goto_exc,
                      body_before_render=body_before_render,
                      wait_for_selector_exc=wait_for_selector_exc)
    browser = _FakeBrowser(page, sink, context_exc=context_exc)
    chromium = _FakeChromium(browser, sink, launch_exc=launch_exc)

    sync_api = types.ModuleType("patchright.sync_api")
    sync_api.sync_playwright = lambda: _FakeSyncPatchrightCM(_FakePatchright(chromium))
    patchright_pkg = types.ModuleType("patchright")
    patchright_pkg.sync_api = sync_api

    monkeypatch.setitem(sys.modules, "patchright", patchright_pkg)
    monkeypatch.setitem(sys.modules, "patchright.sync_api", sync_api)
    return sink


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_is_same_domain():
    assert is_same_domain(
        "https://immi.homeaffairs.gov.au/visas",
        "https://immi.homeaffairs.gov.au/visas/482",
    )
    assert not is_same_domain(
        "https://immi.homeaffairs.gov.au/visas",
        "https://legislation.gov.au/foo",
    )


def test_challenge_marker_detection():
    assert _looks_like_challenge_text("Checking your browser before accessing")
    assert _looks_like_challenge_text("Access denied by Cloudflare")
    assert not _looks_like_challenge_text("A real policy page about skilled visas")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def test_classify_ok_html():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/page",
        status_code=200,
        body_bytes=b"<html><body>visa 482 skilled worker requirements</body></html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.OK
    assert res.raw_bytes is not None


def test_classify_access_blocked_403():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/page",
        status_code=403,
        body_bytes=b"<html>access denied</html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.ACCESS_BLOCKED
    assert res.raw_bytes is None


def test_classify_challenge_page_even_in_browser():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/page",
        status_code=200,
        body_bytes=b"<html>Enable javascript and checking your browser...</html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.REQUIRES_BROWSER
    assert res.raw_bytes is None
    assert res.error_type == "challenge_page"


def test_classify_pdf_rendered_as_html_is_invalid_content():
    """A browser-rendered PDF viewer is NOT the PDF. Storing it as evidence
    would be fabricating a government document, so it is rejected as invalid."""
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/doc",
        status_code=200,
        body_bytes=b"<html>PDF viewer rendering</html>",
        content_type="text/html",
        source_type="PDF",
    )
    assert res.status == FetchStatus.INVALID_CONTENT
    assert res.raw_bytes is None
    assert res.error_type == "pdf_rendered_as_html"


def test_classify_pdf_ok():
    res = _classify_browser_result(
        url="https://immi.homeaffairs.gov.au/doc",
        status_code=200,
        body_bytes=b"%PDF-1.4 %some fake pdf body content here for test",
        content_type="application/pdf",
        source_type="PDF",
    )
    assert res.status == FetchStatus.OK


def test_classify_unexpected_status_fetch_failed():
    res = _classify_browser_result(
        url="https://example.gov/page",
        status_code=500,
        body_bytes=b"<html>server error</html>",
        content_type="text/html",
        source_type="HTML",
    )
    assert res.status == FetchStatus.FETCH_FAILED
    assert res.error_type == "unexpected_http_status"


def test_classify_503_challenge():
    res = _classify_browser_result(
        url="https://example.gov/page",
        status_code=503,
        body_bytes=b"<html>unavailable</html>",
        content_type="text/html",
        source_type="HTML",
        challenge_body="Checking your browser before accessing",
    )
    assert res.status == FetchStatus.REQUIRES_BROWSER


# ---------------------------------------------------------------------------
# Headless configuration
# ---------------------------------------------------------------------------
def test_chromium_launches_headless_by_default(monkeypatch):
    """Headless is the production default: no X server is assumed anywhere."""
    monkeypatch.delenv("BROWSER_HEADLESS", raising=False)
    assert _is_headless() is True

    sink = _install_fake_patchright(monkeypatch)
    result = _patchright_fetch("https://example.gov/page", "HTML", 20)

    launches = [c for c in sink if c[0] == "launch"]
    assert len(launches) == 1
    assert launches[0][1]["headless"] is True
    assert result.status == FetchStatus.OK


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_is_headless_parses_env_values(monkeypatch, value, expected):
    monkeypatch.setenv("BROWSER_HEADLESS", value)
    assert _is_headless() is expected


def test_launch_args_are_container_hardening_not_evasion():
    """--no-sandbox/--disable-dev-shm-usage are container compatibility flags,
    not root requirements. They must not grow into fingerprint-evasion flags."""
    assert "--no-sandbox" in BROWSER_LAUNCH_ARGS
    assert "--disable-dev-shm-usage" in BROWSER_LAUNCH_ARGS
    for evasion in ("--disable-blink-features", "user-agent", "AutomationControlled"):
        assert evasion not in " ".join(BROWSER_LAUNCH_ARGS)


# ---------------------------------------------------------------------------
# Driver-level failure classification
# ---------------------------------------------------------------------------
def test_patchright_missing_returns_launch_failure(monkeypatch):
    """With no Patchright importable the failure is classified, not crashed.

    The import is forced to fail via ``sys.modules`` (a ``None`` entry halts the
    import), so this stays hermetic whether or not patchright happens to be
    installed in the running environment.
    """
    monkeypatch.setitem(sys.modules, "patchright", None)
    monkeypatch.setitem(sys.modules, "patchright.sync_api", None)
    res = _patchright_fetch("https://example.gov/page", "HTML", 20)
    assert res.status == FetchStatus.FETCH_FAILED
    assert res.error_type == "browser_launch_failed"
    assert "patchright not installed" in (res.error or "")


def test_browser_launch_failure_is_classified(monkeypatch):
    _install_fake_patchright(monkeypatch, launch_exc=RuntimeError("no chromium binary"))
    res = _patchright_fetch("https://example.gov/page", "HTML", 20)
    assert res.status == FetchStatus.FETCH_FAILED
    assert res.error_type == "browser_launch_failed"
    assert "no chromium binary" in (res.error or "")


def test_patchright_installed_but_browser_binary_missing(monkeypatch):
    """Patchright importable but its Chromium never downloaded.

    This is the real machine-dependent case: ``import patchright.sync_api``
    succeeds and ``chromium.launch()`` raises Playwright's own "Executable
    doesn't exist" error because the browser was never installed. It must still
    classify as ``browser_launch_failed`` rather than leaking the raw exception.
    """
    _install_fake_patchright(
        monkeypatch,
        launch_exc=RuntimeError(
            "Executable doesn't exist at "
            "C:\\Users\\me\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome.exe\n"
            "Please run the following command to download new browsers: "
            "patchright install chromium"
        ),
    )
    res = _patchright_fetch("https://example.gov/page", "HTML", 20)
    assert res.status == FetchStatus.FETCH_FAILED
    assert res.error_type == "browser_launch_failed"
    assert "Executable doesn't exist" in (res.error or "")


def test_browser_context_failure_is_classified(monkeypatch):
    _install_fake_patchright(monkeypatch, context_exc=RuntimeError("cannot create context"))
    res = _patchright_fetch("https://example.gov/page", "HTML", 20)
    assert res.status == FetchStatus.FETCH_FAILED
    assert res.error_type == "browser_context_failed"


# ---------------------------------------------------------------------------
# Client-side (Angular) rendering race — several immi.homeaffairs.gov.au pages
# ship their real content as an HTML-escaped JSON blob inside a hidden
# <input>'s value attribute and only materialise real <p>/<ul>/<table> DOM
# nodes via client-side JS *after* domcontentloaded fires. Capturing
# page.content() immediately produced 0 extractable chunks even on a
# successful 200 fetch (confirmed live against real saved raw snapshots for
# SRC-AU-600-MAIN and SRC-AU-189-MAIN, not a hypothetical). Waiting for a real
# structural element to exist before capturing fixes this.
# ---------------------------------------------------------------------------
_PRE_RENDER_BODY = (
    '<html><body><main role="main">'
    '<input type="hidden" value="{&quot;eligibility&quot;:&quot;'
    '\\u003cul\\u003e\\n\\t\\u003cli\\u003ebe a genuine visitor\\u003c/li\\u003e\\n'
    '\\u003c/ul\\u003e\\n&quot;}">'
    '</main></body></html>'
)
_POST_RENDER_BODY = (
    '<html><body><main role="main">'
    '<h1>Visitor visa</h1>'
    '<ul><li>be a genuine visitor</li></ul>'
    '</main></body></html>'
)


def test_waits_for_real_content_before_capturing(monkeypatch):
    """The exact bug found live: a page that is served with only the
    pre-hydration JSON blob immediately after domcontentloaded, but renders
    real content shortly after, must have its RENDERED content captured, not
    the pre-render blob."""
    _install_fake_patchright(
        monkeypatch, body=_POST_RENDER_BODY, body_before_render=_PRE_RENDER_BODY,
    )
    res = _patchright_fetch("https://immi.homeaffairs.gov.au/visas/visitor-600", "HTML", 20)
    assert res.status == FetchStatus.OK
    assert b"be a genuine visitor" in res.raw_bytes
    assert b"eligibility&quot;" not in res.raw_bytes


def test_genuinely_contentless_page_is_still_captured_after_wait_times_out(monkeypatch):
    """A page that never renders structural content (e.g. CRICOS's
    search-interface SPA) must not hang or fail — it's captured honestly as
    whatever is there, exactly as before this wait was added."""
    never_renders = (
        '<html><body><main role="main">'
        '<div>Course Search — no results without a query</div>'
        '</main></body></html>'
    )
    _install_fake_patchright(
        monkeypatch, body=never_renders, body_before_render=never_renders,
        wait_for_selector_exc=TimeoutError("Timeout waiting for selector"),
    )
    res = _patchright_fetch("https://cricos.education.gov.au", "HTML", 20)
    assert res.status == FetchStatus.OK
    assert b"Course Search" in res.raw_bytes


def test_block_page_skips_the_content_ready_wait(monkeypatch):
    """Item 3: goto returning HTTP >= 400 is a block/error page that client-side
    JS is never going to grow CONTENT_READY_SELECTOR's structural content into
    — waiting the full CONTENT_READY_TIMEOUT_MS (8s) there was pure waste
    before classifying it as a failure anyway."""
    block_body = "<html><body>Access Denied</body></html>"
    _install_fake_patchright(
        monkeypatch, body=block_body, body_before_render=block_body,
        response=_FakeResponse(status=403),
        wait_for_selector_exc=AssertionError("wait_for_selector must not be called"),
    )
    res = _patchright_fetch("https://example.gov/blocked", "HTML", 20)
    assert res.status == FetchStatus.ACCESS_BLOCKED
    assert res.selector_wait_ms is None
    assert res.selector_timed_out is None


def test_ok_page_still_waits_for_content_ready(monkeypatch):
    """The skip is specific to >= 400 — a normal 200 still gets the wait."""
    _install_fake_patchright(
        monkeypatch, body=_POST_RENDER_BODY, body_before_render=_PRE_RENDER_BODY,
        response=_FakeResponse(status=200),
    )
    res = _patchright_fetch("https://immi.homeaffairs.gov.au/visas/visitor-600", "HTML", 20)
    assert res.status == FetchStatus.OK
    assert res.selector_wait_ms is not None
    assert res.selector_timed_out is False


def test_browser_navigation_timeout_is_classified(monkeypatch):
    """A navigation timeout is reported as browser_timeout so operators can tell
    a slow site apart from a blocked one."""

    class TimeoutError_(Exception):
        pass

    TimeoutError_.__name__ = "TimeoutError"

    _install_fake_patchright(
        monkeypatch, goto_exc=TimeoutError_("page.goto: Timeout 30000ms exceeded")
    )
    res = _patchright_fetch("https://example.gov/page", "HTML", 20)
    assert res.status == FetchStatus.FETCH_FAILED
    assert res.error_type == "browser_timeout"
    assert "Timeout" in (res.error or "")


def test_exception_classification_is_stable():
    """Mapping is by class name so it survives a missing/rearranged driver
    exception hierarchy, and stays deterministic."""

    class TargetClosedError(Exception):
        pass

    class BrowserClosedError(Exception):
        pass

    class Error(Exception):
        pass

    class TimeoutError_(Exception):
        pass

    assert _classify_browser_exception(TimeoutError_("x")) == "browser_timeout"
    assert _classify_browser_exception(TargetClosedError("x")) == "browser_launch_failed"
    assert _classify_browser_exception(BrowserClosedError("x")) == "browser_launch_failed"
    assert _classify_browser_exception(Error("x")) == "browser_navigation_failed"
    assert _classify_browser_exception(ValueError("x")) == "browser_navigation_failed"


def test_browser_session_is_always_torn_down(monkeypatch):
    """Context and browser are closed on every path; a leaked Chromium process
    would otherwise accumulate across syncs."""
    for kwargs in (
        {"goto_exc": RuntimeError("navigation blew up")},
        {},
    ):
        sink = _install_fake_patchright(monkeypatch, **kwargs)
        _patchright_fetch("https://example.gov/page", "HTML", 20)
        assert "context.close" in sink, kwargs
        assert "browser.close" in sink, kwargs


def test_fetch_with_browser_defaults_to_patchright():
    """Production must not be able to silently use another engine."""
    calls = []

    def _fake(url, source_type, timeout_seconds):
        calls.append((url, source_type, timeout_seconds))
        return "sentinel"

    assert fetch_with_browser("https://example.gov", "HTML", 7, launch_fn=_fake) == "sentinel"
    assert calls == [("https://example.gov", "HTML", 7)]


def test_playwright_is_not_used():
    """The Playwright dependency is gone; Patchright replaced it entirely.

    Checked on the parsed AST rather than the raw text, because the module
    docstring legitimately *discusses* Playwright when explaining why it was
    replaced - only real imports/usages matter.
    """
    import ast

    import fetch.browser_fetcher as browser_fetcher

    tree = ast.parse(open(browser_fetcher.__file__, encoding="utf-8").read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            # "from patchright.sync_api import sync_playwright" -> patchright.sync_api
            imported.add(base)
            for alias in node.names:
                imported.add(f"{base}.{alias.name}")

    for name in imported:
        root = name.split(".")[0]
        assert root != "playwright", f"playwright still imported: {name}"
        if "sync_playwright" in name:
            assert root == "patchright", f"driver imported from {name}"

# ---------------------------------------------------------------------------
# Item 4: one shared browser per sync run + heavy-asset request blocking
# ---------------------------------------------------------------------------
class TestBrowserSessionReuse:
    def test_second_fetch_reuses_the_already_launched_browser(self, monkeypatch):
        sink = _install_fake_patchright(monkeypatch)
        session = BrowserSession()

        res1 = _patchright_fetch("https://example.gov/a", "HTML", 20, browser_session=session)
        res2 = _patchright_fetch("https://example.gov/b", "HTML", 20, browser_session=session)

        assert res1.status == FetchStatus.OK
        assert res2.status == FetchStatus.OK
        # Exactly one browser launch across both fetches.
        assert sum(1 for s in sink if isinstance(s, tuple) and s[0] == "launch") == 1
        # The browser is never closed by individual fetches while the
        # session is still open - only the session's own close() does that.
        assert "browser.close" not in sink
        # But each fetch still gets (and closes) its own fresh context.
        assert sink.count("context.close") == 2

        session.close()
        assert "browser.close" in sink

    def test_launch_ms_is_none_on_reuse_not_on_first_use(self, monkeypatch):
        _install_fake_patchright(monkeypatch)
        session = BrowserSession()

        res1 = _patchright_fetch("https://example.gov/a", "HTML", 20, browser_session=session)
        res2 = _patchright_fetch("https://example.gov/b", "HTML", 20, browser_session=session)

        assert res1.browser_launch_ms is not None
        assert res2.browser_launch_ms is None
        session.close()

    def test_session_close_is_idempotent(self, monkeypatch):
        _install_fake_patchright(monkeypatch)
        session = BrowserSession()
        _patchright_fetch("https://example.gov/a", "HTML", 20, browser_session=session)
        session.close()
        session.close()  # must not raise

    def test_session_never_launched_if_never_used(self, monkeypatch):
        sink = _install_fake_patchright(monkeypatch)
        session = BrowserSession()
        session.close()  # nothing to tear down - must not raise or launch
        assert sink == []

    def test_context_manager_closes_on_exit(self, monkeypatch):
        sink = _install_fake_patchright(monkeypatch)
        with BrowserSession() as session:
            _patchright_fetch("https://example.gov/a", "HTML", 20, browser_session=session)
        assert "browser.close" in sink

    def test_fetch_with_browser_passes_session_through_to_patchright(self, monkeypatch):
        """Only the real default path honours browser_session - a custom
        launch_fn (always a test seam) keeps its existing 3-arg call shape."""
        sink = _install_fake_patchright(monkeypatch)
        session = BrowserSession()

        fetch_with_browser("https://example.gov/a", "HTML", 20, browser_session=session)
        fetch_with_browser("https://example.gov/b", "HTML", 20, browser_session=session)

        assert sum(1 for s in sink if isinstance(s, tuple) and s[0] == "launch") == 1
        session.close()


class TestHeavyAssetBlocking:
    def test_page_route_blocks_images_fonts_media_keeps_the_rest(self, monkeypatch):
        _install_fake_patchright(monkeypatch)
        res = _patchright_fetch("https://example.gov/page", "HTML", 20)
        assert res.status == FetchStatus.OK

        # Recover the fake page that was actually used, via the sink-free
        # route: reinstall and capture the page directly for the handler check.
        from fetch.browser_fetcher import _route_blocking_heavy_assets

        class _Req:
            def __init__(self, resource_type):
                self.resource_type = resource_type

        class _Route:
            def __init__(self, resource_type):
                self.request = _Req(resource_type)
                self.aborted = False
                self.continued = False

            def abort(self):
                self.aborted = True

            def continue_(self):
                self.continued = True

        for blocked_type in ("image", "font", "media"):
            route = _Route(blocked_type)
            _route_blocking_heavy_assets(route)
            assert route.aborted is True
            assert route.continued is False

        for kept_type in ("script", "stylesheet", "xhr", "document", "fetch"):
            route = _Route(kept_type)
            _route_blocking_heavy_assets(route)
            assert route.continued is True
            assert route.aborted is False
