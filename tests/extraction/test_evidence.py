"""Unit tests for evidence_validator: the guardrail against fabricated/hallucinated evidence."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src" / "extraction"))

from evidence_validator import validate_evidence  # noqa: E402

SUPPLIED = {
    15: "Applicants must be under 45 years of age at the time of invitation.",
    16: "Age requirement",
}


def test_exact_substring_passes():
    ev = [{"chunk_id": 15, "evidence_text": "Applicants must be under 45 years of age at the time of invitation.",
           "evidence_type": "DIRECT_TEXT"}]
    result = validate_evidence(ev, SUPPLIED)
    assert result.ok, result.errors
    print("PASS: exact verbatim evidence accepted")


def test_partial_substring_passes():
    ev = [{"chunk_id": 15, "evidence_text": "under 45 years of age", "evidence_type": "DIRECT_TEXT"}]
    result = validate_evidence(ev, SUPPLIED)
    assert result.ok, result.errors
    print("PASS: verbatim partial-sentence quote accepted")


def test_whitespace_normalized():
    ev = [{"chunk_id": 15, "evidence_text": "under  45   years\nof age", "evidence_type": "DIRECT_TEXT"}]
    result = validate_evidence(ev, SUPPLIED)
    assert result.ok, result.errors
    print("PASS: whitespace differences tolerated without accepting wording changes")


def test_paraphrased_evidence_rejected():
    ev = [{"chunk_id": 15, "evidence_text": "Applicants need to be younger than 45.",
           "evidence_type": "DIRECT_TEXT"}]
    result = validate_evidence(ev, SUPPLIED)
    assert not result.ok
    assert any("verbatim" in e or "fabricat" in e for e in result.errors)
    print("PASS: paraphrased (non-verbatim) evidence correctly rejected")


def test_fabricated_number_rejected():
    ev = [{"chunk_id": 15, "evidence_text": "Applicants must be under 40 years of age.",
           "evidence_type": "DIRECT_TEXT"}]
    result = validate_evidence(ev, SUPPLIED)
    assert not result.ok
    print("PASS: subtly altered evidence (wrong number) correctly rejected")


def test_hallucinated_chunk_id_rejected():
    ev = [{"chunk_id": 999, "evidence_text": "Applicants must be under 45 years of age.",
           "evidence_type": "DIRECT_TEXT"}]
    result = validate_evidence(ev, SUPPLIED)
    assert not result.ok
    assert any("999" in e for e in result.errors)
    print("PASS: evidence citing a chunk_id never supplied to the model is rejected")


def test_empty_evidence_list_rejected():
    result = validate_evidence([], SUPPLIED)
    assert not result.ok
    print("PASS: empty evidence list rejected")


def test_multiple_evidence_entries_all_must_pass():
    ev = [
        {"chunk_id": 16, "evidence_text": "Age requirement", "evidence_type": "DIRECT_TEXT"},
        {"chunk_id": 15, "evidence_text": "this text does not appear anywhere", "evidence_type": "DIRECT_TEXT"},
    ]
    result = validate_evidence(ev, SUPPLIED)
    assert not result.ok  # one bad entry fails the whole set
    print("PASS: one fabricated entry among several fails validation for the whole requirement")


def run_all():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== test_evidence.py: ALL TESTS PASSED ===")


if __name__ == "__main__":
    run_all()
