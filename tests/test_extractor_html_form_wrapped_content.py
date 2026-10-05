"""Regression test: extractor_html.py must not delete real content wrapped in a <form>.

Root cause (found investigating a live sync of SRC-AU-600-MAIN, which fetched
successfully via the browser escalation path - real HTTP 200, content genuinely
present - but produced 0 document chunks): immi.homeaffairs.gov.au's Visitor visa
page is a legacy ASP.NET CMS page (<form id="aspnetForm" method="post" ...>)
hosting an embedded Angular content widget. The ENTIRE real page body - the h1,
the eligibility list, everything - sits inside that <form>. extract_html() used
to treat every <form> tag as pure noise and decompose() it, which deletes the
tag AND all its children, wiping out the whole page's content along with it.

This is a different failure mode from the known CRICOS 0-chunk case (genuinely
JS-only content that never lands in captured HTML at all): here the real content
IS present in the fetched HTML, it was just being destroyed by an over-broad
noise-stripping rule.

Fix: form *tags* are unwrapped (removed, keeping their children) instead of
decomposed (removed, deleting their children). Form *controls* (input, button,
select, textarea, label) are unaffected either way, since extract_html only ever
walks headings/p/table/ul/ol - it never extracted form controls as chunks, so
nothing about actual interactive noise (search boxes, subscribe forms) changes.

Uses a real saved fetch of the production page (tests/fixtures/real_600_homeaffairs.html,
captured via the same headed-Chromium path browser_fetcher.py uses in production) -
not a synthetic/simplified HTML string - so this test fails the way production did
if the fix regresses.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch.extractor_html import extract_html

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "real_600_homeaffairs.html"
URL = "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600"


def _load():
    return FIXTURE.read_bytes()


class TestFormWrappedContentIsNotDeleted:
    def test_real_600_page_produces_nonzero_chunks(self):
        result = extract_html(_load(), URL)
        assert len(result.chunks) > 0, (
            "extract_html produced 0 chunks for a page with real, genuinely "
            "fetched content - this is the SRC-AU-600-MAIN production bug"
        )

    def test_title_is_recovered(self):
        result = extract_html(_load(), URL)
        assert result.title == "Visitor visa"

    def test_eligibility_list_content_is_recovered(self):
        result = extract_html(_load(), URL)
        list_chunks = [c for c in result.chunks if c.chunk_type == "LIST"]
        assert any("genuine visitor" in c.raw_text for c in list_chunks), (
            "the real 'Basic Eligibility' list content must survive extraction, "
            "not be silently dropped with the <form> wrapper that contains it"
        )

    def test_heading_hierarchy_is_preserved(self):
        result = extract_html(_load(), URL)
        heading_chunks = [c for c in result.chunks if c.chunk_type == "HEADING"]
        assert any(c.raw_text == "Basic Eligibility" for c in heading_chunks)

    def test_form_tag_itself_does_not_appear_as_a_chunk(self):
        # Unwrapping a form must remove the <form> element itself; only its real
        # content (headings/paragraphs/lists) should surface as chunks.
        result = extract_html(_load(), URL)
        for c in result.chunks:
            assert c.chunk_type in ("HEADING", "PARAGRAPH", "TABLE", "LIST")


class TestFormNoiseStillHandledSafely:
    """A small standalone utility form (e.g. a subscribe/search box) must not
    itself leak into chunks - only real narrative content should ever surface,
    and forms unwrap cleanly even when there's no meaningful content inside."""

    def test_form_with_only_controls_contributes_no_chunks(self):
        html = b"""
        <html><body><main>
            <h1>Real Page</h1>
            <form action="/search"><input name="q"><button>Search</button></form>
            <p>Actual content paragraph.</p>
        </main></body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 2
        assert result.chunks[0].raw_text == "Real Page"
        assert result.chunks[1].raw_text == "Actual content paragraph."

    def test_content_nested_inside_form_wrapper_is_preserved(self):
        html = b"""
        <html><body>
        <form id="aspnetForm" method="post">
            <main>
                <h1>Wrapped Page</h1>
                <p>This paragraph lives inside the postback form wrapper.</p>
                <ul><li>item one</li><li>item two</li></ul>
            </main>
        </form>
        </body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 3
        assert result.chunks[0].chunk_type == "HEADING"
        assert result.chunks[1].raw_text == "This paragraph lives inside the postback form wrapper."
        assert result.chunks[2].chunk_type == "LIST"
