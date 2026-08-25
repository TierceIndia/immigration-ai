"""Tests for Phase 3A rule compiler."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest
from phase3.models import RuleStatus
from phase3.rule_compiler import compile_requirement_to_rule


def _make_req(**overrides):
    defaults = dict(
        category="AGE",
        field="applicant.age",
        operator="LT",
        value=45,
        unit="years",
        mandatory=True,
        criticality="CRITICAL",
        name="Age requirement",
        description="Applicant must be under 45",
        evidence=[{"chunk_id": 3, "evidence_text": "You must be aged under 45", "evidence_type": "DIRECT_TEXT"}],
        _source_id="SRC-AU-189-MAIN",
        _document_id="DOC-AU-189-MAIN",
        _document_version=1,
        _requirement_id="REQ-189-AGE-001",
        _program_id="SKILLED_189",
    )
    defaults.update(overrides)
    return defaults


def test_compile_basic():
    rule = compile_requirement_to_rule(_make_req(), "RULE-189-AGE-001")
    assert rule.rule_id == "RULE-189-AGE-001"
    assert rule.category == "AGE"
    assert rule.field == "applicant.age"
    assert rule.operator == "LT"
    assert rule.value == 45
    assert rule.mandatory is True
    assert rule.critical is True


def test_compile_provenance():
    rule = compile_requirement_to_rule(_make_req(), "RULE-001")
    assert rule.provenance.source_id == "SRC-AU-189-MAIN"
    assert rule.provenance.document_id == "DOC-AU-189-MAIN"
    assert rule.provenance.document_version == 1
    assert rule.provenance.chunk_id == 3
    assert rule.provenance.evidence_text == "You must be aged under 45"


def test_compile_status_draft():
    rule = compile_requirement_to_rule(_make_req(), "RULE-001")
    assert rule.status == RuleStatus.DRAFT


def test_compile_with_condition():
    req = _make_req(condition={"if": "visa_type", "equals": "190"})
    rule = compile_requirement_to_rule(req, "RULE-001")
    assert rule.condition["if"] == "visa_type"


def test_compile_with_exception():
    req = _make_req(exception={"description": "Age exception for certain categories"})
    rule = compile_requirement_to_rule(req, "RULE-001")
    assert "exception" in rule.exception["description"]


def test_compile_rejects_unsupported_operator():
    req = _make_req(operator="BETWEEN")
    with pytest.raises(ValueError, match="Unsupported operator"):
        compile_requirement_to_rule(req, "RULE-001")


def test_compile_rejects_missing_evidence():
    req = _make_req(evidence=[])
    with pytest.raises(ValueError, match="evidence"):
        compile_requirement_to_rule(req, "RULE-001")


def test_compile_operator_unknown_allowed():
    req = _make_req(operator="UNKNOWN")
    rule = compile_requirement_to_rule(req, "RULE-001")
    assert rule.operator == "UNKNOWN"
