"""Integration tests for Phase 3A with real requirement data."""
from __future__ import annotations
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rules.models import EvaluationStatus, RuleEvaluation
from rules.rule_compiler import compile_requirement_to_rule
from rules.rule_engine import evaluate_rule, evaluate_all_rules
from rules.explanations import explain_evaluation, explain_eligibility


def _load_real_requirements():
    gold_path = ROOT / "data" / "gold" / "requirements_gold_real.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    return [item for item in gold["items"] if item["expected"] is not None]


def test_compile_and_evaluate_age():
    reqs = _load_real_requirements()
    age_req = next(r for r in reqs if r["category"] == "AGE")

    rule = compile_requirement_to_rule(
        {**age_req["expected"], "category": age_req["category"],
         "name": "Age", "description": "Must be under 45",
         "evidence": [{"chunk_id": age_req["chunk_id"], "evidence_text": age_req["evidence_text"]}],
         "_source_id": "SRC-AU-189-MAIN", "_document_id": "DOC-AU-189-MAIN",
         "_document_version": 1, "_requirement_id": age_req["gold_id"],
         "_program_id": "SKILLED_189"},
        "RULE-189-AGE-001",
    )

    assert rule.field == "applicant.age"
    assert rule.operator == "LT"
    assert rule.value == 45

    assert evaluate_rule(rule, {"applicant.age": 30}).status == EvaluationStatus.PASS
    assert evaluate_rule(rule, {"applicant.age": 50}).status == EvaluationStatus.FAIL
    assert evaluate_rule(rule, {}).status == EvaluationStatus.UNKNOWN


def test_compile_and_evaluate_points():
    reqs = _load_real_requirements()
    pts_req = next(r for r in reqs if r["category"] == "POINTS")

    rule = compile_requirement_to_rule(
        {**pts_req["expected"], "category": pts_req["category"],
         "name": "Points", "description": "Must have 65+",
         "evidence": [{"chunk_id": pts_req["chunk_id"], "evidence_text": pts_req["evidence_text"]}],
         "_source_id": "SRC-AU-189-MAIN", "_document_id": "DOC-AU-189-MAIN",
         "_document_version": 1, "_requirement_id": pts_req["gold_id"],
         "_program_id": "SKILLED_189"},
        "RULE-189-POINTS-001",
    )

    assert evaluate_rule(rule, {"applicant.points_score": 70}).status == EvaluationStatus.PASS
    assert evaluate_rule(rule, {"applicant.points_score": 60}).status == EvaluationStatus.FAIL
    assert evaluate_rule(rule, {}).status == EvaluationStatus.UNKNOWN


def test_compile_and_evaluate_english():
    reqs = _load_real_requirements()
    eng_req = next(r for r in reqs if r["category"] == "ENGLISH_LANGUAGE")

    rule = compile_requirement_to_rule(
        {**eng_req["expected"], "category": eng_req["category"],
         "name": "English", "description": "Must have competent English",
         "evidence": [{"chunk_id": eng_req["chunk_id"], "evidence_text": eng_req["evidence_text"]}],
         "_source_id": "SRC-AU-189-MAIN", "_document_id": "DOC-AU-189-MAIN",
         "_document_version": 1, "_requirement_id": eng_req["gold_id"],
         "_program_id": "SKILLED_189"},
        "RULE-189-ENGLISH-001",
    )

    assert evaluate_rule(rule, {"applicant.english_level": "proficient"}).status == EvaluationStatus.PASS
    assert evaluate_rule(rule, {"applicant.english_level": "basic"}).status == EvaluationStatus.FAIL
    assert evaluate_rule(rule, {}).status == EvaluationStatus.UNKNOWN


def _compile_simple(category, field, op, val, name, ev_text, ev_chunk, req_id):
    return compile_requirement_to_rule(
        {"category": category, "field": field, "operator": op, "value": val,
         "mandatory": True, "criticality": "CRITICAL", "name": name,
         "evidence": [{"chunk_id": ev_chunk, "evidence_text": ev_text}],
         "_source_id": "SRC", "_document_id": "DOC", "_document_version": 1,
         "_requirement_id": req_id, "_program_id": "SKILLED_189"},
        f"RULE-{req_id}",
    )


def test_full_eligibility_eligible_applicant():
    rules = [
        _compile_simple("AGE", "applicant.age", "LT", 45, "Age", "Under 45", 3, "R1"),
        _compile_simple("POINTS", "applicant.points_score", "GTE", 65, "Points", "65 points", 1, "R2"),
        _compile_simple("ENGLISH_LANGUAGE", "applicant.english_level", "GTE", "competent", "English", "Competent", 6, "R3"),
    ]

    applicant = {
        "applicant.age": 30,
        "applicant.points_score": 70,
        "applicant.english_level": "proficient",
    }

    result = evaluate_all_rules(rules, applicant, "SKILLED_189")
    assert result.overall_status == EvaluationStatus.PASS
    assert result.passed == 3
    assert result.failed == 0
    assert result.unknown == 0


def test_full_eligibility_incomplete_applicant():
    rules = [
        _compile_simple("AGE", "applicant.age", "LT", 45, "Age", "Under 45", 3, "R1"),
        _compile_simple("POINTS", "applicant.points_score", "GTE", 65, "Points", "65 points", 1, "R2"),
    ]

    applicant = {"applicant.age": 30}

    result = evaluate_all_rules(rules, applicant, "SKILLED_189")
    assert result.overall_status == EvaluationStatus.UNKNOWN
    assert result.passed == 1
    assert result.unknown == 1


def test_explain_evaluation_pass():
    ev = RuleEvaluation(
        rule_id="R1", status=EvaluationStatus.PASS,
        field="applicant.age", actual_value=30, expected_value=45,
        operator="LT", message="Must be under 45", reason="Got 30, expected < 45",
    )
    explanation = explain_evaluation(ev)
    assert "PASS" in explanation
    assert "applicant.age" in explanation


def test_explain_evaluation_fail():
    ev = RuleEvaluation(
        rule_id="R1", status=EvaluationStatus.FAIL,
        field="applicant.age", actual_value=50, expected_value=45,
        operator="LT", message="Must be under 45", reason="Got 50, expected < 45",
    )
    explanation = explain_evaluation(ev)
    assert "FAIL" in explanation


def test_explain_evaluation_unknown():
    ev = RuleEvaluation(
        rule_id="R1", status=EvaluationStatus.UNKNOWN,
        field="applicant.age", actual_value=None, expected_value=45,
        operator="LT", message="Must be under 45", reason="not provided",
    )
    explanation = explain_evaluation(ev)
    assert "UNKNOWN" in explanation
    assert "not provided" in explanation


def test_provenance_preserved_through_compile_and_evaluate():
    rule = compile_requirement_to_rule(
        {"category": "AGE", "field": "applicant.age", "operator": "LT", "value": 45,
         "mandatory": True, "criticality": "CRITICAL", "name": "Age",
         "evidence": [{"chunk_id": 3, "evidence_text": "Under 45"}],
         "_source_id": "SRC-AU-189-MAIN", "_document_id": "DOC-AU-189-MAIN",
         "_document_version": 1, "_requirement_id": "REQ-AGE-001", "_program_id": "SKILLED_189"},
        "RULE-001",
    )
    ev = evaluate_rule(rule, {"applicant.age": 30})
    assert ev.status == EvaluationStatus.PASS
    assert rule.provenance.source_id == "SRC-AU-189-MAIN"
    assert rule.provenance.document_version == 1
    assert rule.provenance.chunk_id == 3
    assert rule.provenance.evidence_text == "Under 45"


def test_rule_to_dict_preserves_all_fields():
    rule = compile_requirement_to_rule(
        {"category": "AGE", "field": "applicant.age", "operator": "LT", "value": 45,
         "mandatory": True, "criticality": "CRITICAL", "name": "Age",
         "evidence": [{"chunk_id": 3, "evidence_text": "Under 45"}],
         "_source_id": "SRC", "_document_id": "DOC", "_document_version": 1,
         "_requirement_id": "R1", "_program_id": "SKILLED_189"},
        "RULE-001",
    )
    d = rule.to_dict()
    assert d["rule_id"] == "RULE-001"
    assert d["provenance"]["source_id"] == "SRC"
    assert d["status"] == "DRAFT"
    assert d["version"] == 1
