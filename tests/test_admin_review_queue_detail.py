"""Tests for the enriched GET /api/v1/admin/requirements/review-queue (Part 1d).

Before this, the review queue returned a flat requirement_id/category/name/
field/operator/value/unit/mandatory/status/current_version/extracted_at
shape with no description, confidence, evidence text, source document, or
indication of what a CONFLICT requirement is conflicting with. This adds all
of those to each review-queue item without touching the separate, thinner
RequirementSummary shape used by approve/reject/mark-needs-review/get.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, ExtractionRun, Requirement, RequirementEvidence,
)
from db.connection import get_session_factory
from api.main import app


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
def admin_headers(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "review-queue-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(client.app.state.db_engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "review-queue-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "review-queue-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed_chain(session):
    """Country -> Program -> Source -> Document -> DocumentVersion -> ExtractionRun."""
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.merge(GovernmentSource(
        source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs",
        url="https://example.gov.au/189", source_type="HTML",
        priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.merge(GovernmentDocument(
        document_id="DOC-AU-189", source_id="SRC-AU-189", country_id="AU",
        program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
        url="https://example.gov.au/189", title="Skilled 189 eligibility",
        retrieved_at=datetime.now(timezone.utc), content_hash="abc123",
        current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
    ))
    session.flush()
    session.merge(DocumentVersion(
        document_id="DOC-AU-189", version_number=1, content_hash="abc123",
        retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion)
        .filter_by(document_id="DOC-AU-189", version_number=1)
        .one()
        .version_id
    )
    run = ExtractionRun(
        run_id="RUN-REVIEWQ-1", document_id="DOC-AU-189", document_version=1,
        document_version_id=version_id, model="mock", prompt_version="v1",
        status="COMPLETED",
    )
    session.add(run)
    session.flush()
    session.commit()
    return version_id


def _make_requirement(session, requirement_id, semantic_key, status, value, run_id, version_id):
    req = Requirement(
        requirement_id=requirement_id,
        semantic_key=semantic_key,
        program_id="SKILLED_189",
        country_id="AU",
        category="AGE",
        name="Age requirement",
        description="Applicants must meet the age requirement.",
        field="applicant.age",
        operator="LT",
        value_json=json.dumps(value),
        unit="years",
        mandatory=1,
        confidence=0.92,
        status=status,
        current_version=1,
        extraction_run_id=run_id,
        document_version_id=version_id,
    )
    session.add(req)
    session.flush()
    session.add(RequirementEvidence(
        requirement_id=requirement_id,
        document_id="DOC-AU-189",
        document_version=1,
        chunk_id=1,
        evidence_text="Applicants must be under 45 years of age.",
        evidence_type="DIRECT_TEXT",
        extracted_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return req


def _seed_second_source_same_program(session):
    """A second source/document/version/run sharing SKILLED_189 with the
    first (_seed_chain) - the exact scenario the provenance join must get
    right: 12 of the real 14 seeded sources share a program with another
    source, so "filter by program_id" and "filter by source" are NOT
    interchangeable."""
    session.merge(GovernmentSource(
        source_id="SRC-AU-189-POINTS", country_id="AU", program_id="SKILLED_189",
        visa_category="SKILLED", authority="Home Affairs",
        url="https://example.gov.au/189-points", source_type="HTML",
        priority="SECONDARY", monitoring_method="WEB", crawl_frequency="DAILY",
        status="ACTIVE",
    ))
    session.merge(GovernmentDocument(
        document_id="DOC-AU-189-POINTS", source_id="SRC-AU-189-POINTS", country_id="AU",
        program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
        url="https://example.gov.au/189-points", title="Skilled 189 points table",
        retrieved_at=datetime.now(timezone.utc), content_hash="def456",
        current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
    ))
    session.flush()
    session.merge(DocumentVersion(
        document_id="DOC-AU-189-POINTS", version_number=1, content_hash="def456",
        retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
        change_type="INITIAL",
    ))
    session.flush()
    version_id = (
        session.query(DocumentVersion)
        .filter_by(document_id="DOC-AU-189-POINTS", version_number=1)
        .one()
        .version_id
    )
    run = ExtractionRun(
        run_id="RUN-REVIEWQ-2", document_id="DOC-AU-189-POINTS", document_version=1,
        document_version_id=version_id, model="mock", prompt_version="v1",
        status="COMPLETED",
    )
    session.add(run)
    session.flush()
    session.commit()
    return version_id


class TestExactProvenanceJoinAcrossSiblingSources:
    """Two sources sharing one program must each surface only their own
    requirements - proves the join is Requirement.document_version_id ->
    DocumentVersion.document_id -> GovernmentDocument.source_id, not an
    approximation by program_id."""

    def test_each_sibling_source_lists_only_its_own_requirement(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            version_id_1 = _seed_chain(s)
            version_id_2 = _seed_second_source_same_program(s)
            _make_requirement(s, "REQ-SRC1", "semkey-src1", "NEEDS_REVIEW", 45, "RUN-REVIEWQ-1", version_id_1)
            _make_requirement(s, "REQ-SRC2", "semkey-src2", "NEEDS_REVIEW", 50, "RUN-REVIEWQ-2", version_id_2)

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=NEEDS_REVIEW&program_id=SKILLED_189",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        items = {i["requirement_id"]: i for i in resp.json()["requirements"]}
        assert len(items) == 2

        # Both share program_id=SKILLED_189, but each resolves to its OWN
        # source via the document/version chain, never the sibling's.
        assert items["REQ-SRC1"]["source"]["source_id"] == "SRC-AU-189"
        assert items["REQ-SRC2"]["source"]["source_id"] == "SRC-AU-189-POINTS"

        # Filtering client-side by exact source_id (what Source Detail now
        # does) must isolate exactly one requirement per source, not both.
        for_src1 = [i for i in items.values() if i["source"]["source_id"] == "SRC-AU-189"]
        for_src2 = [i for i in items.values() if i["source"]["source_id"] == "SRC-AU-189-POINTS"]
        assert [i["requirement_id"] for i in for_src1] == ["REQ-SRC1"]
        assert [i["requirement_id"] for i in for_src2] == ["REQ-SRC2"]


class TestReviewQueueDetail:
    def test_needs_review_item_has_full_detail_and_no_previous_value(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            version_id = _seed_chain(s)
            _make_requirement(s, "REQ-AGE-1", "semkey-age-1", "NEEDS_REVIEW", 45, "RUN-REVIEWQ-1", version_id)

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=NEEDS_REVIEW",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        items = resp.json()["requirements"]
        assert len(items) == 1
        item = items[0]

        assert item["description"] == "Applicants must meet the age requirement."
        assert item["confidence"] == pytest.approx(0.92)
        assert item["value"] == 45
        assert item["operator"] == "LT"
        assert item["mandatory"] is True

        assert len(item["evidence"]) == 1
        assert item["evidence"][0]["evidence_text"] == "Applicants must be under 45 years of age."

        assert item["document_version"] == 1
        assert item["source"]["source_id"] == "SRC-AU-189"
        assert item["source"]["authority"] == "Home Affairs"
        assert item["source"]["document_id"] == "DOC-AU-189"

        assert item["previous_value"] is None

    def test_conflict_item_shows_the_previous_value_it_conflicts_with(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            version_id = _seed_chain(s)
            # The old, already-approved value...
            _make_requirement(s, "REQ-AGE-OLD", "semkey-age-conflict", "CONFLICT", 45, "RUN-REVIEWQ-1", version_id)
            # ...and a newer extraction that disagrees with it.
            _make_requirement(s, "REQ-AGE-NEW", "semkey-age-conflict", "CONFLICT", 50, "RUN-REVIEWQ-1", version_id)

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=CONFLICT",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        items = {i["requirement_id"]: i for i in resp.json()["requirements"]}
        assert len(items) == 2

        new_item = items["REQ-AGE-NEW"]
        assert new_item["previous_value"]["requirement_id"] == "REQ-AGE-OLD"
        assert new_item["previous_value"]["value"] == 45

        old_item = items["REQ-AGE-OLD"]
        assert old_item["previous_value"]["requirement_id"] == "REQ-AGE-NEW"
        assert old_item["previous_value"]["value"] == 50

    def test_requirement_with_no_document_version_has_null_source(self, client, admin_headers, engine):
        with get_session_factory(engine)() as s:
            version_id = _seed_chain(s)
            req = _make_requirement(s, "REQ-AGE-NODOC", "semkey-age-nodoc", "NEEDS_REVIEW", 45, "RUN-REVIEWQ-1", version_id)
            req.document_version_id = None
            s.commit()

        resp = client.get(
            "/api/v1/admin/requirements/review-queue?status=NEEDS_REVIEW",
            headers=admin_headers,
        )
        item = resp.json()["requirements"][0]
        assert item["source"] is None
        assert item["document_version"] is None

    def test_non_admin_gets_403(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "review-queue-plain@test.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "review-queue-plain@test.com", "password": "pass123"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        queue_resp = client.get("/api/v1/admin/requirements/review-queue", headers=headers)
        assert queue_resp.status_code == 403
