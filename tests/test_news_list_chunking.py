"""NEWS_LIST sources: one chunk per announcement, not one chunk per page.

A "latest updates" page is a list of dated items, each its own announcement.
Chunked as prose it becomes one giant blob: a change to item 3 is
indistinguishable from a rewrite of the whole page, and a requirement
extracted from it cannot say which announcement it came from. These tests pin
the item-level parse, the fields each item keeps (date, link), the fallback
when a page turns out not to be a listing, and the API that lets an admin say
which kind of page a source is.

No network: the HTML is the local fixture server's own news page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from db.connection import get_session_factory  # noqa: E402
from db.models import Base  # noqa: E402
from fetch.extractor_html import extract_news_list  # noqa: E402
from tests.fixtures.fixture_server import NEWS_HTML, STATIC_HTML  # noqa: E402

URL = "https://immi.homeaffairs.gov.au/news-media/archive"


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    s = get_session_factory(engine)()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------
class TestItemLevelChunks:
    def test_each_announcement_is_its_own_chunk(self):
        result = extract_news_list(NEWS_HTML, URL)

        assert len(result.chunks) == 2, [c.raw_text for c in result.chunks]
        assert [c.chunk_type for c in result.chunks] == ["NEWS_ITEM", "NEWS_ITEM"]
        assert [c.order_index for c in result.chunks] == [1, 2]

    def test_an_item_keeps_its_own_date_and_link(self):
        first = extract_news_list(NEWS_HTML, URL).chunks[0]

        assert "Points test updated" in first.raw_text
        assert "Published: 2026-10-01" in first.raw_text
        assert "The points test threshold changed" in first.raw_text
        assert "Link: https://immi.homeaffairs.gov.au/news/points-change" in first.raw_text

    def test_a_relative_link_is_resolved_against_the_source_url(self):
        """The stored link has to be followable on its own - a bare
        "/news/points-change" is not."""
        first = extract_news_list(NEWS_HTML, URL).chunks[0]
        assert "Link: https://immi.homeaffairs.gov.au/news/points-change" in first.raw_text

    def test_an_absolute_link_is_left_alone(self):
        html = (
            b"<html><body><main><h1>Updates</h1><ul class='news-list'>"
            b"<li><a href='https://www.legislation.gov.au/F2026L00001'>New instrument</a>"
            b"<time datetime='2026-10-02'>2 October 2026</time>"
            b"<p>A legislative instrument was registered.</p></li>"
            b"</ul></main></body></html>"
        )
        chunk = extract_news_list(html, URL).chunks[0]
        assert "Link: https://www.legislation.gov.au/F2026L00001" in chunk.raw_text

    def test_the_heading_path_names_the_page_and_the_item(self):
        chunks = extract_news_list(NEWS_HTML, URL).chunks
        assert chunks[0].heading_path == "Latest updates > Points test updated"
        assert chunks[1].heading_path == "Latest updates > Occupation list revised"

    def test_the_items_do_not_bleed_into_each_other(self):
        """The whole point: item 1's text must not contain item 2's."""
        first, second = extract_news_list(NEWS_HTML, URL).chunks
        assert "Occupation list" not in first.raw_text
        assert "Points test" not in second.raw_text

    def test_an_item_with_no_date_is_still_an_item(self):
        html = (
            b"<html><body><main><h1>Updates</h1><ul class='news-list'>"
            b"<li><a href='/a'>Something changed</a><p>Details of the change.</p></li>"
            b"</ul></main></body></html>"
        )
        chunk = extract_news_list(html, URL).chunks[0]
        assert "Something changed" in chunk.raw_text
        assert "Published:" not in chunk.raw_text

    def test_a_date_in_a_class_is_read_when_there_is_no_time_tag(self):
        html = (
            b"<html><body><main><h1>Updates</h1><ul class='news-list'>"
            b"<li><a href='/a'>Something changed</a>"
            b"<span class='publish-date'>3 October 2026</span>"
            b"<p>Details of the change.</p></li>"
            b"</ul></main></body></html>"
        )
        chunk = extract_news_list(html, URL).chunks[0]
        assert "Published: 3 October 2026" in chunk.raw_text


class TestFallbackWhenItIsNotAListing:
    def test_a_prose_page_labelled_news_list_still_produces_chunks(self):
        """Mislabelling a source must degrade to the ordinary heading walk, not
        report a page with content as empty."""
        result = extract_news_list(STATIC_HTML, URL)

        assert result.chunks, "a prose page must not come back with zero chunks"
        kinds = {c.chunk_type for c in result.chunks}
        assert "NEWS_ITEM" not in kinds
        assert any("under 45 years" in c.raw_text for c in result.chunks)

    def test_an_empty_listing_falls_back_rather_than_inventing_items(self):
        html = b"<html><body><main><h1>Updates</h1><ul class='news-list'></ul>" \
               b"<p>There are no updates at this time.</p></main></body></html>"
        result = extract_news_list(html, URL)

        assert all(c.chunk_type != "NEWS_ITEM" for c in result.chunks)
        assert any("no updates" in c.raw_text for c in result.chunks)


# ---------------------------------------------------------------------------
# The sync path
# ---------------------------------------------------------------------------
class TestSyncStoresItemChunks:
    def test_a_news_list_source_stores_one_chunk_per_item(self, session, monkeypatch):
        """End to end through fetch_document_and_store: the source's
        content_kind decides how its bytes are chunked."""
        from db.models import (
            Country, DocumentChunk, GovernmentSource, ImmigrationProgram,
        )
        from fetch.fetcher import FetchResult, FetchStatus
        from utils.fetch_document import fetch_document_and_store

        session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        session.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        session.add(GovernmentSource(
            source_id="SRC-AU-NEWS", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://immi.homeaffairs.gov.au/news-media/archive",
            source_type="HTML", priority="SECONDARY", monitoring_method="WEB",
            crawl_frequency="DAILY", status="ACTIVE", content_kind="NEWS_LIST",
            extract_requirements=False,
        ))
        session.commit()

        monkeypatch.setattr(
            "fetch.backend.fetch",
            lambda url, **kw: FetchResult(
                url=url, status=FetchStatus.OK, http_status=200,
                content_type="text/html", raw_bytes=NEWS_HTML, duration_ms=5,
                error=None, acquisition="playwright_page",
            ),
        )

        source = session.get(GovernmentSource, "SRC-AU-NEWS")
        outcome = fetch_document_and_store(session, source)
        session.commit()

        assert outcome.status == "OK"
        chunks = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == outcome.document_id)
            .order_by(DocumentChunk.order_index)
            .all()
        )
        assert [c.chunk_type for c in chunks] == ["NEWS_ITEM", "NEWS_ITEM"]
        assert "Points test updated" in chunks[0].raw_text
        assert "Occupation list revised" in chunks[1].raw_text
        assert chunks[0].heading_path == "Latest updates > Points test updated"

    def test_a_page_source_is_unaffected(self, session, monkeypatch):
        from db.models import (
            Country, DocumentChunk, GovernmentSource, ImmigrationProgram,
        )
        from fetch.fetcher import FetchResult, FetchStatus
        from utils.fetch_document import fetch_document_and_store

        session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        session.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        session.add(GovernmentSource(
            source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://immi.homeaffairs.gov.au/visas/189",
            source_type="HTML", priority="PRIMARY", monitoring_method="WEB",
            crawl_frequency="DAILY", status="ACTIVE",
            extract_requirements=False,
        ))
        session.commit()

        monkeypatch.setattr(
            "fetch.backend.fetch",
            lambda url, **kw: FetchResult(
                url=url, status=FetchStatus.OK, http_status=200,
                content_type="text/html", raw_bytes=STATIC_HTML, duration_ms=5,
                error=None, acquisition="playwright_page",
            ),
        )

        source = session.get(GovernmentSource, "SRC-AU-189")
        outcome = fetch_document_and_store(session, source)
        session.commit()

        chunks = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == outcome.document_id)
            .all()
        )
        assert chunks
        assert all(c.chunk_type != "NEWS_ITEM" for c in chunks)
        assert source.content_kind == "PAGE", "the default must not need setting"


# ---------------------------------------------------------------------------
# The admin API
# ---------------------------------------------------------------------------
class TestContentKindOverTheApi:
    def test_the_normaliser_accepts_either_casing(self):
        from api.routes.admin_sources import _normalise_content_kind

        assert _normalise_content_kind("news_list") == "NEWS_LIST"
        assert _normalise_content_kind(" page ") == "PAGE"

    def test_an_unknown_kind_is_rejected_with_the_allowed_values(self):
        from api.routes.admin_sources import _normalise_content_kind

        with pytest.raises(ValueError) as excinfo:
            _normalise_content_kind("BLOG")
        assert "NEWS_LIST" in str(excinfo.value)
        assert "PAGE" in str(excinfo.value)

    def test_create_and_update_requests_validate_it(self):
        from pydantic import ValidationError

        from api.routes.admin_sources import SourceCreateRequest, SourceUpdateRequest

        created = SourceCreateRequest(
            source_id="SRC-AU-NEWS", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://immi.homeaffairs.gov.au/news", content_kind="news_list",
        )
        assert created.content_kind == "NEWS_LIST"

        # Omitted entirely: the ordinary page behaviour, unchanged.
        assert SourceUpdateRequest().content_kind is None
        assert SourceUpdateRequest(content_kind="NEWS_LIST").content_kind == "NEWS_LIST"

        with pytest.raises(ValidationError):
            SourceUpdateRequest(content_kind="SOMETHING_ELSE")

    def test_the_response_model_reports_it(self):
        from api.routes.admin_sources import _source_to_response
        from db.models import GovernmentSource

        source = GovernmentSource(
            source_id="SRC-AU-NEWS", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://immi.homeaffairs.gov.au/news", source_type="HTML",
            priority="SECONDARY", monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE", extract_requirements=True, content_kind="NEWS_LIST",
        )
        assert _source_to_response(source).content_kind == "NEWS_LIST"
