"""Tests for Phase 3A rule engine."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phase3.models import Rule, Provenance, EvaluationStatus
from phase3.rule_engine import evaluate_rule, evaluate_all_rules


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


# --- PASS tests ---

def test_pass_basic():
    rule = _make_rule()
    result = evaluate_rule(rule, {"applicant.age": 30})
    assert result.status == EvaluationStatus.PASS


def test_pass_boundary():
    rule = _make_rule()
    result = evaluate_rule(rule, {"applicant.age": 44})
    assert result.status == EvaluationStatus.PASS


def test_pass_gte():
    rule = _make_rule(operator="GTE", value=65, field="applicant.points_score")
    result = evaluate_rule(rule, {"applicant.points_score": 70})
    assert result.status == EvaluationStatus.PASS


def test_pass_in():
    rule = _make_rule(operator="IN", value=["substantive", "BVA"], field="applicant.visa_status")
    result = evaluate_rule(rule, {"applicant.visa_status": "substantive"})
    assert result.status == EvaluationStatus.PASS


# --- FAIL tests ---

def test_fail_basic():
    rule = _make_rule()
    result = evaluate_rule(rule, {"applicant.age": 50})
    assert result.status == EvaluationStatus.FAIL


def test_fail_boundary():
    rule = _make_rule()
    result = evaluate_rule(rule, {"applicant.age": 45})
    assert result.status == EvaluationStatus.FAIL


def test_fail_gte():
    rule = _make_rule(operator="GTE", value=65, field="applicant.points_score")
    result = evaluate_rule(rule, {"applicant.points_score": 60})
    assert result.status == EvaluationStatus.FAIL


def test_fail_in():
    rule = _make_rule(operator="IN", value=["substantive", "BVA"], field="applicant.visa_status")
    result = evaluate_rule(rule, {"applicant.visa_status": "tourist"})
    assert result.status == EvaluationStatus.FAIL


# --- UNKNOWN tests ---

def test_unknown_missing_field():
    rule = _make_rule()
    result = evaluate_rule(rule, {})
    assert result.status == EvaluationStatus.UNKNOWN


def test_unknown_none_value():
    rule = _make_rule()
    result = evaluate_rule(rule, {"applicant.age": None})
    assert result.status == EvaluationStatus.UNKNOWN


def test_unknown_reason():
    rule = _make_rule()
    result = evaluate_rule(rule, {})
    assert "not provided" in result.reason


# --- EXISTS / NOT_EXISTS ---

def test_exists_pass():
    rule = _make_rule(operator="EXISTS", value=None)
    result = evaluate_rule(rule, {"applicant.age": 30})
    assert result.status == EvaluationStatus.PASS


def test_exists_fail():
    rule = _make_rule(operator="EXISTS", value=None)
    result = evaluate_rule(rule, {})
    assert result.status == EvaluationStatus.FAIL


def test_not_exists_pass():
    rule = _make_rule(operator="NOT_EXISTS", value=None)
    result = evaluate_rule(rule, {})
    assert result.status == EvaluationStatus.PASS


def test_not_exists_fail():
    rule = _make_rule(operator="NOT_EXISTS", value=None)
    result = evaluate_rule(rule, {"applicant.age": 30})
    assert result.status == EvaluationStatus.FAIL


# --- evaluate_all_rules ---

def test_all_pass():
    rules = [
        _make_rule(rule_id="R1", field="f1", operator="LT", value=45),
        _make_rule(rule_id="R2", field="f2", operator="GTE", value=65),
    ]
    data = {"f1": 30, "f2": 70}
    result = evaluate_all_rules(rules, data, "SKILLED_189")
    assert result.overall_status == EvaluationStatus.PASS
    assert result.passed == 2
    assert result.failed == 0
    assert result.unknown == 0


def test_one_fail():
    rules = [
        _make_rule(rule_id="R1", field="f1", operator="LT", value=45),
        _make_rule(rule_id="R2", field="f2", operator="GTE", value=65),
    ]
    data = {"f1": 50, "f2": 70}
    result = evaluate_all_rules(rules, data, "SKILLED_189")
    assert result.overall_status == EvaluationStatus.FAIL
    assert result.failed == 1


def test_unknown_no_fail():
    rules = [
        _make_rule(rule_id="R1", field="f1", operator="LT", value=45),
        _make_rule(rule_id="R2", field="f2", operator="GTE", value=65),
    ]
    data = {"f1": 30}
    result = evaluate_all_rules(rules, data, "SKILLED_189")
    assert result.overall_status == EvaluationStatus.UNKNOWN
    assert result.unknown == 1
    assert result.passed == 1


def test_fail_takes_precedence_over_unknown():
    rules = [
        _make_rule(rule_id="R1", field="f1", operator="LT", value=45),
        _make_rule(rule_id="R2", field="f2", operator="GTE", value=65),
    ]
    data = {"f1": 50}
    result = evaluate_all_rules(rules, data, "SKILLED_189")
    assert result.overall_status == EvaluationStatus.FAIL
    assert result.failed == 1
    assert result.unknown == 1
