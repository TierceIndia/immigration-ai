"""Tests for PipelineRun tracking (model, repo, API, integration)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import (
    Base, Country, ImmigrationProgram, GovernmentSource,
    GovernmentDocument, PipelineRun, ApiUser,
)
from db.repositories import PipelineRunRepo
from db.connection import get_session_factory
from api.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def session(db_engine):
    Session = sessionmaker(bind=db_engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def client(db_engine):
    app.state.db_engine = db_engine
    with TestClient(app) as c:
        yield c


def _seed_source(session, source_id="src-001"):
    """Seed a government source with all required FK dependencies (idempotent)."""
    country = session.get(Country, "AU")
    if not country:
        session.add(Country(country_id="AU", name="Australia", iso_code="AU"))
    prog = session.get(ImmigrationProgram, "491")
    if not prog:
        session.add(ImmigrationProgram(
            program_id="491", country_id="AU", visa_category="SKILLED",
            subclass_code="491", name="Skilled Work Regional (Provisional) - subclass 491",
        ))
    src = session.get(GovernmentSource, source_id)
    if not src:
        session.add(GovernmentSource(
            source_id=source_id, country_id="AU", program_id="491",
            visa_category="SKILLED", authority="Department of Home Affairs",
            url=f"https://example.com/{source_id}",
            source_type="WEB", priority="HIGH", monitoring_method="WEB",
            crawl_frequency="DAILY", status="ACTIVE",
        ))
    session.flush()


def _seed_source_with_doc(session, source_id="src-001", doc_id=None, content_hash="hash_v1"):
    """Seed a source with a document for orchestrator integration tests."""
    _seed_source(session, source_id)
    if doc_id is None:
        doc_id = f"doc-{source_id}"
    existing = session.get(GovernmentDocument, doc_id)
    if not existing:
        session.add(GovernmentDocument(
            document_id=doc_id, source_id=source_id, country_id="AU",
            program_id="491", visa_category="SKILLED", document_type="WEB",
            url=f"https://example.com/{doc_id}", title=f"Doc {doc_id}",
            retrieved_at=datetime.now(timezone.utc), content_hash=content_hash,
            raw_path=f"/raw/{doc_id}", status="CURRENT",
        ))
    session.flush()


def _auth_headers(client, engine):
    """Register a test admin user and return auth headers."""
    client.post(
        "/api/v1/auth/register",
        json={"email": "run-test@example.com", "password": "pass123", "full_name": "Run Test"},
    )
    # Set role to admin directly
    factory = get_session_factory(engine)
    with factory() as s:
        user = s.query(ApiUser).filter(ApiUser.email == "run-test@example.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "run-test@example.com", "password": "pass123"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# PipelineRun ORM Model
# ---------------------------------------------------------------------------

class TestPipelineRunModel:
    def test_create_run(self, session):
        _seed_source(session)
        run = PipelineRun(source_id="src-001", trigger_type="MANUAL")
        session.add(run)
        session.flush()
        assert run.run_id is not None
        assert run.status == "RUNNING"
        assert run.trigger_type == "MANUAL"
        assert run.started_at is not None
        assert run.completed_at is None
        assert run.duration_ms is None
        assert run.errors == []
        assert run.metadata_json == {}

    def test_default_values(self, session):
        run = PipelineRun(source_id="x", trigger_type="SCHEDULED")
        session.add(run)
        session.flush()
        assert run.requirements_extracted == 0
        assert run.requirements_approved == 0
        assert run.rules_created == 0
        assert run.errors == []
        assert run.metadata_json == {}

    def test_set_metrics(self, session):
        run = PipelineRun(source_id="x", trigger_type="MANUAL")
        session.add(run)
        run.old_hash = "abc123"
        run.new_hash = "def456"
        run.requirements_extracted = 10
        run.rules_created = 5
        run.errors = ["timeout"]
        run.metadata_json = {"dry_run": True}
        session.flush()
        assert run.old_hash == "abc123"
        assert run.new_hash == "def456"
        assert run.requirements_extracted == 10
        assert run.rules_created == 5
        assert run.errors == ["timeout"]
        assert run.metadata_json == {"dry_run": True}

    def test_fk_to_source(self, session):
        _seed_source(session)
        run = PipelineRun(source_id="src-001", trigger_type="MANUAL")
        session.add(run)
        session.flush()
        loaded = session.get(PipelineRun, run.run_id)
        assert loaded.source_id == "src-001"


# ---------------------------------------------------------------------------
# PipelineRunRepo
# ---------------------------------------------------------------------------

class TestPipelineRunRepo:
    def test_create(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        assert run.run_id is not None
        assert run.status == "RUNNING"
        assert run.trigger_type == "MANUAL"

    def test_get(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        loaded = repo.get(run.run_id)
        assert loaded is not None
        assert loaded.run_id == run.run_id

    def test_get_nonexistent(self, session):
        repo = PipelineRunRepo(session)
        assert repo.get("nonexistent") is None

    def test_complete(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        completed = repo.complete(
            run.run_id,
            requirements_extracted=10,
            requirements_approved=8,
            rules_created=5,
        )
        assert completed.status == "COMPLETED"
        assert completed.completed_at is not None
        assert completed.duration_ms is not None
        assert completed.requirements_extracted == 10
        assert completed.rules_created == 5

    def test_complete_nonexistent(self, session):
        repo = PipelineRunRepo(session)
        with pytest.raises(ValueError, match="not found"):
            repo.complete("nonexistent")

    def test_fail(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        failed = repo.fail(run.run_id, "Connection timeout")
        assert failed.status == "FAILED"
        assert failed.completed_at is not None
        assert failed.errors == ["Connection timeout"]

    def test_fail_nonexistent(self, session):
        repo = PipelineRunRepo(session)
        with pytest.raises(ValueError, match="not found"):
            repo.fail("nonexistent", "error")

    def test_partial(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        partial = repo.partial(run.run_id, "Some chunks failed", rules_created=3)
        assert partial.status == "PARTIAL"
        assert partial.completed_at is not None
        assert partial.errors == ["Some chunks failed"]
        assert partial.rules_created == 3

    def test_partial_nonexistent(self, session):
        repo = PipelineRunRepo(session)
        with pytest.raises(ValueError, match="not found"):
            repo.partial("nonexistent", "error")

    def test_fail_accumulates_errors(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        run.errors = ["first error"]
        session.flush()
        failed = repo.fail(run.run_id, "second error")
        assert failed.errors == ["first error", "second error"]

    def test_list_all(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        repo.create(source_id="src-001", trigger_type="MANUAL")
        repo.create(source_id="src-001", trigger_type="SCHEDULED")
        repo.create(source_id="src-001", trigger_type="ON_DEMAND")
        runs = repo.list_all(limit=10)
        assert len(runs) == 3

    def test_list_all_empty(self, session):
        repo = PipelineRunRepo(session)
        runs = repo.list_all()
        assert runs == []

    def test_list_all_limit(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        for _ in range(5):
            repo.create(source_id="src-001", trigger_type="MANUAL")
        runs = repo.list_all(limit=2)
        assert len(runs) == 2

    def test_list_all_offset(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        for _ in range(5):
            repo.create(source_id="src-001", trigger_type="MANUAL")
        runs = repo.list_all(limit=2, offset=3)
        assert len(runs) == 2

    def test_list_for_source(self, session):
        _seed_source(session, "src-001")
        _seed_source(session, "src-002")
        repo = PipelineRunRepo(session)
        repo.create(source_id="src-001", trigger_type="MANUAL")
        repo.create(source_id="src-001", trigger_type="SCHEDULED")
        repo.create(source_id="src-002", trigger_type="MANUAL")
        runs = repo.list_for_source("src-001")
        assert len(runs) == 2
        assert all(r.source_id == "src-001" for r in runs)

    def test_list_for_source_empty(self, session):
        repo = PipelineRunRepo(session)
        runs = repo.list_for_source("nonexistent")
        assert runs == []

    def test_list_for_source_limit(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        for _ in range(5):
            repo.create(source_id="src-001", trigger_type="MANUAL")
        runs = repo.list_for_source("src-001", limit=2)
        assert len(runs) == 2

    def test_count_by_status(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        r1 = repo.create(source_id="src-001", trigger_type="MANUAL")
        r2 = repo.create(source_id="src-001", trigger_type="MANUAL")
        r3 = repo.create(source_id="src-001", trigger_type="MANUAL")
        repo.complete(r1.run_id)
        repo.fail(r2.run_id, "error")
        counts = repo.count_by_status()
        assert counts["COMPLETED"] == 1
        assert counts["FAILED"] == 1
        assert counts["RUNNING"] == 1

    def test_count_by_status_empty(self, session):
        repo = PipelineRunRepo(session)
        counts = repo.count_by_status()
        assert counts == {}

    def test_duration_ms_calculated(self, session):
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        # Simulate some time passing by manually setting started_at
        from datetime import timedelta
        run.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        session.flush()
        completed = repo.complete(run.run_id)
        assert completed.duration_ms is not None
        assert completed.duration_ms >= 1900  # at least ~2 seconds


# ---------------------------------------------------------------------------
# PipelineRun API
# ---------------------------------------------------------------------------

class TestPipelineRunAPI:
    def test_list_runs(self, client, db_engine):
        headers = _auth_headers(client, db_engine)
        resp = client.get("/api/v1/admin/runs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert "total" in data

    def test_list_runs_requires_admin(self, client, db_engine):
        # Non-admin user
        client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "pass123", "full_name": "User"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "pass123"},
        )
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/api/v1/admin/runs", headers=headers)
        assert resp.status_code == 403

    def test_list_runs_unauthenticated(self, client):
        resp = client.get("/api/v1/admin/runs")
        assert resp.status_code in (401, 403)

    def test_get_run(self, client, db_engine, session):
        headers = _auth_headers(client, db_engine)
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        session.commit()
        resp = client.get(f"/api/v1/admin/runs/{run.run_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run.run_id
        assert data["source_id"] == "src-001"
        assert data["trigger_type"] == "MANUAL"
        assert data["status"] == "RUNNING"

    def test_get_run_not_found(self, client, db_engine):
        headers = _auth_headers(client, db_engine)
        resp = client.get("/api/v1/admin/runs/nonexistent", headers=headers)
        assert resp.status_code == 404

    def test_runs_for_source(self, client, db_engine, session):
        headers = _auth_headers(client, db_engine)
        _seed_source(session)
        repo = PipelineRunRepo(session)
        repo.create(source_id="src-001", trigger_type="MANUAL")
        repo.create(source_id="src-001", trigger_type="SCHEDULED")
        session.commit()
        resp = client.get("/api/v1/admin/runs/source/src-001", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_id"] == "src-001"
        assert data["total"] == 2

    def test_runs_for_source_empty(self, client, db_engine):
        headers = _auth_headers(client, db_engine)
        resp = client.get("/api/v1/admin/runs/source/nonexistent", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_run_summary(self, client, db_engine, session):
        headers = _auth_headers(client, db_engine)
        _seed_source(session)
        repo = PipelineRunRepo(session)
        r1 = repo.create(source_id="src-001", trigger_type="MANUAL")
        repo.complete(r1.run_id)
        session.commit()
        resp = client.get("/api/v1/admin/runs/summary", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "counts" in data
        assert data["counts"]["COMPLETED"] == 1

    def test_list_runs_with_pagination(self, client, db_engine, session):
        headers = _auth_headers(client, db_engine)
        _seed_source(session)
        repo = PipelineRunRepo(session)
        for _ in range(5):
            repo.create(source_id="src-001", trigger_type="MANUAL")
        session.commit()
        resp = client.get("/api/v1/admin/runs?limit=2&offset=0", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runs"]) == 2

    def test_completed_run_in_api(self, client, db_engine, session):
        headers = _auth_headers(client, db_engine)
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="MANUAL")
        repo.complete(run.run_id, rules_created=3)
        session.commit()
        resp = client.get(f"/api/v1/admin/runs/{run.run_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["rules_created"] == 3
        assert data["completed_at"] is not None
        assert data["duration_ms"] is not None

    def test_failed_run_in_api(self, client, db_engine, session):
        headers = _auth_headers(client, db_engine)
        _seed_source(session)
        repo = PipelineRunRepo(session)
        run = repo.create(source_id="src-001", trigger_type="SCHEDULED")
        repo.fail(run.run_id, "Connection refused")
        session.commit()
        resp = client.get(f"/api/v1/admin/runs/{run.run_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "FAILED"
        assert data["errors"] == ["Connection refused"]


# ---------------------------------------------------------------------------
# Integration: Orchestrator creates PipelineRuns
# ---------------------------------------------------------------------------

class TestOrchestratorPipelineRunIntegration:
    def test_sync_source_no_document_creates_run(self, session):
        """Source with no document → detect_change returns (False, None, None) → COMPLETED unchanged."""
        _seed_source(session)
        from orchestration.government_sync import sync_source
        result = sync_source(session, "src-001", dry_run=True, trigger_type="ON_DEMAND")
        session.flush()
        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].source_id == "src-001"
        assert runs[0].trigger_type == "ON_DEMAND"
        assert runs[0].status == "COMPLETED"  # no doc = unchanged
        assert result.status == "unchanged"

    def test_sync_source_first_hash_creates_run(self, session):
        """Source with a document but no last_hash → first-time detection → COMPLETED."""
        _seed_source_with_doc(session, "src-001", content_hash="new_hash_abc")
        from orchestration.government_sync import sync_source
        result = sync_source(session, "src-001", dry_run=True, trigger_type="MANUAL")
        session.flush()
        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].trigger_type == "MANUAL"
        assert runs[0].new_hash == "new_hash_abc"
        assert runs[0].status == "COMPLETED"

    def test_sync_source_changed_creates_completed_run(self, session):
        """Source with old_hash != new_hash → changed detection → COMPLETED with metrics."""
        _seed_source_with_doc(session, "src-001", content_hash="hash_v2")
        source = session.get(GovernmentSource, "src-001")
        source.last_hash = "hash_v1"
        session.flush()
        from orchestration.government_sync import sync_source
        result = sync_source(session, "src-001", dry_run=True, trigger_type="MANUAL")
        session.flush()
        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].old_hash == "hash_v1"
        assert runs[0].new_hash == "hash_v2"
        assert runs[0].status == "COMPLETED"
        assert result.status == "changed"

    def test_sync_source_unchanged_creates_completed_run(self, session):
        """Source with same hash → unchanged → COMPLETED."""
        _seed_source_with_doc(session, "src-001", content_hash="same_hash")
        source = session.get(GovernmentSource, "src-001")
        source.last_hash = "same_hash"
        session.flush()
        from orchestration.government_sync import sync_source
        result = sync_source(session, "src-001", dry_run=True, trigger_type="SCHEDULED")
        session.flush()
        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].status == "COMPLETED"
        assert result.status == "unchanged"

    def test_sync_source_error_creates_failed_run(self, session):
        from orchestration.government_sync import sync_source
        # Non-existent source will raise ValueError, caught as error
        result = sync_source(session, "nonexistent", trigger_type="MANUAL")
        session.flush()
        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].status == "FAILED"
        assert len(runs[0].errors) > 0

    def test_sync_all_sources_creates_runs(self, session):
        _seed_source(session, "src-001")
        _seed_source(session, "src-002")
        from orchestration.government_sync import sync_all_sources
        results = sync_all_sources(session, dry_run=True, trigger_type="SCHEDULED")
        session.flush()
        runs = session.query(PipelineRun).all()
        assert len(runs) == 2
        assert all(r.trigger_type == "SCHEDULED" for r in runs)

    def test_scheduler_sync_job_creates_scheduled_runs(self, session):
        _seed_source(session)
        from scheduler import _sync_job
        from db.connection import get_engine
        # Create an in-memory engine for the scheduler to use
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        # Seed source in the new engine
        Session = sessionmaker(bind=engine)
        with Session() as s:
            _seed_source(s, "src-001")
            s.commit()
        _sync_job(engine)
        with Session() as s:
            runs = s.query(PipelineRun).all()
            assert len(runs) == 1
            assert runs[0].trigger_type == "SCHEDULED"
