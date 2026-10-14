"""Tests for scripts/seed_review_demo.py (dev-only Review-screen demo data)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from db.models import (
    Base, Country, DocumentChunk, DocumentVersion, ExtractionRun,
    GovernmentDocument, GovernmentSource, ImmigrationProgram, Requirement,
    RequirementEvidence, RequirementVersion, Rule, RuleVersion,
)
from db.connection import get_session_factory

import seed_review_demo as demo


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite does not enforce foreign keys unless told to, per-connection -
    # without this, a wrong DELETE order here would pass silently under
    # SQLite and only blow up as a real ForeignKeyViolation on Postgres.
    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _seed_real_document(engine):
    """A real (non-DEMO) document/version, so the anchor-reuse path is exercised.

    Same no-relationship()/no-auto-ordering caveat as scripts/seed_review_demo.py
    itself (see its own comments): each of these depends on the previous one
    (country <- program <- source <- document <- version), so each needs its
    own flush rather than trusting unit-of-work ordering across unrelated
    mappers. Without this, PRAGMA foreign_keys=ON (the `engine` fixture above)
    raises a real IntegrityError on INSERT, exactly as Postgres would.
    """
    from datetime import datetime, timezone
    with get_session_factory(engine)() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        s.flush()
        s.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        s.flush()
        s.merge(GovernmentSource(
            source_id="SRC-AU-189", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.invalid/189", source_type="HTML",
            priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE",
        ))
        s.flush()
        s.merge(GovernmentDocument(
            document_id="DOC-AU-189", source_id="SRC-AU-189", country_id="AU",
            program_id="SKILLED_189", visa_category="SKILLED", document_type="GENERAL",
            url="https://example.invalid/189", title="Real doc",
            retrieved_at=datetime.now(timezone.utc), content_hash="real123",
            current_version=1, language="en", raw_path="fetch:doc:v1", status="CURRENT",
        ))
        s.flush()
        s.merge(DocumentVersion(
            document_id="DOC-AU-189", version_number=1, content_hash="real123",
            retrieved_at=datetime.now(timezone.utc), raw_path="fetch:doc:v1",
            change_type="INITIAL",
        ))
        s.commit()


class TestRefusesInProduction:
    def test_refuses_when_environment_is_production(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="production"):
            demo._refuse_in_production()

    def test_allows_development_and_unset(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        demo._refuse_in_production()  # does not raise
        monkeypatch.setenv("ENVIRONMENT", "development")
        demo._refuse_in_production()  # does not raise


class TestSeedAndRemove:
    def test_seed_creates_exactly_four_requirements_with_real_chunks(self, engine):
        _seed_real_document(engine)
        demo.seed(engine)

        with get_session_factory(engine)() as s:
            reqs = s.execute(
                select(Requirement).where(Requirement.requirement_id.in_(demo.DEMO_REQUIREMENT_IDS))
            ).scalars().all()
            assert len(reqs) == 4
            assert all(r.name.startswith("[DEMO]") for r in reqs)

            # Reused the REAL document version rather than fabricating one.
            assert all(r.document_version_id is not None for r in reqs)
            versions = {r.document_version_id for r in reqs}
            real_version = s.execute(
                select(DocumentVersion).filter_by(document_id="DOC-AU-189")
            ).scalar_one()
            assert versions == {real_version.version_id}

            # Every evidence row points at a real, non-null chunk whose text
            # matches exactly (the FK constraint alone proves the chunk
            # exists; this proves the text is genuinely linked, not just a
            # dangling id).
            for r in reqs:
                ev = s.execute(
                    select(RequirementEvidence).where(RequirementEvidence.requirement_id == r.requirement_id)
                ).scalar_one()
                chunk = s.get(DocumentChunk, ev.chunk_id)
                assert chunk is not None
                assert chunk.raw_text == ev.evidence_text

    def test_conflict_pair_resolves_previous_value_via_the_real_join(self, engine):
        """Exercises the actual admin_requirements.py::_req_to_review_item
        conflict-sibling lookup, not a reimplementation of it."""
        _seed_real_document(engine)
        demo.seed(engine)

        import sys as _sys
        sys_path_backup = list(_sys.path)
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        try:
            from api.routes.admin_requirements import _req_to_review_item
        finally:
            _sys.path[:] = sys_path_backup

        with get_session_factory(engine)() as s:
            new_req = s.get(Requirement, "REQ-DEMO-CONFLICT-NEW")
            item = _req_to_review_item(s, new_req)
            assert item["previous_value"]["requirement_id"] == "REQ-DEMO-CONFLICT-OLD"
            assert item["previous_value"]["value"] == "COMPETENT"

    def test_creates_a_labelled_fallback_anchor_when_no_document_exists(self, engine):
        """No real document anywhere - the script must still work, by
        creating its own clearly-[DEMO]-labelled chain."""
        demo.seed(engine)

        with get_session_factory(engine)() as s:
            doc = s.get(GovernmentDocument, demo.DEMO_DOCUMENT_ID)
            assert doc is not None
            assert doc.title.startswith("[DEMO]")
            source = s.get(GovernmentSource, demo.DEMO_SOURCE_ID)
            assert source.status == "DISABLED"  # never picked up by a real sync

    def test_seed_is_not_run_twice_without_remove(self, engine, capsys):
        _seed_real_document(engine)
        demo.seed(engine)
        demo.seed(engine)  # second call must not duplicate or raise
        captured = capsys.readouterr()
        assert "already present" in captured.out

        with get_session_factory(engine)() as s:
            reqs = s.execute(
                select(Requirement).where(Requirement.requirement_id.in_(demo.DEMO_REQUIREMENT_IDS))
            ).scalars().all()
            assert len(reqs) == 4

    def test_remove_deletes_exactly_the_demo_rows_and_nothing_else(self, engine):
        _seed_real_document(engine)
        demo.seed(engine)
        demo.remove(engine)

        with get_session_factory(engine)() as s:
            reqs = s.execute(
                select(Requirement).where(Requirement.requirement_id.in_(demo.DEMO_REQUIREMENT_IDS))
            ).scalars().all()
            assert reqs == []

            demo_chunks = s.execute(
                select(DocumentChunk).where(DocumentChunk.heading_path == demo.DEMO_CHUNK_HEADING)
            ).scalars().all()
            assert demo_chunks == []

            # The REAL document/source/version seeded by the test fixture
            # must survive untouched.
            assert s.get(GovernmentDocument, "DOC-AU-189") is not None
            assert s.get(GovernmentSource, "SRC-AU-189") is not None

    def test_remove_skips_a_requirement_with_real_rule_version_history(self, engine, capsys):
        """The real finding from a live Postgres run: once a reviewer has
        actually validated/approved a [DEMO] requirement through the real
        Review screen, its compiled rule gets a RuleVersion row - and
        rule_versions is unconditionally append-only on Postgres. That one
        requirement can never be removed again; remove() must skip exactly
        it (and the chunk/evidence only it uses), remove the other three,
        and say so - not crash."""
        _seed_real_document(engine)
        demo.seed(engine)

        with get_session_factory(engine)() as s:
            req = s.get(Requirement, "REQ-DEMO-NEW")
            rule = Rule(
                rule_id="RULE-REQ-DEMO-NEW", requirement_id=req.requirement_id,
                program_id=req.program_id, category=req.category, field=req.field,
                operator=req.operator, value_json=req.value_json, mandatory=True,
                critical=False, message="demo rule", status="DRAFT", current_version=1,
            )
            s.add(rule)
            s.flush()
            s.add(RuleVersion(
                rule_id=rule.rule_id, version_number=1,
                snapshot_json={"field": req.field}, change_type="CREATED",
            ))
            s.commit()

        demo.remove(engine)
        captured = capsys.readouterr()
        assert "REQ-DEMO-NEW" in captured.out
        assert "immutable" in captured.out.lower()

        with get_session_factory(engine)() as s:
            # The locked requirement, its rule, and its rule's version all survive.
            assert s.get(Requirement, "REQ-DEMO-NEW") is not None
            assert s.get(Rule, "RULE-REQ-DEMO-NEW") is not None
            assert s.execute(
                select(RuleVersion).where(RuleVersion.rule_id == "RULE-REQ-DEMO-NEW")
            ).scalar_one_or_none() is not None
            # Its evidence/chunk survive too (still referenced).
            assert s.execute(
                select(RequirementEvidence).where(RequirementEvidence.requirement_id == "REQ-DEMO-NEW")
            ).scalar_one_or_none() is not None

            # The other three removable demo requirements are gone.
            remaining = {
                r.requirement_id for r in s.execute(
                    select(Requirement).where(Requirement.requirement_id.in_(demo.DEMO_REQUIREMENT_IDS))
                ).scalars().all()
            }
            assert remaining == {"REQ-DEMO-NEW"}

    def test_remove_cleans_up_the_run_but_keeps_the_fallback_anchor_chain(self, engine):
        """document_versions is unconditionally append-only on real Postgres
        (a BEFORE DELETE/UPDATE trigger, invisible to SQLite) and
        government_documents/government_sources are then permanently FK-held
        by that version row regardless - so once this fallback chain is
        created, it is a permanent part of the historical record, same as
        any real document version. Only the ExtractionRun (not append-only)
        is ever removed."""
        demo.seed(engine)  # no real document -> creates its own anchor
        demo.remove(engine)

        with get_session_factory(engine)() as s:
            assert s.get(GovernmentDocument, demo.DEMO_DOCUMENT_ID) is not None
            assert s.get(GovernmentSource, demo.DEMO_SOURCE_ID) is not None
            assert s.get(ExtractionRun, demo.DEMO_RUN_ID) is None

    def test_remove_when_nothing_was_seeded_is_a_safe_no_op(self, engine, capsys):
        demo.remove(engine)
        captured = capsys.readouterr()
        assert "nothing to remove" in captured.out.lower()

    def test_seed_remove_seed_remove_round_trip_with_fk_enforcement(self, engine):
        """Reproduces the production bug: document_chunks was deleted while
        requirement_evidence still pointed at it (requirement_evidence_chunk_id_fkey).
        SQLite silently allowed the wrong order by default; with the
        PRAGMA foreign_keys=ON connection in the `engine` fixture above, this
        round-trip raises the same IntegrityError Postgres did unless the
        delete order in seed_review_demo.remove() is actually FK-safe."""
        _seed_real_document(engine)

        demo.seed(engine)
        demo.remove(engine)
        demo.seed(engine)
        demo.remove(engine)

        with get_session_factory(engine)() as s:
            reqs = s.execute(
                select(Requirement).where(Requirement.requirement_id.in_(demo.DEMO_REQUIREMENT_IDS))
            ).scalars().all()
            assert reqs == []
            demo_chunks = s.execute(
                select(DocumentChunk).where(DocumentChunk.heading_path == demo.DEMO_CHUNK_HEADING)
            ).scalars().all()
            assert demo_chunks == []
            # The real document/source from the fixture must still survive
            # two full round trips untouched.
            assert s.get(GovernmentDocument, "DOC-AU-189") is not None
            assert s.get(GovernmentSource, "SRC-AU-189") is not None
