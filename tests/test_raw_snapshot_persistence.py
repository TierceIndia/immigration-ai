"""Regression tests: raw fetched content is actually persisted to disk.

The evidentiary root of this system is the literal original government document:
every extracted requirement must be verifiable against the bytes that were
actually fetched. ``government_documents.raw_path`` and
``document_versions.raw_path`` previously stored a synthetic placeholder string
(``fetch:{document_id}:v{n}``) and the fetched bytes were discarded after
chunking, which made that verification impossible for anything fetched through
the live API/Docker path.

These tests assert the real bytes land on disk at the path the database records,
and that an earlier version's snapshot is never overwritten by a later one.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetcher
from db.connection import get_session_factory
from db.models import (
    Base,
    Country,
    DocumentVersion,
    GovernmentDocument,
    GovernmentSource,
    ImmigrationProgram,
)
from utils.fetch_document import (
    _raw_snapshot_relpath,
    build_raw_metadata,
    fetch_document_and_store,
    persist_raw_snapshot,
    read_raw_metadata,
)
from hashing import sha256_bytes

HTML_V1 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 45 years of age.</p>"
    b"</body></html>"
)

HTML_V2 = (
    b"<html><head><title>Skilled 189</title></head><body>"
    b"<h1>Eligibility</h1>"
    b"<p>Applicants must be under 45 years of age at invitation.</p>"
    b"<p>Applicants must have a skilled occupation.</p>"
    b"</body></html>"
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
def session(engine):
    s = get_session_factory(engine)()
    yield s
    s.close()


@pytest.fixture()
def raw_root(tmp_path, monkeypatch):
    """Redirect immutable snapshots into a temp dir so tests never touch raw/."""
    root = tmp_path / "raw"
    monkeypatch.setenv("RAW_SNAPSHOT_DIR", str(root))
    return root


@pytest.fixture()
def source(session):
    session.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
    session.merge(
        ImmigrationProgram(
            program_id="SKILLED_189",
            country_id="AU",
            visa_category="SKILLED",
            subclass_code="189",
            name="Skilled Independent",
            status="ACTIVE",
        )
    )
    session.add(
        GovernmentSource(
            source_id="SRC-AU-189",
            country_id="AU",
            program_id="SKILLED_189",
            visa_category="SKILLED",
            authority="Home Affairs",
            url="https://immi.homeaffairs.gov.au/189",
            source_type="HTML",
            priority="PRIMARY",
            monitoring_method="WEB",
            crawl_frequency="DAILY",
            status="ACTIVE",
        )
    )
    session.commit()
    return session.query(GovernmentSource).filter_by(source_id="SRC-AU-189").first()


def _ok_result(url, payload):
    return fetcher.FetchResult(
        url=url,
        status=fetcher.FetchStatus.OK,
        http_status=200,
        content_type="text/html",
        raw_bytes=payload,
        duration_ms=5,
        error=None,
    )


def _fetch_html(session, source, payload, monkeypatch):
    monkeypatch.setattr(fetcher, "fetch", lambda url, **kw: _ok_result(url, payload))
    outcome = fetch_document_and_store(session, source)
    session.commit()
    return outcome


class TestRawSnapshotIsPersisted:
    def test_document_raw_path_is_a_real_file(self, session, source, raw_root, monkeypatch):
        outcome = _fetch_html(session, source, HTML_V1, monkeypatch)
        assert outcome.status == "OK"

        doc = session.get(GovernmentDocument, outcome.document_id)
        assert doc is not None

        # The exact defect: a synthetic placeholder instead of a real path.
        assert not doc.raw_path.startswith("fetch:"), (
            f"raw_path is still a placeholder: {doc.raw_path!r}"
        )

        stored = Path(doc.raw_path)
        assert stored.is_file(), f"raw_path does not dereference to a file: {doc.raw_path!r}"
        assert stored.read_bytes() == HTML_V1

    def test_version_raw_path_is_a_real_file(self, session, source, raw_root, monkeypatch):
        outcome = _fetch_html(session, source, HTML_V1, monkeypatch)

        dv = (
            session.query(DocumentVersion)
            .filter_by(document_id=outcome.document_id, version_number=1)
            .one()
        )
        assert not dv.raw_path.startswith("fetch:"), (
            f"raw_path is still a placeholder: {dv.raw_path!r}"
        )

        stored = Path(dv.raw_path)
        assert stored.is_file(), f"raw_path does not dereference to a file: {dv.raw_path!r}"
        assert stored.read_bytes() == HTML_V1

    def test_stored_bytes_hash_to_the_recorded_content_hash(
        self, session, source, raw_root, monkeypatch
    ):
        """The snapshot must be the exact bytes that produced content_hash —
        otherwise the hash chain certifies something we did not store."""
        outcome = _fetch_html(session, source, HTML_V1, monkeypatch)

        dv = (
            session.query(DocumentVersion)
            .filter_by(document_id=outcome.document_id, version_number=1)
            .one()
        )
        stored_bytes = Path(dv.raw_path).read_bytes()
        assert sha256_bytes(stored_bytes) == dv.content_hash
        assert dv.content_hash == outcome.content_hash

    def test_snapshot_lives_under_the_raw_root_override(
        self, session, source, raw_root, monkeypatch
    ):
        _fetch_html(session, source, HTML_V1, monkeypatch)
        doc = session.get(GovernmentDocument, "DOC-AU-189")
        assert raw_root in Path(doc.raw_path).parents


class TestRawSnapshotImmutability:
    def test_a_new_version_does_not_overwrite_the_previous_snapshot(
        self, session, source, raw_root, monkeypatch
    ):
        """A source that changes twice must not destroy the v1 snapshot that
        document_versions v1 still references."""
        first = _fetch_html(session, source, HTML_V1, monkeypatch)
        v1_path = Path(
            session.query(DocumentVersion)
            .filter_by(document_id=first.document_id, version_number=1)
            .one()
            .raw_path
        )
        assert v1_path.read_bytes() == HTML_V1

        # Second fetch with different content -> a new version.
        second = _fetch_html(session, source, HTML_V2, monkeypatch)
        assert second.document_version == 2

        v2_path = Path(
            session.query(DocumentVersion)
            .filter_by(document_id=second.document_id, version_number=2)
            .one()
            .raw_path
        )

        assert v1_path != v2_path, "v1 and v2 must not share one snapshot file"
        assert v2_path.read_bytes() == HTML_V2
        # The critical immutability assertion: v1's bytes survive untouched.
        assert v1_path.read_bytes() == HTML_V1, (
            "writing v2 overwrote the v1 immutable snapshot"
        )

    def test_document_row_tracks_the_current_version_snapshot(
        self, session, source, raw_root, monkeypatch
    ):
        first = _fetch_html(session, source, HTML_V1, monkeypatch)
        second = _fetch_html(session, source, HTML_V2, monkeypatch)

        doc = session.get(GovernmentDocument, second.document_id)
        v2_path = Path(
            session.query(DocumentVersion)
            .filter_by(document_id=second.document_id, version_number=2)
            .one()
            .raw_path
        )
        assert doc.current_version == 2
        assert Path(doc.raw_path) == v2_path
        assert Path(doc.raw_path).read_bytes() == HTML_V2
        assert first.document_version == 1


class TestRawSnapshotPathConvention:
    def test_layout_matches_the_legacy_pipeline_convention(self, source):
        rel = _raw_snapshot_relpath(source, 3)
        assert rel.parts == ("AU", "skilled", "SRC-AU-189", "content.v3.html")

    def test_pdf_sources_get_a_pdf_extension(self, source):
        source.source_type = "PDF"
        rel = _raw_snapshot_relpath(source, 1)
        assert rel.parts == ("AU", "skilled", "SRC-AU-189", "content.v1.pdf")

    def test_persist_writes_exact_bytes_and_returns_that_path(self, source, raw_root):
        path = persist_raw_snapshot(source, 1, HTML_V1)
        assert path.is_file()
        assert path.read_bytes() == HTML_V1
        assert path == raw_root / "AU" / "skilled" / "SRC-AU-189" / "content.v1.html"

    def test_persist_is_idempotent_for_the_same_version(self, source, raw_root):
        first = persist_raw_snapshot(source, 1, HTML_V1)
        second = persist_raw_snapshot(source, 1, HTML_V1)
        # Same version_number always maps to the same file, holding the same
        # bytes — never a second file and never divergent content.
        assert first == second
        assert second.read_bytes() == HTML_V1
        # Exactly the snapshot plus its sidecar manifest — no stray temp files.
        assert sorted(p.name for p in first.parent.iterdir()) == [
            "content.v1.html",
            "content.v1.html.meta.json",
        ]


# ---------------------------------------------------------------------------
# Retrieval metadata manifest — the stored artifact must be self-describing.
# ---------------------------------------------------------------------------
class TestRawSnapshotMetadata:
    def test_manifest_records_full_retrieval_provenance(self, source, raw_root):
        from fetcher import FetchResult, FetchStatus

        retrieved_at = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)
        result = FetchResult(
            url="https://immi.homeaffairs.gov.au/final",
            status=FetchStatus.OK,
            http_status=200,
            content_type="text/html; charset=utf-8",
            raw_bytes=HTML_V1,
            duration_ms=1234,
            error=None,
        )
        path = persist_raw_snapshot(
            source, 1, HTML_V1,
            document_id="DOC-AU-189",
            content_hash=sha256_bytes(HTML_V1),
            retrieved_at=retrieved_at,
            fetch_result=result,
            acquisition="browser",
        )

        meta = read_raw_metadata(path)
        assert meta["source_id"] == "SRC-AU-189"
        assert meta["country_id"] == "AU"
        assert meta["document_id"] == "DOC-AU-189"
        assert meta["version_number"] == 1
        assert meta["requested_url"] == source.url
        assert meta["final_url"] == "https://immi.homeaffairs.gov.au/final"
        assert meta["content_type"] == "text/html; charset=utf-8"
        assert meta["http_status"] == 200
        assert meta["fetch_status"] == "OK"
        assert meta["acquisition"] == "browser"
        assert meta["retrieved_at"] == retrieved_at.isoformat()
        assert meta["content_hash"] == sha256_bytes(HTML_V1)
        assert meta["content_length"] == len(HTML_V1)

    def test_manifest_is_deterministic_for_identical_retrieval(self, source, raw_root):
        """Identical retrievals must produce byte-identical manifests — the
        manifest is part of the evidence trail, so it cannot drift."""
        from fetcher import FetchResult, FetchStatus

        def _result():
            return FetchResult(
                url=source.url, status=FetchStatus.OK, http_status=200,
                content_type="text/html", raw_bytes=HTML_V1, duration_ms=7,
                error=None,
            )

        first = persist_raw_snapshot(
            source, 1, HTML_V1, document_id="DOC-AU-189",
            content_hash=sha256_bytes(HTML_V1),
            retrieved_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            fetch_result=_result(), acquisition="http",
        )
        first_bytes = _manifest_path_for_test(first).read_bytes()
        second = persist_raw_snapshot(
            source, 1, HTML_V1, document_id="DOC-AU-189",
            content_hash=sha256_bytes(HTML_V1),
            retrieved_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            fetch_result=_result(), acquisition="http",
        )
        assert first == second
        assert _manifest_path_for_test(second).read_bytes() == first_bytes

    def test_metadata_preserves_failure_diagnostics(self, source, raw_root):
        """A failure classification written into the manifest must stay
        machine-readable, not collapse into free text."""
        from fetcher import FetchResult, FetchStatus

        result = FetchResult(
            url=source.url,
            status=FetchStatus.NETWORK_RESTRICTED,
            http_status=None,
            content_type=None,
            raw_bytes=None,
            duration_ms=15,
            error="proxy refused",
            error_type="proxy_rejected",
        )
        meta = build_raw_metadata(
            source=source, document_id="DOC-AU-189", version_number=1,
            content_hash="deadbeef", retrieved_at=datetime.now(timezone.utc),
            raw_bytes=b"", fetch_result=result, acquisition="http",
        )
        assert meta["fetch_status"] == "NETWORK_RESTRICTED"
        assert meta["error_type"] == "proxy_rejected"
        assert meta["http_status"] is None


def _manifest_path_for_test(path):
    return path.parent / f"{path.name}.meta.json"
