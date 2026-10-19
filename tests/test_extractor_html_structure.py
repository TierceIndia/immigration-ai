"""Heading-aware chunking: what makes a piece of evidence traceable.

Every chunk carries the heading chain it was found under ("h1 > h2 > h3"), so
a requirement can later be shown with the section it came from rather than a
bare sentence. These tests pin the chain itself - nesting, trimming when the
document climbs back up a level, skipped levels, inheritance by lists and
tables, the order index, and the bound that keeps the chain inside the
database column.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch.extractor_html import (  # noqa: E402
    HEADING_PATH_SEPARATOR,
    MAX_HEADING_PATH_CHARS,
    TRUNCATION_MARKER,
    extract_html,
)

URL = "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189"


def _chunks(html: str):
    return extract_html(html.encode("utf-8"), URL).chunks


def _by_text(chunks, needle: str):
    matches = [c for c in chunks if needle in c.raw_text]
    assert matches, f"no chunk containing {needle!r}; got {[c.raw_text[:40] for c in chunks]}"
    return matches[0]


# ---------------------------------------------------------------------------
# The heading chain
# ---------------------------------------------------------------------------
class TestHeadingPath:
    def test_three_levels_nest_into_one_path(self):
        chunks = _chunks(
            "<html><body><main>"
            "<h1>Skilled Independent 189</h1>"
            "<h2>Eligibility</h2>"
            "<h3>Age</h3>"
            "<p>You must be under 45 years of age at the time of invitation.</p>"
            "</main></body></html>"
        )
        para = _by_text(chunks, "under 45 years")
        assert para.chunk_type == "PARAGRAPH"
        assert para.heading_path == "Skilled Independent 189 > Eligibility > Age"

    def test_climbing_back_up_a_level_drops_the_deeper_heading(self):
        """An h2 after an h3 must not inherit that h3 - the sibling section is
        not inside it."""
        chunks = _chunks(
            "<html><body><main>"
            "<h1>Skilled Independent 189</h1>"
            "<h2>Eligibility</h2>"
            "<h3>Age</h3>"
            "<p>You must be under 45 years of age.</p>"
            "<h2>How to apply</h2>"
            "<p>You must be invited to apply.</p>"
            "</main></body></html>"
        )
        assert _by_text(chunks, "under 45").heading_path == (
            "Skilled Independent 189 > Eligibility > Age"
        )
        assert _by_text(chunks, "invited to apply").heading_path == (
            "Skilled Independent 189 > How to apply"
        )

    def test_a_heading_chunk_includes_its_own_text_in_its_path(self):
        chunks = _chunks(
            "<html><body><main><h1>Eligibility</h1><h2>Age</h2>"
            "<p>Under 45 years of age.</p></main></body></html>"
        )
        heading = [c for c in chunks if c.chunk_type == "HEADING" and c.raw_text == "Age"][0]
        assert heading.heading_path == "Eligibility > Age"

    def test_a_skipped_level_does_not_invent_an_empty_segment(self):
        """Real pages jump h1 -> h3. The path must stay readable, with no
        empty "a >  > b" segment."""
        chunks = _chunks(
            "<html><body><main><h1>Visa</h1><h3>Health</h3>"
            "<p>You must meet the health requirement.</p></main></body></html>"
        )
        path = _by_text(chunks, "health requirement").heading_path
        assert path == "Visa > Health"
        assert "  " not in path
        assert ">  >" not in path

    def test_content_before_any_heading_has_an_empty_path_not_a_fake_one(self):
        chunks = _chunks(
            "<html><body><main><p>This page explains the visa.</p>"
            "<h1>Eligibility</h1><p>You must be under 45.</p></main></body></html>"
        )
        assert _by_text(chunks, "explains the visa").heading_path == ""
        assert _by_text(chunks, "under 45").heading_path == "Eligibility"

    def test_lists_and_tables_inherit_the_current_section(self):
        chunks = _chunks(
            "<html><body><main><h1>Points test</h1><h2>Age</h2>"
            "<table><tr><th>Age</th><th>Points</th></tr>"
            "<tr><td>25-32</td><td>30</td></tr></table>"
            "<h2>English</h2>"
            "<ul><li>Competent English</li><li>Proficient English</li></ul>"
            "</main></body></html>"
        )
        table = [c for c in chunks if c.chunk_type == "TABLE"][0]
        listing = [c for c in chunks if c.chunk_type == "LIST"][0]
        assert table.heading_path == "Points test > Age"
        assert listing.heading_path == "Points test > English"
        assert "25-32 | 30" in table.raw_text
        assert "- Competent English" in listing.raw_text


class TestOrderIndex:
    def test_it_is_document_order_starting_at_one(self):
        chunks = _chunks(
            "<html><body><main><h1>A</h1><p>First paragraph here.</p>"
            "<h2>B</h2><p>Second paragraph here.</p></main></body></html>"
        )
        assert [c.order_index for c in chunks] == [1, 2, 3, 4]
        assert [c.raw_text for c in chunks] == [
            "A", "First paragraph here.", "B", "Second paragraph here.",
        ]


class TestTheBoundOnTheHeadingPath:
    """document_chunks.heading_path is VARCHAR(512); Postgres rejects longer
    values where SQLite silently truncates nothing and accepts them."""

    def test_a_deeply_nested_long_chain_is_bounded_and_marked(self):
        long_heading = "Division 2 of Part 2 of Schedule 2 to the Migration Regulations " * 3
        html = "<html><body><main>"
        for level in range(1, 7):
            html += f"<h{level}>{long_heading} level {level}</h{level}>"
        html += "<p>You must satisfy the criteria in this clause.</p></main></body></html>"

        para = _by_text(_chunks(html), "satisfy the criteria")

        assert len(para.heading_path) <= MAX_HEADING_PATH_CHARS
        assert para.heading_path.startswith(TRUNCATION_MARKER)
        # The deepest heading survives - it carries the specific context.
        assert "level 6" in para.heading_path

    def test_a_single_oversized_heading_is_hard_cut_not_dropped(self):
        html = (
            "<html><body><main>"
            f"<h1>{'x' * (MAX_HEADING_PATH_CHARS * 2)}</h1>"
            "<p>You must meet this requirement.</p></main></body></html>"
        )
        para = _by_text(_chunks(html), "meet this requirement")

        assert len(para.heading_path) <= MAX_HEADING_PATH_CHARS
        assert para.heading_path.startswith(TRUNCATION_MARKER)

    def test_an_ordinary_path_is_untouched(self):
        chunks = _chunks(
            "<html><body><main><h1>Eligibility</h1><h2>Age</h2>"
            "<p>Under 45 years of age.</p></main></body></html>"
        )
        path = _by_text(chunks, "Under 45").heading_path
        assert TRUNCATION_MARKER not in path
        assert path == f"Eligibility{HEADING_PATH_SEPARATOR}Age"


class TestNoiseIsNotEvidence:
    def test_navigation_and_footers_never_become_chunks(self):
        chunks = _chunks(
            "<html><body>"
            "<nav><ul><li>Home</li><li>Visas</li></ul></nav>"
            "<header><p>Australian Government</p></header>"
            "<div class='cookie-banner'><p>We use cookies on this site.</p></div>"
            "<main><h1>Eligibility</h1><p>You must be under 45 years of age.</p></main>"
            "<footer><p>Copyright Commonwealth of Australia</p></footer>"
            "</body></html>"
        )
        texts = " ".join(c.raw_text for c in chunks)
        assert "under 45 years" in texts
        for noise in ("Home", "Australian Government", "cookies", "Copyright"):
            assert noise not in texts, f"{noise!r} leaked into the chunks"
