"""Tests for enhanced rule repository — persistence, status machine, activation."""
from __future__ import annotations

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.models import Base, Rule as RuleORM, RuleVersion
from db.connection import get_session_factory
from rules.rule_repository import EnhancedRuleRepo, orm_to_dataclass
from rules.models import RuleStatus


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
    factory = get_session_factory(engine)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@pytest.fixture()
def repo(session):
    return EnhancedRuleRepo(session)


def _make_rule_orm(rule_id="RULE-AGE-001", program_id="SKILLED_189",
                   category="AGE", field="age", operator="GTE",
                   value=25, status="DRAFT"):
    return RuleORM(
        rule_id=rule_id,
        requirement_id="REQ-AGE-001",
        program_id=program_id,
        category=category,
        field=field,
        operator=operator,
        value_json=json.dumps(value),
        unit="years",
        mandatory=True,
        critical=True,
        message="Must be at least 25",
        status=status,
        current_version=1,
        source_id="SRC-AU-189",
        document_id="DOC-AU-189",
        document_version=1,
        chunk_id=100,
        evidence_text="Applicants must be at least 25 years of age",
    )


def _create_rule(session, rule_id="R-1", status="DRAFT", program_id="P1",
                 category="AGE", field="age", operator="GTE", value=1):
    """Create a Rule ORM object directly in the session."""
    obj = RuleORM(
        rule_id=rule_id,
        requirement_id=f"REQ-{rule_id}",
        program_id=program_id,
        category=category,
        field=field,
        operator=operator,
        value_json=json.dumps(value),
        status=status,
        current_version=1,
        message=f"Rule {rule_id}",
    )
    session.add(obj)
    session.flush()
    return obj


class TestCRUD:
    def test_create_and_get(self, session, repo):
        _create_rule(session, "R-1", category="AGE", field="age", operator="GTE")
        assert repo.get("R-1") is not None

    def test_get_nonexistent_returns_none(self, repo):
        assert repo.get("NOPE") is None

    def test_count(self, session, repo):
        assert repo.count() == 0
        _create_rule(session, "R-1", category="AGE", field="age", operator="GTE")
        assert repo.count() == 1

    def test_list_by_status(self, session, repo):
        _create_rule(session, "R-1", status="DRAFT", category="AGE", field="age", operator="GTE")
        _create_rule(session, "R-2", status="ACTIVE", category="AGE", field="age", operator="GTE")
        assert len(repo.list_by_status("DRAFT")) == 1
        assert len(repo.list_by_status("ACTIVE")) == 1
        assert len(repo.list_by_status("RETIRED")) == 0


class TestStatusTransitions:
    def test_validate_rule(self, session, repo):
        _create_rule(session, "R-1", status="DRAFT", category="AGE", field="age", operator="GTE")
        result = repo.validate_rule("R-1", actor="admin1")
        assert result.status == "VALIDATED"

    def test_approve_rule(self, session, repo):
        _create_rule(session, "R-1", status="VALIDATED", category="AGE", field="age", operator="GTE")
        result = repo.approve_rule("R-1", actor="admin1")
        assert result.status == "APPROVED"

    def test_activate_rule(self, session, repo):
        _create_rule(session, "R-1", status="APPROVED", category="AGE", field="age", operator="GTE")
        result = repo.activate_rule("R-1", actor="admin1")
        assert result.status == "ACTIVE"

    def test_invalid_transition_raises(self, session, repo):
        _create_rule(session, "R-1", status="DRAFT", category="AGE", field="age", operator="GTE")
        with pytest.raises(ValueError, match="Invalid rule status transition"):
            repo.activate_rule("R-1", actor="admin1")

    def test_reject_rule(self, session, repo):
        _create_rule(session, "R-1", status="DRAFT", category="AGE", field="age", operator="GTE")
        result = repo.reject_rule("R-1", actor="admin1")
        assert result.status == "REJECTED"


class TestTransactionalActivation:
    def test_activate_supersedes_old(self, session, repo):
        """Activating R-2 should supersede R-1 if both are for same field."""
        _create_rule(session, "R-1", status="ACTIVE", category="AGE", field="age",
                     operator="LT", value=45)
        _create_rule(session, "R-2", status="APPROVED", category="AGE", field="age",
                     operator="LT", value=50)

        repo.activate_rule("R-2", actor="admin1")

        old = repo.get("R-1")
        new = repo.get("R-2")
        assert old.status == "SUPERSEDED"
        assert new.status == "ACTIVE"

    def test_activate_different_field_no_supersede(self, session, repo):
        """Activating a rule for a different field should NOT supersede the old one."""
        _create_rule(session, "R-AGE", status="ACTIVE", category="AGE", field="age",
                     operator="LT", value=45)
        _create_rule(session, "R-PTS", status="APPROVED", category="POINTS", field="points",
                     operator="GTE", value=65)

        repo.activate_rule("R-PTS", actor="admin1")

        assert repo.get("R-AGE").status == "ACTIVE"
        assert repo.get("R-PTS").status == "ACTIVE"

    def test_version_snapshot_saved(self, session, repo):
        _create_rule(session, "R-1", status="APPROVED", category="AGE", field="age", operator="GTE")
        repo.activate_rule("R-1", actor="admin1")
        repo.save_version_snapshot("R-1", "ACTIVATED", "admin1")

        versions = session.query(RuleVersion).filter(
            RuleVersion.rule_id == "R-1"
        ).all()
        assert len(versions) >= 1


class TestLoadingDataclasses:
    def test_orm_to_dataclass(self, session):
        orm = _make_rule_orm()
        session.add(orm)
        session.flush()

        dc = orm_to_dataclass(orm)
        assert dc.rule_id == "RULE-AGE-001"
        assert dc.field == "age"
        assert dc.operator == "GTE"
        assert dc.value == 25
        assert dc.status == RuleStatus.DRAFT
        assert dc.provenance.source_id == "SRC-AU-189"

    def test_list_active_as_dataclasses(self, session, repo):
        _create_rule(session, "R-1", status="ACTIVE", category="AGE", field="age",
                     operator="GTE", value=25)
        # Set provenance fields
        orm = repo.get("R-1")
        orm.source_id = "SRC"
        orm.document_id = "DOC"
        orm.document_version = 1
        orm.chunk_id = 1
        orm.evidence_text = "test"
        session.flush()

        result = repo.list_active_as_dataclasses("P1")
        assert len(result) == 1
        assert result[0].rule_id == "R-1"
        assert isinstance(result[0].value, int)
