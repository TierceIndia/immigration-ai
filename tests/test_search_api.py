"""The search endpoint (Phase 3b).

The endpoint promises two things:

* It answers the question. A search that returns nothing because
  embeddings are switched off would be a feature that only works on the
  machine where somebody remembered to switch them on.
* It says how it answered. A keyword result dressed up as a semantic
  result would send an admin looking for the meaning of a passage and
  finding the words instead.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.main import app  # noqa: E402
from db.connection import get_session_factory  # noqa: E402
from db.models import (  # noqa: E402
    Base, Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, DocumentChunk,
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def client(engine):
    app.state.db_engine = engine
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def user_headers(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "searcher@test.com", "password": "search123"},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "searcher@test.com", "password": "search123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def seeded(engine):
    with get_session_factory(engine)() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        s.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        s.merge(GovernmentSource(
            source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.invalid/189", source_type="HTML",
            priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE",
        ))
        s.merge(GovernmentDocument(
            document_id="DOC-AU-189", source_id="SRC-AU-189", country_id="AU",
            program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
            url="https://example.invalid/189", title="Skilled 189 eligibility",
            retrieved_at=datetime.now(timezone.utc), content_hash="abc123",
            current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
        ))
        s.flush()
        s.merge(DocumentVersion(
            document_id="DOC-AU-189", version_number=1, content_hash="abc123",
            retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
            change_type="INITIAL",
        ))
        s.flush()
        version_id = (
            s.query(DocumentVersion)
            .filter_by(document_id="DOC-AU-189", version_number=1)
            .one()
            .version_id
        )
        s.add(DocumentChunk(
            chunk_id=1, document_id="DOC-AU-189", version_id=version_id,
            chunk_type="PARAGRAPH", heading_path="Eligibility criteria > Age",
            order_index=1,
            raw_text="Applicants must be under 45 years of age at the time of invitation.",
            page_number=None,
        ))
        s.add(DocumentChunk(
            chunk_id=2, document_id="DOC-AU-189", version_id=version_id,
            chunk_type="PARAGRAPH", heading_path="Eligibility criteria > Points",
            order_index=2,
            raw_text="You must score at least 65 points on the points test.",
            page_number=None,
        ))
        s.commit()
    return version_id


class TestKeywordSearch:
    def test_it_finds_a_chunk_by_its_words(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "45 years of age"},
            headers=user_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["chunk_id"] == 1
        assert "45 years of age" in data["results"][0]["snippet"]

    def test_it_reports_that_it_used_keyword_search(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "45 years of age"},
            headers=user_headers,
        )
        assert resp.json()["mode"] == "keyword"

    def test_it_returns_nothing_for_a_query_that_matches_nothing(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "quantum entanglement"},
            headers=user_headers,
        )
        assert resp.json()["total"] == 0

    def test_the_result_carries_the_source_anchor(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "45 years of age"},
            headers=user_headers,
        )
        assert resp.json()["results"][0]["citation"] == "Eligibility criteria > Age"

    def test_it_can_be_narrowed_to_one_program(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "45 years of age", "program_id": "SKILLED_190"},
            headers=user_headers,
        )
        assert resp.json()["total"] == 0

    def test_it_respects_the_limit(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "must", "limit": 1},
            headers=user_headers,
        )
        assert resp.json()["total"] == 1


class TestModeSelection:
    def test_semantic_is_refused_when_embeddings_are_off(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "age limit", "mode": "semantic"},
            headers=user_headers,
        )
        assert resp.status_code == 409
        assert "Embeddings are off" in resp.json()["detail"]

    def test_auto_falls_back_to_keyword_when_embeddings_are_off(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "45 years of age", "mode": "auto"},
            headers=user_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "keyword"

    def test_an_unknown_mode_is_refused(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "age", "mode": "telepathy"},
            headers=user_headers,
        )
        assert resp.status_code == 400

    def test_it_says_semantic_is_not_available(self, client, seeded, user_headers):
        resp = client.post(
            "/api/v1/search",
            json={"query": "45 years of age"},
            headers=user_headers,
        )
        assert resp.json()["semantic_available"] is False


class TestAccess:
    def test_an_unauthenticated_search_is_refused(self, client, seeded):
        resp = client.post("/api/v1/search", json={"query": "45 years of age"})
        assert resp.status_code == 401

    def test_an_empty_query_is_refused(self, client, seeded, user_headers):
        resp = client.post("/api/v1/search", json={"query": "   "}, headers=user_headers)
        assert resp.status_code == 400
