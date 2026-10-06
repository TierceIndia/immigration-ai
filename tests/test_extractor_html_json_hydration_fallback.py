"""Regression test: extractor_html.py must recover real content that only
exists as an HTML-escaped JSON blob inside a hidden <input>, when the normal
DOM walk finds nothing.

Root cause (found investigating SRC-AU-189-MAIN, and SRC-AU-600-MAIN before
it, both producing 0 chunks on a genuinely successful fetch - "Fetch
succeeded and content changed, but extraction produced zero document
chunks"): several immi.homeaffairs.gov.au page templates host an Angular
widget (<ha-streams-root> on the "-MAIN" visa-listing pages) that only
renders its real content into visible <p>/<ul>/<table> DOM via client-side
JS. The same SharePoint CMS serves a *different* response depending on its
own server-side bot-detection (an `ms-isBot` class on <html>): bot-detected
requests get the content server-rendered into real static HTML (confirmed on
SRC-AU-189-POINTS-TABLE and SRC-AU-SKILLSELECT-EOI's real saved captures,
both carrying `class="ms-isBot"`), while non-bot-detected requests get only
the inert JSON blob (confirmed on this file - no `ms-isBot` class, 0 real
<p> tags anywhere, but the real government text IS present, HTML-escaped,
inside a hidden <input>'s value attribute).

We do not attempt to influence that server-side classification - that would
be fingerprint evasion. Instead, when the normal main-content walk finds
nothing, extract_html() now falls back to reading the exact same real text
straight out of the JSON the server already sent, which is not fabrication:
it is the real, verbatim government content, just structurally relocated.

Uses a real saved fetch of the production page
(tests/fixtures/real_189_main_json_hydration.html, captured via the same
Patchright browser_fetcher.py path used in production), not a synthetic
HTML string, so this test fails the way production did if the fix
regresses, and two tests use a synthetic second JSON shape
(a {content:[{text,block}]} tab/card array, the other shape confirmed in
this investigation) to prove the fallback is schema-agnostic, not hardcoded
to this one page's field names.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch.extractor_html import extract_html

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "real_189_main_json_hydration.html"
URL = "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189"


def _load():
    return FIXTURE.read_bytes()


class TestJsonHydrationFallbackOnRealCapture:
    def test_real_189_main_page_produces_nonzero_chunks(self):
        result = extract_html(_load(), URL)
        assert len(result.chunks) > 0, (
            "extract_html produced 0 chunks for a page whose real content "
            "exists but only inside a hidden-input JSON blob - the exact "
            "SRC-AU-189-MAIN production bug"
        )

    def test_title_is_recovered(self):
        result = extract_html(_load(), URL)
        assert result.title == "Skilled Independent visa (subclass 189)"

    def test_real_eligibility_text_is_recovered_verbatim(self):
        result = extract_html(_load(), URL)
        texts = [c.raw_text for c in result.chunks]
        assert any("See the relevant stream" in t for t in texts)
        assert any(
            "invited workers" in t and "Hong Kong or British National" in t
            for t in texts
        )

    def test_no_fabricated_or_truncated_content(self):
        # Every chunk's text must be a byte-for-byte match of a real
        # escaped-HTML fragment from the JSON blob - never longer, never
        # paraphrased.
        result = extract_html(_load(), URL)
        raw_html = _load().decode("utf-8")
        for c in result.chunks:
            assert c.raw_text in raw_html.replace("\\u003c", "<").replace(
                "&quot;", '"'
            ) or c.raw_text in raw_html


class TestJsonHydrationFallbackDoesNotInterfereWithRealDom:
    """The fallback must only ever fire when the normal DOM walk finds
    nothing - it must never run (or duplicate content) on a page whose
    content already rendered normally."""

    def test_page_with_real_dom_content_ignores_json_blob_entirely(self):
        html = b"""
        <html><body><main>
            <h1>Real Page</h1>
            <p>Real rendered paragraph.</p>
            <input type="hidden" value='{"title": "Should never appear", "description": "<p>Should never appear either</p>"}'>
        </main></body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 2
        texts = [c.raw_text for c in result.chunks]
        assert "Should never appear" not in " ".join(texts)
        assert "Should never appear either" not in " ".join(texts)


class TestJsonHydrationFallbackIsSchemaAgnostic:
    """Confirmed two different JSON shapes across page templates in this
    investigation. The fallback must handle both without hardcoding either
    shape's field names."""

    def test_flat_field_shape(self):
        html = b"""
        <html><body><main>
            <input type="hidden" value='{"title": "Visitor visa", "description": "&lt;p&gt;For tourists.&lt;/p&gt;", "eligibility": "&lt;ul&gt;&lt;li&gt;be a genuine visitor&lt;/li&gt;&lt;/ul&gt;"}'>
        </main></body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 2
        types = {c.chunk_type for c in result.chunks}
        assert types == {"PARAGRAPH", "LIST"}
        assert any("For tourists." in c.raw_text for c in result.chunks)
        assert any("be a genuine visitor" in c.raw_text for c in result.chunks)

    def test_labelled_card_array_shape(self):
        html = b"""
        <html><body><main>
            <input type="hidden" value='{"content": [
                {"id": -1, "text": "Overview", "block": "&lt;p&gt;Under the SkillSelect model.&lt;/p&gt;"},
                {"id": 0, "text": "Next steps", "block": "&lt;p&gt;Submit your EOI.&lt;/p&gt;"}
            ]}'>
        </main></body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 2
        assert result.chunks[0].heading_path == "Overview"
        assert "Under the SkillSelect model." in result.chunks[0].raw_text
        assert result.chunks[1].heading_path == "Next steps"
        assert "Submit your EOI." in result.chunks[1].raw_text

    def test_non_html_strings_in_json_are_not_extracted_as_chunks(self):
        """A JSON blob full of plain config strings (ids, flags, labels with
        no markup) must not leak into chunks - only real HTML-fragment
        string values count."""
        html = b"""
        <html><body><main>
            <input type="hidden" value='{"id": "189-63", "enabled": true, "label": "Not a fragment", "count": 3}'>
        </main></body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 0

    def test_malformed_json_in_hidden_input_does_not_crash(self):
        html = b"""
        <html><body><main>
            <input type="hidden" value='{not valid json at all'>
        </main></body></html>
        """
        result = extract_html(html, "https://example.invalid/test")
        assert len(result.chunks) == 0
