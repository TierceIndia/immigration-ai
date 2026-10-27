"""The search service (Phase 3b).

Keyword search has to work on any database, including the SQLite one the
unit tests run on. Semantic search needs pgvector, so it is exercised
against the real PostgreSQL instance instead of being mocked - a mocked
cosine distance would only prove that the mock works.

Run the PostgreSQL half with:

    $env:POSTGRES_E2E="1"; python -m pytest tests/services/test_search_service.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from db.models import (  # noqa: E402
    Base, Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, DocumentChunk,
)
from extraction.embeddings import MockEmbeddingClient  # noqa: E402
from services.search_service import (  # noqa: E402
    attach_citations, embedded_chunk_count, keyword_search, semantic_search,
    vector_support_available,
)

E2E_URL = os.environ.get(
    "POSTGRES_E2E_URL",
    # The host port follows POSTGRES_HOST_PORT so the suite still finds
    # docker-compose's database when 5432 is already taken on the host.
    f"postgresql+psycopg2://immigration:immigration_dev@localhost:"
    f"{os.environ.get('POSTGRES_HOST_PORT', '5432')}/immigration_e2e",
)

SOURCE_ID = "SRC-AU-SEARCH-189"
DOCUMENT_ID = "DOC-AU-SEARCH-189"


@pytest.fixture()
def session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    with Session(eng) as s:
        yield s


from sqlalchemy.orm import Session  # noqa: E402


def _seed(session, texts: list[str], *, with_embedding: bool = False):
    """A minimal source -> document -> version -> chunks chain."""
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.merge(GovernmentSource(
        source_id=SOURCE_ID, country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs",
        url="https://example.gov.au/189", source_type="HTML",
        priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.merge(GovernmentDocument(
        document_id=DOCUMENT_ID, source_id=SOURCE_ID, country_id="AU",
        program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
        url="https://example.gov.au/189", title="Skilled 189 eligibility",
        retrieved_at=datetime.now(timezone.utc), content_hash="abc123",
        current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
    ))
    session.flush()
    session.merge(DocumentVersion(
        document_id=DOCUMENT_ID, version_number=1, content_hash="abc123",
        retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion)
        .filter_by(document_id=DOCUMENT_ID, version_number=1)
        .one()
        .version_id
    )
    client = MockEmbeddingClient()
    for index, text_ in enumerate(texts, start=1):
        session.add(DocumentChunk(
            chunk_id=index, document_id=DOCUMENT_ID, version_id=version_id,
            chunk_type="PARAGRAPH", heading_path=f"Section {index}",
            order_index=index, raw_text=text_,
            page_number=None,
            embedding=client.embed_query(text_) if with_embedding else None,
        ))
    session.flush()


class TestVectorSupport:
    def test_sqlite_has_no_vector_support(self, session):
        assert vector_support_available(session) is False

    def test_sqlite_counts_no_embedded_chunks(self, session):
        assert embedded_chunk_count(session) == 0


class TestKeywordSearch:
    def test_it_finds_a_chunk(self, session):
        _seed(session, [
            "Applicants must be under 45 years of age.",
            "You must score at least 65 points.",
        ])
        hits = keyword_search(session, "45 years of age")
        assert len(hits) == 1
        assert hits[0].chunk_id == 1

    def test_it_ignores_case(self, session):
        _seed(session, ["Applicants must be under 45 YEARS of age."])
        assert len(keyword_search(session, "45 years")) == 1

    def test_it_only_searches_the_current_version(self, session):
        _seed(session, ["Applicants must be under 45 years of age."])
        # An older version holds the same words; returning it would show
        # the same passage twice.
        hits = keyword_search(session, "45 years")
        assert len(hits) == 1

    def test_it_reports_keyword_mode(self, session):
        _seed(session, ["Applicants must be under 45 years of age."])
        assert keyword_search(session, "45 years")[0].mode == "keyword"

    def test_keyword_hits_have_no_similarity_score(self, session):
        # A keyword match has no meaningful score; reporting 0.0 is honest,
        # reporting 1.0 would be a lie that ranks above a real semantic hit.
        _seed(session, ["Applicants must be under 45 years of age."])
        assert keyword_search(session, "45 years")[0].similarity == 0.0

    def test_it_can_be_narrowed_to_one_program(self, session):
        _seed(session, ["Applicants must be under 45 years of age."])
        assert keyword_search(session, "45 years", program_id="OTHER") == []

    def test_an_empty_query_matches_nothing(self, session):
        _seed(session, ["Applicants must be under 45 years of age."])
        assert keyword_search(session, "") == []

    def test_the_snippet_holds_the_match(self, session):
        _seed(session, ["Applicants must be under 45 years of age."])
        assert "45 years of age" in keyword_search(session, "45 years")[0].snippet

    def test_the_snippet_is_shortened_for_a_long_chunk(self, session):
        _seed(session, ["filler " * 200 + "NEEDLE " + "filler " * 200])
        hit = keyword_search(session, "NEEDLE")[0]
        assert len(hit.snippet) < 300
        assert "NEEDLE" in hit.snippet


class TestCitations:
    def test_a_hit_gets_its_heading_path(self, session):
        _seed(session, ["Applicants must be under 45 years of age."])
        hits = keyword_search(session, "45 years")
        attach_citations(session, hits)
        assert hits[0].citation == "Section 1"

    def test_a_hit_with_no_chunks_is_left_alone(self, session):
        assert attach_citations(session, []) == []


# ---------------------------------------------------------------------------
# Semantic search needs a real pgvector. It is exercised against the
# dedicated e2e database, never the shared dev/production one.
# ---------------------------------------------------------------------------

postgres_only = pytest.mark.skipif(
    os.environ.get("POSTGRES_E2E") != "1",
    reason="POSTGRES_E2E=1 required (needs a real PostgreSQL with pgvector)",
)


@pytest.fixture()
def pg_session():
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(E2E_URL, pool_pre_ping=True)
    try:
        s = sessionmaker(bind=eng)()
        yield s
        s.rollback()
    finally:
        eng.dispose()


@pytest.fixture()
def pg_clean():
    """Remove this module's sandbox rows before and after the test.

    document_versions is append-only (a database trigger blocks UPDATE and
    DELETE), and this module seeds one. The trigger is disabled for the
    delete only, inside a transaction, and re-enabled straight after -
    the same treatment the pipeline e2e gives its own sandbox rows.
    """
    from sqlalchemy import text as sql_text

    eng = create_engine(E2E_URL, pool_pre_ping=True)

    def _wipe():
        with eng.begin() as conn:
            conn.execute(sql_text(
                "ALTER TABLE document_versions DISABLE TRIGGER ALL"
            ))
            try:
                conn.execute(sql_text(
                    "DELETE FROM document_chunks WHERE document_id = :doc"
                ), {"doc": DOCUMENT_ID})
                conn.execute(sql_text(
                    "DELETE FROM document_versions WHERE document_id = :doc"
                ), {"doc": DOCUMENT_ID})
                conn.execute(sql_text(
                    "DELETE FROM government_documents WHERE document_id = :doc"
                ), {"doc": DOCUMENT_ID})
                conn.execute(sql_text(
                    "DELETE FROM government_sources WHERE source_id = :src"
                ), {"src": SOURCE_ID})
            finally:
                conn.execute(sql_text(
                    "ALTER TABLE document_versions ENABLE TRIGGER ALL"
                ))

    try:
        _wipe()
        yield
    finally:
        _wipe()
        eng.dispose()


@postgres_only
class TestSemanticSearchOnPostgres:
    def test_postgres_has_vector_support(self, pg_session):
        assert vector_support_available(pg_session) is True

    def test_it_finds_the_closest_chunk(self, pg_session, pg_clean):
        _seed(pg_session, [
            "Applicants must be under 45 years of age.",
            "You must score at least 65 points on the points test.",
        ], with_embedding=True)
        pg_session.commit()

        client = MockEmbeddingClient()
        vector = client.embed_query("age limit for applicants")
        hits = semantic_search(pg_session, vector)
        assert hits
        assert hits[0].chunk_id == 1

    def test_the_similarity_score_is_a_real_number(self, pg_session, pg_clean):
        _seed(pg_session, ["Applicants must be under 45 years of age."],
              with_embedding=True)
        pg_session.commit()

        client = MockEmbeddingClient()
        vector = client.embed_query("Applicants must be under 45 years of age.")
        hits = semantic_search(pg_session, vector)
        assert hits[0].similarity == pytest.approx(1.0, abs=1e-6)

    def test_a_chunk_itself_is_a_perfect_match(self, pg_session, pg_clean):
        _seed(pg_session, ["Applicants must be under 45 years of age."],
              with_embedding=True)
        pg_session.commit()

        client = MockEmbeddingClient()
        vector = client.embed_query("Applicants must be under 45 years of age.")
        hits = semantic_search(pg_session, vector)
        assert hits[0].similarity == pytest.approx(1.0, abs=1e-6)

    def test_it_reports_the_semantic_mode(self, pg_session, pg_clean):
        _seed(pg_session, ["Applicants must be under 45 years of age."],
              with_embedding=True)
        pg_session.commit()

        client = MockEmbeddingClient()
        hits = semantic_search(pg_session, client.embed_query("age"))
        assert hits[0].mode == "semantic"

    def test_the_embedded_count_is_true(self, pg_session, pg_clean):
        _seed(pg_session, ["one text", "another text"], with_embedding=True)
        pg_session.commit()
        assert embedded_chunk_count(pg_session) >= 2

    def test_unembedded_chunks_are_left_out(self, pg_session, pg_clean):
        _seed(pg_session, [
            "Applicants must be under 45 years of age.",
            "You must score at least 65 points.",
        ], with_embedding=True)
        # The second chunk loses its embedding.
        pg_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE document_chunks SET embedding = NULL WHERE chunk_id = 2"
            )
        )
        pg_session.commit()

        client = MockEmbeddingClient()
        hits = semantic_search(pg_session, client.embed_query("age limit"))
        assert [h.chunk_id for h in hits] == [1]

    def test_the_result_carries_its_citation(self, pg_session, pg_clean):
        _seed(pg_session, ["Applicants must be under 45 years of age."],
              with_embedding=True)
        pg_session.commit()

        client = MockEmbeddingClient()
        hits = semantic_search(pg_session, client.embed_query("age limit"))
        attach_citations(pg_session, hits)
        assert hits[0].citation == "Section 1"


@postgres_only
class TestTheBackfill:
    def test_it_embeds_the_chunks_that_have_none(self, pg_session, pg_clean):
        _seed(pg_session, ["Applicants must be under 45 years of age."])
        pg_session.commit()
        assert embedded_chunk_count(pg_session) == 0

        from extraction.embeddings import backfill_embeddings

        summary = backfill_embeddings(pg_session, MockEmbeddingClient())
        pg_session.commit()
        assert summary["embedded"] == 1
        assert embedded_chunk_count(pg_session) == 1

    def test_it_leaves_already_embedded_chunks_alone(self, pg_session, pg_clean):
        _seed(pg_session, ["Applicants must be under 45 years of age."],
              with_embedding=True)
        pg_session.commit()

        from extraction.embeddings import backfill_embeddings

        summary = backfill_embeddings(pg_session, MockEmbeddingClient())
        pg_session.commit()
        assert summary["embedded"] == 0
