"""LLM contract tests. Each test injects a canned (mocked) LLM response - valid, invalid JSON,
hallucinated evidence, multiple requirements, conditional, cross-reference, multi-chunk - and
verifies requirement_extractor.extract_from_document handles it correctly end to end (no
database, no network). This is the test class explicitly requested as "LLM contract tests" in
the Phase 2 spec.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src" / "phase2"))

from llm_client import LLMClient  # noqa: E402
from requirement_extractor import extract_from_document  # noqa: E402

CHUNKS = [
    {"chunk_id": 1, "chunk_type": "HEADING", "heading_path": "Eligibility", "order_index": 1,
     "raw_text": "Eligibility", "page_number": None},
    {"chunk_id": 2, "chunk_type": "PARAGRAPH", "heading_path": "Eligibility > Age",
     "order_index": 2, "raw_text": "Applicants must be under 45 years of age.", "page_number": None},
    {"chunk_id": 3, "chunk_type": "PARAGRAPH", "heading_path": "Eligibility > Nomination",
     "order_index": 3,
     "raw_text": "If the applicant applies under the regional stream, state nomination is required unless an exemption applies.",
     "page_number": None},
    {"chunk_id": 4, "chunk_type": "PARAGRAPH", "heading_path": "Eligibility > Legal basis",
     "order_index": 4,
     "raw_text": "This is subject to Schedule 2 of the Migration Regulations 1994.",
     "page_number": None},
]

SOURCE = {"source_id": "SRC-TEST", "authority": "Test Authority", "program_id": "SKILLED_189"}
DOCUMENT = {"document_id": "DOC-TEST", "version": 1, "title": "Test Document"}


class CannedLLMClient(LLMClient):
    model_name = "canned-test-client"

    def __init__(self, response_by_chunk: dict[int, str]):
        self.response_by_chunk = response_by_chunk

    def extract(self, input_context: dict) -> str:
        chunk_id = input_context["primary_chunk"]["chunk_id"]
        return self.response_by_chunk.get(chunk_id, json.dumps({"requirements": []}))


def _valid_age_response():
    return json.dumps({
        "requirements": [{
            "category": "AGE", "name": "Age requirement", "content_kind": "REQUIREMENT",
            "field": "applicant.age", "operator": "LT", "value": 45, "unit": "years",
            "mandatory": True, "condition": None, "exception": None, "criticality": "CRITICAL",
            "confidence": 0.95,
            "evidence": [{"chunk_id": 2, "evidence_text": "Applicants must be under 45 years of age.",
                           "evidence_type": "DIRECT_TEXT"}],
            "references": [],
        }],
    })


def test_valid_response_produces_accepted_requirement():
    client = CannedLLMClient({2: _valid_age_response()})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert len(outcome.accepted_requirements) == 1
    assert outcome.accepted_requirements[0]["category"] == "AGE"
    print("PASS: valid, evidence-backed LLM response is accepted")


def test_invalid_json_response_produces_schema_error_not_a_crash():
    client = CannedLLMClient({2: "this is not valid json {{{"})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert outcome.schema_errors
    assert not outcome.accepted_requirements
    print("PASS: invalid JSON response is caught as a schema_error, does not crash the run")


def test_hallucinated_evidence_rejected():
    bad = json.dumps({
        "requirements": [{
            "category": "AGE", "name": "Age requirement", "content_kind": "REQUIREMENT",
            "field": "applicant.age", "operator": "LT", "value": 40, "unit": "years",
            "mandatory": True, "condition": None, "exception": None, "criticality": "CRITICAL",
            "evidence": [{"chunk_id": 2, "evidence_text": "Applicants must be under 40 years of age.",
                           "evidence_type": "DIRECT_TEXT"}],
            "references": [],
        }],
    })
    client = CannedLLMClient({2: bad})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert not outcome.accepted_requirements
    assert len(outcome.rejected_requirements) == 1
    assert any("verbatim" in r or "fabricat" in r for r in outcome.rejected_requirements[0]["reasons"])
    print("PASS: hallucinated evidence (altered number) is rejected, never silently accepted")


def test_hallucinated_chunk_id_rejected():
    bad = json.dumps({
        "requirements": [{
            "category": "AGE", "name": "Age requirement", "content_kind": "REQUIREMENT",
            "field": "applicant.age", "operator": "LT", "value": 45, "unit": "years",
            "mandatory": True, "condition": None, "exception": None, "criticality": "CRITICAL",
            "evidence": [{"chunk_id": 9999, "evidence_text": "Applicants must be under 45 years of age.",
                           "evidence_type": "DIRECT_TEXT"}],
            "references": [],
        }],
    })
    client = CannedLLMClient({2: bad})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert not outcome.accepted_requirements
    print("PASS: evidence citing a chunk_id never supplied to the model is rejected")


def test_unknown_value_preserved_not_guessed():
    resp = json.dumps({
        "requirements": [{
            "category": "NOMINATION", "name": "Nomination requirement", "content_kind": "REQUIREMENT",
            "field": "nomination.required", "operator": "UNKNOWN", "value": None, "unit": None,
            "mandatory": None, "condition": {"field": "applicant.stream", "operator": "EQ",
                                              "value": "regional", "description": "regional stream"},
            "exception": {"description": "unless an exemption applies", "condition": None},
            "criticality": "IMPORTANT",
            "evidence": [{"chunk_id": 3,
                           "evidence_text": "If the applicant applies under the regional stream, state nomination is required unless an exemption applies.",
                           "evidence_type": "DIRECT_TEXT"}],
            "references": [],
        }],
    })
    client = CannedLLMClient({3: resp})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 3)
    assert len(outcome.accepted_requirements) == 1
    req = outcome.accepted_requirements[0]
    assert req["operator"] == "UNKNOWN"
    assert req["condition"]["value"] == "regional"
    assert req["exception"]["description"] == "unless an exemption applies"
    print("PASS: conditional requirement with UNKNOWN operator is preserved structurally, not guessed")


def test_cross_reference_represented_and_unresolved():
    resp = json.dumps({
        "requirements": [{
            "category": "OTHER", "name": "Legal basis", "content_kind": "CROSS_REFERENCE",
            "field": None, "operator": "UNKNOWN", "value": None, "unit": None, "mandatory": None,
            "condition": None, "exception": None, "criticality": "IMPORTANT",
            "evidence": [{"chunk_id": 4, "evidence_text": "This is subject to Schedule 2 of the Migration Regulations 1994.",
                           "evidence_type": "CROSS_REFERENCE"}],
            "references": [{"reference_type": "LEGAL_CROSS_REFERENCE",
                              "target_text": "Schedule 2 of the Migration Regulations 1994",
                              "status": "UNRESOLVED"}],
        }],
    })
    client = CannedLLMClient({4: resp})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 4)
    assert len(outcome.accepted_requirements) == 1
    req = outcome.accepted_requirements[0]
    assert req["content_kind"] == "CROSS_REFERENCE"
    assert req["references"][0]["status"] == "UNRESOLVED"
    print("PASS: cross-reference is represented explicitly and marked UNRESOLVED, not invented")


def test_multiple_requirements_from_one_chunk():
    resp = json.dumps({
        "requirements": [
            {
                "category": "AGE", "name": "Age requirement", "content_kind": "REQUIREMENT",
                "field": "applicant.age", "operator": "LT", "value": 45, "unit": "years",
                "mandatory": True, "condition": None, "exception": None, "criticality": "CRITICAL",
                "evidence": [{"chunk_id": 2, "evidence_text": "Applicants must be under 45 years of age.",
                               "evidence_type": "DIRECT_TEXT"}],
                "references": [],
            },
            {
                "category": "OTHER", "name": "Second finding", "content_kind": "INFORMATIONAL",
                "field": None, "operator": "UNKNOWN", "value": None, "unit": None, "mandatory": None,
                "condition": None, "exception": None, "criticality": "INFORMATIONAL",
                "evidence": [{"chunk_id": 2, "evidence_text": "under 45 years", "evidence_type": "DIRECT_TEXT"}],
                "references": [],
            },
        ],
    })
    client = CannedLLMClient({2: resp})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert len(outcome.accepted_requirements) == 2
    print("PASS: multiple requirements returned from a single chunk are all processed")


def test_empty_requirements_array_is_valid_not_an_error():
    client = CannedLLMClient({2: json.dumps({"requirements": []})})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS, llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert not outcome.schema_errors
    assert not outcome.accepted_requirements
    print("PASS: empty requirements array (nothing extractable) is valid, not treated as failure")


def test_duplicate_semantics_from_one_response_are_merged():
    base = {
        "category": "AGE", "name": "Age requirement", "content_kind": "REQUIREMENT",
        "field": "applicant.age", "operator": "LT", "value": 45, "unit": "years",
        "mandatory": True, "condition": None, "exception": None, "criticality": "CRITICAL",
        "references": [],
    }
    first = dict(base, evidence=[{
        "chunk_id": 2, "evidence_text": "Applicants must be under 45 years of age.",
        "evidence_type": "DIRECT_TEXT",
    }])
    second = dict(base, name="Duplicate wording", evidence=[{
        "chunk_id": 2, "evidence_text": "under 45 years", "evidence_type": "DIRECT_TEXT",
    }])
    client = CannedLLMClient({2: json.dumps({"requirements": [first, second]})})
    outcomes = extract_from_document(source=SOURCE, document=DOCUMENT, all_chunks=CHUNKS,
                                     llm_client=client)
    outcome = next(o for o in outcomes if o.candidate_chunk_id == 2)
    assert len(outcome.accepted_requirements) == 1
    assert len(outcome.accepted_requirements[0]["evidence"]) == 2
    print("PASS: duplicate semantics merged while preserving distinct evidence")


def run_all():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n=== test_extraction.py: ALL TESTS PASSED ===")


if __name__ == "__main__":
    run_all()
