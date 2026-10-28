"""Tests for Phase 3A models."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rules.models import Rule, Provenance, RuleEvaluation, EvaluationStatus, EligibilityResult, RuleStatus


def _make_rule(**overrides):
    prov = Provenance(
        source_id="SRC-AU-189-MAIN",
        document_id="DOC-AU-189-MAIN",
        document_version=1,
        chunk_id=3,
        requirement_id="REQ-189-AGE-001",
        evidence_text="You must be aged under 45",
    )
    defaults = dict(
        rule_id="RULE-189-AGE-001",
        requirement_id="REQ-189-AGE-001",
        program_id="SKILLED_189",
        category="AGE",
        field="applicant.age",
        operator="LT",
        value=45,
        unit="years",
        mandatory=True,
        critical=True,
        message="Applicant must be under 45",
        provenance=prov,
    )
    defaults.update(overrides)
    return Rule(**defaults)


def test_rule_to_dict_and_from_dict():
    rule = _make_rule()
    d = rule.to_dict()
    restored = Rule.from_dict(d)
    assert restored.rule_id == rule.rule_id
    assert restored.field == rule.field
    assert restored.operator == rule.operator
    assert restored.value == rule.value
    assert restored.provenance.source_id == rule.provenance.source_id


def test_rule_status_default():
    rule = _make_rule()
    assert rule.status == RuleStatus.DRAFT


def test_rule_status_active():
    rule = _make_rule(status=RuleStatus.ACTIVE)
    assert rule.status == RuleStatus.ACTIVE


def test_rule_provenance_preserved():
    rule = _make_rule()
    assert rule.provenance.source_id == "SRC-AU-189-MAIN"
    assert rule.provenance.document_version == 1
    assert rule.provenance.chunk_id == 3
    assert rule.provenance.evidence_text == "You must be aged under 45"


def test_rule_serialization_roundtrip():
    rule = _make_rule(version=2, condition={"if": "visa_type", "equals": "190"})
    d = rule.to_dict()
    assert d["version"] == 2
    assert d["condition"]["if"] == "visa_type"
    restored = Rule.from_dict(d)
    assert restored.condition["equals"] == "190"


def test_evaluation_status_values():
    assert EvaluationStatus.PASS.value == "PASS"
    assert EvaluationStatus.FAIL.value == "FAIL"
    assert EvaluationStatus.UNKNOWN.value == "UNKNOWN"
    assert EvaluationStatus.ERROR.value == "ERROR"


def test_rule_evaluation_to_dict():
    ev = RuleEvaluation(
        rule_id="RULE-189-AGE-001",
        status=EvaluationStatus.PASS,
        field="applicant.age",
        actual_value=30,
        expected_value=45,
        operator="LT",
        message="Must be under 45",
        reason="Got 30, expected < 45",
    )
    d = ev.to_dict()
    assert d["status"] == "PASS"
    assert d["actual_value"] == 30


def test_eligibility_result_to_dict():
    ev_pass = RuleEvaluation(
        rule_id="R1", status=EvaluationStatus.PASS,
        field="f", actual_value=1, expected_value=2, operator="LT",
        message="m", reason="r",
    )
    ev_fail = RuleEvaluation(
        rule_id="R2", status=EvaluationStatus.FAIL,
        field="f", actual_value=5, expected_value=2, operator="LT",
        message="m", reason="r",
    )
    result = EligibilityResult(
        program_id="SKILLED_189",
        overall_status=EvaluationStatus.FAIL,
        rule_evaluations=[ev_pass, ev_fail],
        passed=1, failed=1, unknown=0, not_applicable=0, total=2,
    )
    d = result.to_dict()
    assert d["overall_status"] == "FAIL"
    assert len(d["rule_evaluations"]) == 2
