"""Lineage + immutability + integrity hardening tests (Phase G migration).

Covers the invariants the `f7a6d5c4b3a2_lineage_and_immutability` migration enforces:
  * CHECK constraints (document version numbers, status vocabularies, bool flags,
    change_type vocabulary incl. MANUAL_CAPTURE)
  * UNIQUE constraints (document version/snapshot identity, program country pairing,
    one-live-active requirement per semantic key, one rule result per assessment)
  * composite foreign-key lineage (doc/version pairs, chunk/version triples, rulesets)
  * extraction-run idempotency (reuse RUNNING/COMPLETED, never reuse FAILED)
  * ruleset idempotency + immutable release tracking + assessment traceability
  * fallback-source integrity guard (self-ref / missing / cycle)
"""
from __future__ import annotations

import json
import uuid
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base
from db.models import GovernmentSource as SourceORM
from db.models import GovernmentDocument as DocORM
from db.models import DocumentVersion as DocVersionORM
from db.models import DocumentChunk as ChunkORM
from db.models import ExtractionRun as ExtractORM
from db.models import FetchLog as FetchLogORM
from db.models import Country as CountryORM
from db.models import DiscoveredSource as DiscoveredSourceORM
from db.models import ImmigrationProgram as ProgramORM
from db.models import Requirement as RequirementORM
from db.models import RequirementEvidence as EvidenceORM
from db.models import Rule as RuleORM
from db.models import RuleVersion as RuleVersionORM
from db.models import Ruleset as RulesetORM
from db.models import Assessment as AssessmentORM
from db.models import AssessmentRuleResult as ArrORM
from db.models import ApplicantProfile as ApplicantORM
from db.models import PipelineRun as PipelineRunORM
from db.repositories import SourceRepo, RulesetRepo
from phase3.models import Rule as RuleDC, Provenance as ProvenanceDC

NOW = datetime.now(timezone.utc)


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
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = factory()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def fk_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(eng, "connect", lambda dbapi_con, _record: dbapi_con.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def fk_session(fk_engine):
    factory = sessionmaker(bind=fk_engine, autocommit=False, autoflush=False)
    s = factory()
    yield s
    s.rollback()
    s.close()


def _seed_country_program(session, program_id="SKILLED_189", country_id="AU"):
    session.add(CountryORM(country_id=country_id, name="Australia", iso_code=country_id))
    session.add(ProgramORM(
        program_id=program_id, country_id=country_id,
        visa_category="Skilled", name="Skilled Independent",
    ))
    session.flush()


def _seed_document(session, document_id="D1", version_number=1, content_hash="h1"):
    doc = DocORM(
        document_id=document_id, source_id="SRC-1", country_id="AU",
        program_id="SKILLED_189", visa_category="Skilled", document_type="HTML",
        url="https://example.gov/doc", retrieved_at=NOW,
        content_hash=content_hash, current_version=version_number,
        raw_path=f"/raw/{document_id}", status="CURRENT",
    )
    session.add(doc)
    dv = DocVersionORM(
        document_id=document_id, version_number=version_number,
        content_hash=content_hash, retrieved_at=NOW,
        raw_path=f"/raw/{document_id}", change_type="INITIAL",
    )
    session.add(dv)
    session.flush()
    return doc, dv


def _seed_fk_chain(session, program_id="SKILLED_189", country_id="AU"):
    """Country -> program -> source -> document -> version -> extraction run, all FK-valid."""
    session.add(CountryORM(country_id=country_id, name="Australia", iso_code=country_id))
    session.add(ProgramORM(
        program_id=program_id, country_id=country_id,
        visa_category="Skilled", name="Skilled Independent",
    ))
    # flush the (country, program) pair first: SQLAlchemy cannot topo-sort a flush across
    # a composite FK (program_id, country_id) -> immigration_programs, so a single flush
    # may INSERT government_sources before immigration_programs exists.
    session.flush()
    session.add(SourceORM(
        source_id="SRC-1", country_id=country_id, program_id=program_id,
        visa_category="SKILLED", authority="Home Affairs",
        url="https://example.gov/src", source_type="HTML", status="ACTIVE",
        priority="SECONDARY", crawl_frequency="WEEKLY",
    ))
    session.flush()
    doc = DocORM(
        document_id="D1", source_id="SRC-1", country_id=country_id,
        program_id=program_id, visa_category="Skilled", document_type="HTML",
        url="https://example.gov/doc", retrieved_at=NOW,
        content_hash="h1", current_version=1, raw_path="/raw/D1", status="CURRENT",
    )
    session.add(doc)
    # flush the document before its version: SQLAlchemy's flush ordering emits
    # document_versions before government_documents when both are pending in one flush
    session.flush()
    dv = DocVersionORM(
        document_id="D1", version_number=1, content_hash="h1",
        retrieved_at=NOW, raw_path="/raw/D1", change_type="INITIAL",
    )
    session.add(dv)
    session.flush()
    run = ExtractORM(
        run_id="RUN-1", document_id="D1", document_version=1,
        document_version_id=dv.version_id, model="mock-llm-v1",
        prompt_version="p1", status="RUNNING", dry_run=0,
    )
    session.add(run)
    session.flush()
    return doc, dv, run


def _seed_legacy_document(conn, document_id="D1", version_number=1, content_hash="h1"):
    """Wire the legacy FK chain (country -> program -> source -> document -> version)."""
    conn.execute(
        "INSERT OR IGNORE INTO countries (country_id, name, iso_code) VALUES ('AU', 'Australia', 'AU')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO immigration_programs "
        "(program_id, country_id, visa_category, name) VALUES "
        "('SKILLED_189', 'AU', 'SKILLED', 'Skilled Independent')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO government_sources "
        "(source_id, country_id, program_id, visa_category, authority, url, source_type, priority) "
        "VALUES ('SRC-1', 'AU', 'SKILLED_189', 'SKILLED', 'Home Affairs', "
        "'https://example.gov/src', 'HTML', 'PRIMARY')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO government_documents "
        "(document_id, source_id, country_id, program_id, visa_category, document_type, url, "
        "retrieved_at, content_hash, current_version, raw_path) "
        "VALUES (?, 'SRC-1', 'AU', 'SKILLED_189', 'SKILLED', 'HTML', "
        "'https://example.gov/doc', '2026-01-01T00:00:00Z', ?, ?, ?)",
        (document_id, content_hash, version_number, f"/raw/{document_id}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO document_versions "
        "(document_id, version_number, content_hash, retrieved_at, raw_path, change_type) "
        "VALUES (?, ?, ?, '2026-01-01T00:00:00Z', '/x', 'INITIAL')",
        (document_id, version_number, content_hash),
    )
    conn.commit()


class TestDocumentVersionConstraints:
    def test_version_number_zero_rejected(self, session):
        session.add(DocVersionORM(
            document_id="D1", version_number=0, content_hash="h",
            retrieved_at=NOW, raw_path="/r", change_type="INITIAL",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_invalid_change_type_rejected(self, session):
        session.add(DocVersionORM(
            document_id="D1", version_number=1, content_hash="h",
            retrieved_at=NOW, raw_path="/r", change_type="NEW",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_duplicate_version_number_rejected(self, session):
        session.add(DocVersionORM(
            document_id="D1", version_number=1, content_hash="h1",
            retrieved_at=NOW, raw_path="/r", change_type="INITIAL",
        ))
        session.flush()
        session.add(DocVersionORM(
            document_id="D1", version_number=1, content_hash="h2",
            retrieved_at=NOW, raw_path="/r", change_type="CHANGED",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_duplicate_content_hash_rejected(self, session):
        session.add(DocVersionORM(
            document_id="D1", version_number=1, content_hash="h1",
            retrieved_at=NOW, raw_path="/r", change_type="INITIAL",
        ))
        session.flush()
        session.add(DocVersionORM(
            document_id="D1", version_number=2, content_hash="h1",
            retrieved_at=NOW, raw_path="/r", change_type="CHANGED",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_manual_capture_change_type_accepted(self, session):
        session.add(DocVersionORM(
            document_id="D1", version_number=1, content_hash="h1",
            retrieved_at=NOW, raw_path="/r", change_type="MANUAL_CAPTURE",
            diff_summary="retrieval_method=MANUAL_CAPTURE",
        ))
        session.flush()


class TestRequirementConstraints:
    def _add(self, session, status="APPROVED", sem_key="sk1", program_id="SKILLED_189"):
        session.add(RequirementORM(
            requirement_id=f"REQ-{uuid.uuid4().hex[:8]}", semantic_key=sem_key,
            program_id=program_id, country_id="AU", category="age",
            name="Under 45", field="age", operator="LT", value_json="45",
            mandatory=1, status=status, current_version=1,
            extraction_run_id="RUN-1",
        ))
        session.flush()

    def test_invalid_status_rejected(self, session):
        _seed_country_program(session)
        with pytest.raises(IntegrityError):
            self._add(session, status="NOPE")

    def test_zero_current_version_rejected(self, session):
        _seed_country_program(session)
        session.add(RequirementORM(
            requirement_id="REQ-x", semantic_key="skx", program_id="SKILLED_189",
            country_id="AU", category="age", name="Under 45", field="age",
            operator="LT", value_json="45", mandatory=1,
            status="APPROVED", current_version=0, extraction_run_id="RUN-1",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_duplicate_active_semantic_key_rejected(self, session):
        _seed_country_program(session)
        self._add(session, status="APPROVED", sem_key="sk1")
        with pytest.raises(IntegrityError):
            self._add(session, status="DRAFT", sem_key="sk1")

    def test_conflicted_semantic_key_allowed_alongside_active(self, session):
        _seed_country_program(session)
        self._add(session, status="APPROVED", sem_key="sk1")
        self._add(session, status="CONFLICT", sem_key="sk1")
        self._add(session, status="REJECTED", sem_key="sk1")


class TestFetchLogConstraint:
    def test_changed_flag_invalid_rejected(self, session):
        session.add(FetchLogORM(
            source_id="SRC-1", fetched_at=NOW, status="OK", changed=2,
        ))
        with pytest.raises(IntegrityError):
            session.flush()


class TestRuleVersionConstraints:
    def test_version_number_zero_rejected(self, session):
        _seed_country_program(session)
        session.add(RuleORM(
            rule_id="R1", requirement_id="REQ-1", program_id="SKILLED_189",
            category="age", field="age", operator="LT", value_json="45",
            mandatory=True, critical=True, message="Under 45",
            status="ACTIVE", current_version=1,
        ))
        session.flush()
        session.add(RuleVersionORM(
            rule_id="R1", version_number=0, snapshot_json={},
            change_type="ACTIVATED",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_duplicate_rule_version_rejected(self, session):
        _seed_country_program(session)
        session.add(RuleORM(
            rule_id="R1", requirement_id="REQ-1", program_id="SKILLED_189",
            category="age", field="age", operator="LT", value_json="45",
            mandatory=True, critical=True, message="Under 45",
            status="ACTIVE", current_version=1,
        ))
        session.flush()
        session.add(RuleVersionORM(
            rule_id="R1", version_number=1, snapshot_json={}, change_type="ACTIVATED",
        ))
        session.flush()
        session.add(RuleVersionORM(
            rule_id="R1", version_number=1, snapshot_json={}, change_type="MODIFIED",
        ))
        with pytest.raises(IntegrityError):
            session.flush()


class TestDocumentChunkConstraints:
    def test_duplicate_order_index_rejected(self, session):
        _, dv = _seed_document(session)
        dv2 = DocVersionORM(
            document_id="D1", version_number=2, content_hash="h2",
            retrieved_at=NOW, raw_path="/r", change_type="CHANGED",
        )
        session.add(dv2)
        session.flush()
        chunk = ChunkORM(
            document_id="D1", version_id=dv2.version_id, chunk_type="BODY",
            heading_path="/h", order_index=1, raw_text="text", page_number=1,
        )
        session.add(chunk)
        session.flush()
        session.add(ChunkORM(
            document_id="D1", version_id=dv2.version_id, chunk_type="BODY",
            heading_path="/h", order_index=1, raw_text="other", page_number=1,
        ))
        with pytest.raises(IntegrityError):
            session.flush()


class TestForeignKeyEnforcement:
    """PRAGMA foreign_keys=ON makes SQLite enforce the composite lineage FKs exactly
    like PostgreSQL does via the Alembic migration."""

    def test_extraction_run_doc_version_pair_must_exist(self, fk_session):
        _seed_fk_chain(fk_session)
        fk_session.add(ExtractORM(
            run_id="RUN-2", document_id="D1", document_version=99,
            model="mock-llm-v1", prompt_version="p1", status="RUNNING", dry_run=0,
        ))
        with pytest.raises(IntegrityError):
            fk_session.flush()

    def test_evidence_chunk_lineage_triple_mismatch_rejected(self, fk_session):
        _, dv, _ = _seed_fk_chain(fk_session)
        fk_session.add(RequirementORM(
            requirement_id="REQ-1", semantic_key="sk1", program_id="SKILLED_189",
            country_id="AU", category="age", name="Under 45", field="age",
            operator="LT", value_json="45", mandatory=1,
            status="APPROVED", current_version=1, extraction_run_id="RUN-1",
        ))
        chunk = ChunkORM(
            document_id="D1", version_id=dv.version_id, chunk_type="BODY",
            heading_path="/h", order_index=1, raw_text="text", page_number=1,
        )
        fk_session.add(chunk)
        fk_session.flush()
        # valid: chunk_id + document_id + version_id all point at the real chunk/version
        fk_session.add(EvidenceORM(
            requirement_id="REQ-1", document_id="D1", document_version=1,
            document_version_id=dv.version_id, chunk_id=chunk.chunk_id,
            evidence_text="Under 45", evidence_type="SUBMATCH",
            verified=1, extraction_method="LLM_EXTRACTION", extracted_at=NOW,
        ))
        fk_session.flush()
        # invalid: version_id does not match the chunk's owning version
        fk_session.add(EvidenceORM(
            requirement_id="REQ-1", document_id="D1", document_version=1,
            document_version_id=999, chunk_id=chunk.chunk_id,
            evidence_text="Under 45", evidence_type="SUBMATCH",
            verified=1, extraction_method="LLM_EXTRACTION", extracted_at=NOW,
        ))
        with pytest.raises(IntegrityError):
            fk_session.flush()

    def test_evidence_doc_version_pair_must_exist(self, fk_session):
        _seed_fk_chain(fk_session)
        fk_session.add(RequirementORM(
            requirement_id="REQ-1", semantic_key="sk1", program_id="SKILLED_189",
            country_id="AU", category="age", name="Under 45", field="age",
            operator="LT", value_json="45", mandatory=1,
            status="APPROVED", current_version=1, extraction_run_id="RUN-1",
        ))
        fk_session.add(EvidenceORM(
            requirement_id="REQ-1", document_id="D1", document_version=1,
            chunk_id=1, evidence_text="Under 45", evidence_type="SUBMATCH",
            verified=1, extraction_method="LLM_EXTRACTION", extracted_at=NOW,
        ))
        with pytest.raises(IntegrityError):
            fk_session.flush()


class TestRulesetIntegrity:
    def _seed_rule_with_versions(self, session, rule_id="R1", versions=(1,)):
        existing = session.get(RuleORM, rule_id)
        if existing is None:
            rule = RuleORM(
                rule_id=rule_id, requirement_id="REQ-1", program_id="SKILLED_189",
                category="age", field="age", operator="LT", value_json="45",
                mandatory=True, critical=True, message="Under 45",
                status="ACTIVE", current_version=max(versions),
            )
            session.add(rule)
            session.flush()
        else:
            rule = existing
            rule.current_version = max(versions)
        created = []
        for ver in versions:
            dup = (
                session.query(RuleVersionORM)
                .filter_by(rule_id=rule_id, version_number=ver)
                .first()
            )
            if dup is not None:
                created.append(dup)
                continue
            rv = RuleVersionORM(
                rule_id=rule_id, version_number=ver, snapshot_json={},
                change_type="ACTIVATED" if ver == 1 else "MODIFIED",
            )
            session.add(rv)
            created.append(rv)
        session.flush()
        return rule, created

    def test_ruleset_acquire_is_idempotent(self, session):
        from services.assessment_service import _resolve_rule_versions, _acquire_ruleset
        rule, _ = self._seed_rule_with_versions(session, "R1", (1,))
        resolved = _resolve_rule_versions(session, [rule])
        assert resolved["R1"] is not None and resolved["R1"][1] == 1

        sid1, h1 = _acquire_ruleset(session, "SKILLED_189", resolved)
        sid2, h2 = _acquire_ruleset(session, "SKILLED_189", resolved)
        assert sid1 == sid2
        assert h1 == h2 and len(h1) == 16

        count = session.query(RulesetORM).filter_by(program_id="SKILLED_189").count()
        assert count == 1

    def test_ruleset_changes_when_rule_version_changes(self, session):
        from services.assessment_service import _resolve_rule_versions, _acquire_ruleset
        rule, _ = self._seed_rule_with_versions(session, "R1", (1,))
        sid1, h1 = _acquire_ruleset(session, "SKILLED_189", _resolve_rule_versions(session, [rule]))

        rule, new_versions = self._seed_rule_with_versions(session, "R1", (1, 2))
        sid2, h2 = _acquire_ruleset(
            session, "SKILLED_189", _resolve_rule_versions(session, [rule])
        )
        assert new_versions[-1].version_number == 2
        assert sid2 != sid1
        assert h2 != h1

    def test_ruleset_position_conflict_rejected(self, session):
        from services.assessment_service import _resolve_rule_versions, _acquire_ruleset
        rule, _ = self._seed_rule_with_versions(session, "R1", (1,))
        resolved = _resolve_rule_versions(session, [rule])
        sid, _ = _acquire_ruleset(session, "SKILLED_189", resolved)
        v_id = resolved["R1"][0]
        repo = RulesetRepo(session)
        repo.add_rule(sid, v_id, 0)
        with pytest.raises(IntegrityError):
            session.flush()

    def test_assessment_trace_back_to_exact_rule_version(self, session):
        from services.assessment_service import AssessmentService, _resolve_rule_versions, _acquire_ruleset
        rule, versions = self._seed_rule_with_versions(session, "R1", (1,))
        resolved = _resolve_rule_versions(session, [rule])
        ruleset_id, ruleset_hash = _acquire_ruleset(session, "SKILLED_189", resolved)

        app_id = str(uuid.uuid4())
        session.add(ApplicantORM(
            applicant_id=app_id, user_id=str(uuid.uuid4()),
            first_name="Ada", last_name="Lovelace", profile_json={},
        ))
        session.flush()

        active_rules = [
            RuleDC(
                rule_id="R1", requirement_id="REQ-1", program_id="SKILLED_189",
                category="age", field="age", operator="LT", value=45,
                unit="years", mandatory=True, critical=True, message="Under 45",
                provenance=ProvenanceDC(
                    source_id="SRC-1", document_id="D1", document_version=1,
                    chunk_id=1, requirement_id="REQ-1", evidence_text="Under 45",
                ),
                status="ACTIVE", version=1,
            )
        ]
        eligibility_result = {
            "business_status": "ELIGIBLE",
            "overall_status": "PASS",
            "total_rules": 1,
            "passed_count": 1,
            "failed_count": 0,
            "unknown_count": 0,
            "not_applicable_count": 0,
            "rule_evaluations": [
                {
                    "rule_id": "R1", "status": "PASS", "field": "age",
                    "actual_value": 30, "expected_value": 45,
                    "operator": "LT", "message": "ok", "reason": None,
                }
            ],
        }
        svc = AssessmentService(session)
        result = svc.create(
            applicant_id=app_id, program_id="SKILLED_189",
            eligibility_result=eligibility_result,
            active_rules=active_rules,
            request_id="REQ-TRACE",
            applicant_snapshot={"age": 30},
            ruleset_hash=ruleset_hash, ruleset_id=ruleset_id,
            rule_version_ids=resolved,
        )

        assessment = session.get(AssessmentORM, result["assessment_id"])
        assert assessment.ruleset_id == ruleset_id
        assert assessment.ruleset_hash == ruleset_hash

        arrs = (
            session.query(ArrORM)
            .filter_by(assessment_id=result["assessment_id"])
            .all()
        )
        assert len(arrs) == 1
        assert arrs[0].rule_version_id == resolved["R1"][0]
        assert arrs[0].rule_version == resolved["R1"][1]

        # full immutable trace: assessment -> ruleset -> ruleset_rules -> rule_version -> rule
        repo = RulesetRepo(session)
        rule_versions = repo.list_rule_versions(ruleset_id)
        assert [rv.version_id for rv in rule_versions] == [resolved["R1"][0]]
        assert rule_versions[0].rule_id == "R1"

        # an assessment cannot record two results for the same rule snapshot
        session.add(ArrORM(
            assessment_id=result["assessment_id"], rule_id="R1",
            rule_version=1, rule_version_id=resolved["R1"][0],
            status="PASS",
        ))
        with pytest.raises(IntegrityError):
            session.flush()


class TestFallbackGuard:
    def _seed_sources(self, session, *fallback_pairs):
        for source_id, fallback in fallback_pairs:
            session.add(SourceORM(
                source_id=source_id, country_id="AU", program_id="SKILLED_189",
                visa_category="SKILLED", authority="Home Affairs",
                url=f"https://x/{source_id}",
                source_type="HTML", status="ACTIVE",
                priority="SECONDARY", crawl_frequency="WEEKLY",
                fallback_source_id=fallback,
            ))
        session.flush()

    def test_self_reference_rejected(self, session):
        repo = SourceRepo(session)
        with pytest.raises(ValueError):
            repo.assert_safe_fallback("S1", "S1")

    def test_missing_fallback_target_rejected(self, session):
        _seed_country_program(session)
        self._seed_sources(session, ("S1", None))
        repo = SourceRepo(session)
        with pytest.raises(ValueError):
            repo.assert_safe_fallback("S1", "NOPE")

    def test_cycle_rejected(self, session):
        _seed_country_program(session)
        self._seed_sources(session, ("S1", None), ("S2", "S1"))
        repo = SourceRepo(session)
        with pytest.raises(ValueError):
            repo.assert_safe_fallback("S1", "S2")

    def test_create_guards_self_reference(self, session):
        repo = SourceRepo(session)
        with pytest.raises(ValueError):
            repo.create(
                source_id="S9", country_id="AU", program_id="SKILLED_189",
                url="https://x", source_type="HTML", fallback_source_id="S9",
            )


class TestExtractionRunIdempotency:
    def test_reuse_completed_and_never_reuse_failed(self, tmp_path):
        from db import init_schema, get_connection
        from phase2.requirement_repository import (
            create_extraction_run,
            complete_extraction_run,
        )

        db_path = tmp_path / "legacy_integrity.db"
        init_schema(get_connection(db_path), db_path)
        rconn = get_connection(db_path)
        _seed_legacy_document(rconn)

        run1 = create_extraction_run(rconn, "D1", 1, "mock-llm-v1", "p1", dry_run=False)
        run2 = create_extraction_run(rconn, "D1", 1, "mock-llm-v1", "p1", dry_run=False)
        assert run1 == run2

        complete_extraction_run(rconn, run1, {}, status="FAILED", error="boom")
        run3 = create_extraction_run(rconn, "D1", 1, "mock-llm-v1", "p1", dry_run=False)
        assert run3 != run1

    def test_missing_document_version_raises(self, tmp_path):
        from db import init_schema, get_connection
        from phase2.requirement_repository import create_extraction_run

        db_path = tmp_path / "legacy_missing.db"
        init_schema(get_connection(db_path), db_path)
        rconn = get_connection(db_path)
        with pytest.raises(ValueError):
            create_extraction_run(rconn, "D1", 1, "mock-llm-v1", "p1", dry_run=False)


class TestAuditHardening:
    """DB-level guards added by the audit-hardening migration (f8a9b0c1d2e3).

    Mirrored in the ORM so SQLite enforces the same invariants as live PostgreSQL.
    """

    def test_assessment_dropped_legacy_rule_version_column(self):
        assert "rule_version_id" not in [c.name for c in AssessmentORM.__table__.columns]

    def test_discovered_sources_valid_country_ok(self, fk_session):
        fk_session.add(CountryORM(country_id="AU", name="Australia", iso_code="AU"))
        fk_session.flush()
        fk_session.add(DiscoveredSourceORM(
            discovered_source_id="DS-1", seed_url="https://gov.au/seed",
            candidate_url="https://gov.au/candidate", confidence_score=80,
            suggested_country_id="AU",
        ))
        fk_session.flush()

    def test_discovered_sources_invalid_country_rejected(self, fk_session):
        fk_session.add(DiscoveredSourceORM(
            discovered_source_id="DS-2", seed_url="https://gov.au/seed",
            candidate_url="https://gov.au/candidate2", confidence_score=80,
            suggested_country_id="XX",
        ))
        with pytest.raises(IntegrityError):
            fk_session.flush()

    def test_discovered_sources_invalid_status_rejected(self, session):
        session.add(DiscoveredSourceORM(
            discovered_source_id="DS-3", seed_url="https://gov.au/seed",
            candidate_url="https://gov.au/candidate3", confidence_score=80,
            suggested_country_id="AU", status="WILDCARD",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_extraction_run_invalid_status_rejected(self, session):
        _seed_fk_chain(session)
        session.add(ExtractORM(
            run_id="RUN-BAD", document_id="D1", document_version=1,
            model="mock-llm-v1", prompt_version="p1", status="PARTIAL", dry_run=0,
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_pipeline_run_invalid_status_rejected(self, session):
        _seed_fk_chain(session)
        session.add(PipelineRunORM(
            run_id="PR-1", source_id="SRC-1", trigger_type="SCHEDULED",
            status="PAUSED", started_at=NOW,
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_gov_source_invalid_status_rejected(self, session):
        session.add(SourceORM(
            source_id="SRC-BAD", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov/bad", source_type="HTML",
            status="SUSPENDED", priority="SECONDARY",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_gov_source_invalid_fetch_status_rejected(self, session):
        session.add(SourceORM(
            source_id="SRC-FB", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov/fb", source_type="HTML",
            status="ACTIVE", priority="SECONDARY", fetch_status="MAYBE",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_two_active_rules_same_field_rejected(self, session):
        session.add(RuleORM(
            rule_id="R1", requirement_id="REQ-X", program_id="SKILLED_189",
            category="age", field="age", operator="LT", value_json="45",
            mandatory=True, critical=True, message="Under 45",
            status="ACTIVE", current_version=1,
        ))
        session.flush()
        session.add(RuleORM(
            rule_id="R2", requirement_id="REQ-X", program_id="SKILLED_189",
            category="age", field="age", operator="GT", value_json="18",
            mandatory=False, critical=False, message="Over 18",
            status="ACTIVE", current_version=1,
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_non_active_rule_same_field_allowed(self, session):
        session.add(RuleORM(
            rule_id="R1", requirement_id="REQ-X", program_id="SKILLED_189",
            category="age", field="age", operator="LT", value_json="45",
            mandatory=True, critical=True, message="Under 45",
            status="ACTIVE", current_version=1,
        ))
        session.flush()
        session.add(RuleORM(
            rule_id="R2", requirement_id="REQ-X", program_id="SKILLED_189",
            category="age", field="age", operator="GT", value_json="18",
            mandatory=False, critical=False, message="Over 18",
            status="DRAFT", current_version=1,
        ))
        session.flush()


class TestGovernmentSourceUrlUniqueness:
    """government_sources.url is globally unique (ORM unique=True and DB UNIQUE).

    Migrated since the initial schema (49b9088f3a03, UniqueConstraint('url')) and
    verified on live PostgreSQL as `government_sources_url_key`. This test pins the
    DB-level behaviour so an accidental regression can never be reintroduced.
    """

    def test_duplicate_url_rejected(self, session):
        _seed_country_program(session)
        session.add(SourceORM(
            source_id="SRC-A", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov/unique-src", source_type="HTML",
            status="ACTIVE", priority="SECONDARY", crawl_frequency="WEEKLY",
        ))
        session.flush()
        session.add(SourceORM(
            source_id="SRC-B", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov/unique-src", source_type="HTML",
            status="ACTIVE", priority="SECONDARY", crawl_frequency="WEEKLY",
        ))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_distinct_urls_allowed(self, session):
        _seed_country_program(session)
        session.add(SourceORM(
            source_id="SRC-A", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov/src-a", source_type="HTML",
            status="ACTIVE", priority="SECONDARY", crawl_frequency="WEEKLY",
        ))
        session.flush()
        session.add(SourceORM(
            source_id="SRC-B", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.gov/src-b", source_type="HTML",
            status="ACTIVE", priority="SECONDARY", crawl_frequency="WEEKLY",
        ))
        session.flush()