from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src" / "phase2"))

from requirement_deduplicator import decide, semantic_key  # noqa: E402


def test_semantic_key_stable_across_calls():
    k1 = semantic_key("SKILLED_189", "AGE", "applicant.age", "Age requirement")
    k2 = semantic_key("SKILLED_189", "AGE", "applicant.age", "Age requirement")
    assert k1 == k2
    print("PASS: semantic_key is deterministic/stable")


def test_semantic_key_differs_for_different_programs():
    k1 = semantic_key("SKILLED_189", "AGE", "applicant.age", "Age requirement")
    k2 = semantic_key("SKILLED_190", "AGE", "applicant.age", "Age requirement")
    assert k1 != k2
    print("PASS: semantic_key differs across programs for the same requirement concept")


def test_new_requirement_no_existing_match():
    d = decide({"operator": "LT", "value": 45, "unit": "years"}, [])
    assert d.relationship == "NEW"
    print("PASS: no existing match -> NEW")


def test_exact_duplicate_detected():
    existing = [{"requirement_id": "REQ-1", "operator": "LT", "value": 45, "unit": "years"}]
    d = decide({"operator": "LT", "value": 45, "unit": "years"}, existing)
    assert d.relationship == "EXACT_DUPLICATE"
    assert d.matched_requirement_id == "REQ-1"
    print("PASS: identical operator/value/unit correctly detected as EXACT_DUPLICATE")


def test_complementary_when_one_side_unknown():
    existing = [{"requirement_id": "REQ-1", "operator": "UNKNOWN", "value": None, "unit": None}]
    d = decide({"operator": "LT", "value": 45, "unit": "years"}, existing)
    assert d.relationship == "COMPLEMENTARY"
    print("PASS: UNKNOWN vs. concrete value treated as COMPLEMENTARY, not a conflict")


def test_conflict_detected_for_incompatible_values():
    existing = [{"requirement_id": "REQ-1", "operator": "LT", "value": 45, "unit": "years"}]
    d = decide({"operator": "LT", "value": 50, "unit": "years"}, existing)
    assert d.relationship == "CONFLICT"
    assert d.matched_requirement_id == "REQ-1"
    print("PASS: two PRIMARY sources with different numeric thresholds correctly flagged CONFLICT")


def test_conflict_detected_for_different_operators():
    existing = [{"requirement_id": "REQ-1", "operator": "LT", "value": 45, "unit": "years"}]
    d = decide({"operator": "LTE", "value": 45, "unit": "years"}, existing)
    assert d.relationship == "CONFLICT"
    print("PASS: different operators on the same field correctly flagged CONFLICT")


def test_never_auto_deletes():
    # decide() only classifies - it never mutates or deletes; this test documents that contract.
    existing = [{"requirement_id": "REQ-1", "operator": "LT", "value": 45, "unit": "years"}]
    d = decide({"operator": "LT", "value": 50, "unit": "years"}, existing)
    assert existing[0]["requirement_id"] == "REQ-1"  # untouched
    print("PASS: decide() never mutates the existing requirement list (no auto-merge/auto-delete)")


def run_all():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== test_deduplication.py: ALL TESTS PASSED ===")


if __name__ == "__main__":
    run_all()
