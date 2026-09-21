"""Regression: requirement-evidence lineage must be version-self-consistent.

Production enforces this with the ``fk_evidence_chunk_lineage`` composite FK on
PostgreSQL; a content change triggered an INSERT of an old-version chunk paired
with the new document version_id, aborting the sync (InFailedSqlTransaction is
masked, PipelineRun = FAILED). The fix scopes ``_load_chunk_dicts`` to the exact
``DocumentVersion`` being extracted. This test reproduces on SQLite (FKs off,
so it asserts the invariant explicitly) and would fail on the pre-fix code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource, GovernmentDocument,
    DocumentVersion, RequirementEvidence, ExtractionRun,
)
from db.connection import get_session_factory
from fetcher import FetchResult, FetchStatus
from hashing import sha256_bytes
from utils.fetch_document import fetch_document_and_store, _load_chunk_dicts

FIXTURE_V1 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<h2>Age</h2><p>You must be under 45 years of age.</p>"
    b"</body></html>"
)
FIXTURE_V2 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<h2>Age</h2><p>You must be under 50 years of age.</p>"
    b"</body></html>"
)

SOURCE_ID = "SRC-SCOPE-189"
DOCUMENT_ID = "DOC-SCOPE-189"
PROGRAM_ID = "SKILLED_189"


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
def session(engine):
    s = get_session_factory(engine)()
    yield s
    s.close()


def _seed(session):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(ImmigrationProgram(
        program_id=PROGRAM_ID, country_id="AU", visa_category="SKILLED",
        subclass_code="189", name="Skilled Independent", status="ACTIVE",
    ))
    session.add(GovernmentSource(
        source_id=SOURCE_ID, country_id="AU", program_id=PROGRAM_ID,
        visa_category="189", authority="Home Affairs",
        url="https://immi.homeaffairs.gov.au/189", source_type="WEBPAGE",
        source_subtype="ELIGIBILITY_CRITERIA", priority="PRIMARY",
        monitoring_method="WEB", crawl_frequency="DAILY", status="ACTIVE",
    ))
    session.commit()


def _ok_fetch(payload: bytes):
    def fetch(url, source_type=None, **kw):
        return FetchResult(
            url=url, status=FetchStatus.OK, http_status=200,
            content_type="text/html", raw_bytes=payload,
            duration_ms=5, error=None, error_type=None,
        )
    return fetch


def _evidence_consistent(session) -> list[str]:
    """Return a list of inconsistency descriptions (empty = lineage is sound)."""
    problems = []
    from db.models import DocumentChunk
    pairs = (
        session.query(RequirementEvidence, DocumentChunk)
        .join(DocumentChunk, DocumentChunk.chunk_id == RequirementEvidence.chunk_id)
        .all()
    )
    for ev, chunk in pairs:
        dv = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.version_id == chunk.version_id)
            .one()
        )
        if ev.document_version_id != chunk.version_id:
            problems.append(
                f"evidence {ev.evidence_id}: ev.version_id={ev.document_version_id} "
                f"vs chunk.version_id={chunk.version_id}"
            )
        if ev.document_version != dv.version_number:
            problems.append(
                f"evidence {ev.evidence_id}: ev.version={ev.document_version} "
                f"vs dv.version={dv.version_number}"
            )
    return problems


class TestVersionScopedExtraction:
    def test_load_chunk_dicts_scopes_to_version(self, session):
        _seed(session)
        from db.models import DocumentChunk
        session.add(GovernmentDocument(
            document_id=DOCUMENT_ID, source_id=SOURCE_ID, country_id="AU",
            program_id=PROGRAM_ID, visa_category="189",
            document_type="ELIGIBILITY_CRITERIA", url="u", title="t",
            content_hash="h1", current_version=2,
            retrieved_at=datetime.now(timezone.utc),
            raw_path="p", status="CURRENT",
        ))
        dv1 = DocumentVersion(document_id=DOCUMENT_ID, version_number=1,
                              content_hash="h1", change_type="INITIAL",
                              retrieved_at=datetime.now(timezone.utc),
                              raw_path="p")
        dv2 = DocumentVersion(document_id=DOCUMENT_ID, version_number=2,
                              content_hash="h2", change_type="CHANGED",
                              retrieved_at=datetime.now(timezone.utc),
                              raw_path="p")
        session.add_all([dv1, dv2])
        session.flush()
        for dv, text in ((dv1, "v1 text"), (dv2, "v2 text")):
            session.add(DocumentChunk(
                document_id=DOCUMENT_ID, version_id=dv.version_id,
                chunk_type="BODY", heading_path="/Age", order_index=1,
                raw_text=text, page_number=1,
            ))
        session.flush()

        v1_chunks = _load_chunk_dicts(session, DOCUMENT_ID, version_id=dv1.version_id)
        v2_chunks = _load_chunk_dicts(session, DOCUMENT_ID, version_id=dv2.version_id)
        assert [c["raw_text"] for c in v1_chunks] == ["v1 text"]
        assert [c["raw_text"] for c in v2_chunks] == ["v2 text"]

    def test_change_does_not_break_evidence_lineage(self, session, monkeypatch):
        """v1 stored + extracted, then v2 stored + extracted → every evidence row
        pairs its chunk with the chunk's OWN version (old versions never leak
        into new-version evidence)."""
        from db.models import DocumentChunk
        _seed(session)
        source = session.get(GovernmentSource, SOURCE_ID)

        monkeypatch.setattr("fetcher.fetch", _ok_fetch(FIXTURE_V1))
        out_v1 = fetch_document_and_store(session, source)
        assert out_v1.document_version == 1
        assert _evidence_consistent(session) == []

        # Change the content → version 2 → re-extraction over v2 chunks only.
        monkeypatch.setattr("fetcher.fetch", _ok_fetch(FIXTURE_V2))
        out_v2 = fetch_document_and_store(session, source)
        assert out_v2.document_version == 2

        problems = _evidence_consistent(session)
        assert problems == [], f"evidence lineage broken:\n" + "\n".join(problems)

        # Version-aware leak check: evidence stamped with the NEW version id must
        # reference a chunk belonging to that same (new) version, never an old one.
        dv2 = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.document_id == DOCUMENT_ID,
                    DocumentVersion.version_number == 2)
            .one()
        )
        v2_chunk_ids = {
            c.chunk_id for c in session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == DOCUMENT_ID,
                    DocumentChunk.version_id == dv2.version_id)
            .all()
        }
        for ev in session.query(RequirementEvidence).all():
            if ev.document_version_id == dv2.version_id:
                assert ev.chunk_id in v2_chunk_ids, \
                    f"v2 evidence {ev.evidence_id} references a non-v2 chunk"

        # Extraction produced a run linked to the exact v2 version snapshot.
        runs = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == DOCUMENT_ID
        ).order_by(ExtractionRun.document_version).all()
        assert [r.document_version for r in runs] == [1, 2]
        # Each run's snapshot version_id must be v1/v2 respectively.
        vids = {
            v.version_id: v.version_number
            for v in session.query(DocumentVersion).all()
        }
        assert vids[runs[0].document_version_id] == 1
        assert vids[runs[1].document_version_id] == 2