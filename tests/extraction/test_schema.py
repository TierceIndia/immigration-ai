"""Unit tests for requirement_validator: JSON schema + logic consistency checks.
Run with: python tests/extraction/test_schema.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src" / "extraction"))

from requirement_validator import parse_llm_output, validate_logic, validate_schema  # noqa: E402

VALID_REQUIREMENT = {
    "category": "AGE", "name": "Age requirement", "content_kind": "REQUIREMENT",
    "field": "applicant.age", "operator": "LT", "value": 45, "unit": "years", "mandatory": True,
    "condition": None, "exception": None, "criticality": "CRITICAL",
    "evidence": [{"chunk_id": 6, "evidence_text": "Applicants must be under 45 years of age.",
                   "evidence_type": "DIRECT_TEXT"}],
}


def test_valid_payload_passes_schema():
    payload = {"requirements": [VALID_REQUIREMENT]}
    result = validate_schema(payload)
    assert result.ok, result.errors
    print("PASS: valid payload passes schema validation")


def test_missing_required_field_fails_schema():
    bad = dict(VALID_REQUIREMENT)
    del bad["evidence"]  # evidence is required
    payload = {"requirements": [bad]}
    result = validate_schema(payload)
    assert not result.ok
    assert any("evidence" in e for e in result.errors)
    print("PASS: missing evidence field correctly rejected by schema")


def test_invalid_category_fails_schema():
    bad = dict(VALID_REQUIREMENT)
    bad["category"] = "NOT_A_REAL_CATEGORY"
    payload = {"requirements": [bad]}
    result = validate_schema(payload)
    assert not result.ok
    print("PASS: invalid category correctly rejected by schema")


def test_invalid_operator_fails_schema():
    bad = dict(VALID_REQUIREMENT)
    bad["operator"] = "MAYBE"
    payload = {"requirements": [bad]}
    result = validate_schema(payload)
    assert not result.ok
    print("PASS: invalid operator correctly rejected by schema")


def test_extra_top_level_key_fails_schema():
    payload = {"requirements": [VALID_REQUIREMENT], "extra_hallucinated_field": "oops"}
    result = validate_schema(payload)
    assert not result.ok
    print("PASS: unexpected top-level key rejected (additionalProperties: false)")


def test_logic_numeric_operator_requires_numeric_value():
    bad = dict(VALID_REQUIREMENT)
    bad["operator"] = "LT"
    bad["value"] = "forty-five"
    result = validate_logic(bad)
    assert not result.ok
    assert any("numeric" in e for e in result.errors)
    print("PASS: LT operator with non-numeric value rejected by logic validator")


def test_logic_allows_ordered_english_level():
    item = dict(VALID_REQUIREMENT)
    item.update({"field": "applicant.english_level", "operator": "GTE",
                 "value": "competent", "unit": None})
    result = validate_logic(item)
    assert result.ok, result.errors
    print("PASS: ordered English proficiency comparison accepted")


def test_logic_in_operator_requires_list_value():
    bad = dict(VALID_REQUIREMENT)
    bad["operator"] = "IN"
    bad["value"] = "single string, not a list"
    result = validate_logic(bad)
    assert not result.ok
    print("PASS: IN operator with non-list value rejected by logic validator")


def test_logic_no_evidence_rejected():
    bad = dict(VALID_REQUIREMENT)
    bad["evidence"] = []
    result = validate_logic(bad)
    assert not result.ok
    print("PASS: requirement with zero evidence entries rejected by logic validator")


def test_field_enum_accepts_not_modeled_with_concept():
    req = dict(VALID_REQUIREMENT)
    req["field"] = "NOT_MODELED"
    req["concept"] = "visa_history_bridging_count"
    payload = {"requirements": [req]}
    result = validate_schema(payload)
    assert result.ok, result.errors
    print("PASS: field=NOT_MODELED with a concept passes schema validation")


def test_field_enum_rejects_an_invented_dotted_name():
    req = dict(VALID_REQUIREMENT)
    req["field"] = "applicant.visa_history"  # not in the closed vocabulary - must use NOT_MODELED
    payload = {"requirements": [req]}
    result = validate_schema(payload)
    assert not result.ok
    print("PASS: an invented dotted field name outside the closed vocabulary is rejected by schema")


def test_field_enum_accepts_null():
    req = dict(VALID_REQUIREMENT)
    req["field"] = None
    req["operator"] = "UNKNOWN"
    payload = {"requirements": [req]}
    result = validate_schema(payload)
    assert result.ok, result.errors
    print("PASS: a null field (non-REQUIREMENT content with no applicant fact) passes schema validation")


def test_logic_not_modeled_without_concept_warns():
    bad = dict(VALID_REQUIREMENT)
    bad["field"] = "NOT_MODELED"
    result = validate_logic(bad)
    assert result.ok  # missing concept is a warning, not a hard error - never drop the row
    assert any("concept" in w for w in result.warnings)
    print("PASS: field=NOT_MODELED with no concept warns (routes to review) rather than silently accepted")


def test_logic_not_modeled_with_concept_is_clean():
    ok = dict(VALID_REQUIREMENT)
    ok["field"] = "NOT_MODELED"
    ok["concept"] = "visa_history_bridging_count"
    result = validate_logic(ok)
    assert result.ok
    assert not any("concept" in w for w in result.warnings)
    print("PASS: field=NOT_MODELED with a real concept produces no concept-related warning")


def test_parse_llm_output_handles_markdown_fences():
    raw = '```json\n{"requirements": []}\n```'
    parsed, errors = parse_llm_output(raw)
    assert parsed == {"requirements": []}
    assert errors == []
    print("PASS: markdown-fenced JSON is tolerated and parsed correctly")


def test_parse_llm_output_rejects_invalid_json():
    raw = "This is not JSON at all, sorry!"
    parsed, errors = parse_llm_output(raw)
    assert parsed is None
    assert errors
    print("PASS: invalid JSON correctly reported as a parse error, not silently swallowed")


def run_all():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== test_schema.py: ALL TESTS PASSED ===")


if __name__ == "__main__":
    run_all()
