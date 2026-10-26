"""Evaluate extraction against REAL gold dataset using frozen real chunks.

Usage:
    python tests/extraction/test_real_gold_evaluation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "extraction"))

from llm_client import MockLLMClient, OpenAILLMClient, get_client  # noqa: E402
from requirement_extractor import extract_from_document  # noqa: E402

REAL_CHUNKS_PATH = ROOT / "data" / "real_validation" / "skilled_189_real_chunks.json"
REAL_GOLD_PATH = ROOT / "data" / "gold" / "skilled" / "skilled_189_real.json"
SOURCE_ID = "SRC-AU-189-MAIN"
DOCUMENT_ID = "DOC-AU-189-MAIN"


def _build_client(model: str):
    """Route a model name to the right LLM client.

    OpenAI model names resolve through the production factory; anything else
    (including the test-only mock-llm-v1) resolves to the deterministic
    MockLLMClient so the default offline evaluation never needs an API key.
    """
    if OpenAILLMClient._is_openai_model(model):
        return get_client(model)
    return MockLLMClient()


def _run_extraction_with_real_chunks(model: str = "mock-llm-v1", context_window: int = 0):
    """Load frozen real chunks, persist to DB, run LLM extraction."""
    real_data = json.loads(REAL_CHUNKS_PATH.read_text(encoding="utf-8"))
    chunks = real_data["chunks"]

    client = _build_client(model)
    print(f"LLM model: {client.model_name}")
    outcomes = extract_from_document(
        source={"source_id": SOURCE_ID, "authority": "Department of Home Affairs", "program_id": "SKILLED_189"},
        document={"document_id": DOCUMENT_ID, "version": 1, "title": real_data["title"]},
        all_chunks=chunks, llm_client=client, context_window=context_window,
    )
    return outcomes, chunks


def _all_accepted(outcomes):
    accepted = []
    for o in outcomes:
        accepted.extend(o.accepted_requirements)
        for item in o.needs_review_requirements:
            accepted.append(item["requirement"])
    return accepted


def evaluate(model: str = "mock-llm-v1", context_window: int = 0):
    gold = json.loads(REAL_GOLD_PATH.read_text(encoding="utf-8"))
    gold_items = gold["items"]
    gold_positive = [g for g in gold_items if g["expected"] is not None]
    gold_negative = [g for g in gold_items if g["expected"] is None]

    outcomes, real_chunks = _run_extraction_with_real_chunks(model=model, context_window=context_window)
    extracted = _all_accepted(outcomes)

    chunk_text_by_id = {c["chunk_id"]: c["raw_text"] for c in real_chunks}

    def semantic_match(gold_item, requirement):
        """Require provenance and the executable semantics, not merely category proximity."""
        expected = gold_item["expected"]
        if requirement.get("category") != gold_item["category"]:
            return False
        evidence_chunk_ids = {e["chunk_id"] for e in requirement.get("evidence", [])}
        if gold_item["chunk_id"] not in evidence_chunk_ids:
            return False
        return all(requirement.get(key) == expected.get(key) for key in
                   ("field", "operator", "value", "unit", "mandatory"))

    matched_gold_ids = set()
    matched_extracted_idx = set()
    for g in gold_positive:
        for i, ext in enumerate(extracted):
            if i in matched_extracted_idx:
                continue
            if semantic_match(g, ext):
                matched_gold_ids.add(g["gold_id"])
                matched_extracted_idx.add(i)
                break

    true_positives = len(matched_gold_ids)
    false_negatives = len(gold_positive) - true_positives

    # Every unmatched extraction is a false positive. Restricting this to annotated-negative
    # chunks hides duplicate or semantically wrong rows emitted from positive chunks.
    false_positives = len(extracted) - len(matched_extracted_idx)

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) else 1.0
    recall = true_positives / len(gold_positive) if gold_positive else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    critical_gold = [g for g in gold_positive if g["criticality"] == "CRITICAL"]
    critical_matched = [g for g in critical_gold if g["gold_id"] in matched_gold_ids]
    critical_recall = len(critical_matched) / len(critical_gold) if critical_gold else 1.0

    evidence_correct = 0
    evidence_total = 0
    for ext in extracted:
        for ev in ext.get("evidence", []):
            evidence_total += 1
            chunk_text = chunk_text_by_id.get(ev["chunk_id"], "")
            if ev["evidence_text"].strip() in chunk_text:
                evidence_correct += 1
    evidence_accuracy = evidence_correct / evidence_total if evidence_total else 1.0

    missed = [g for g in gold_positive if g["gold_id"] not in matched_gold_ids]
    missed_critical = [g for g in missed if g["criticality"] == "CRITICAL"]

    report = {
        "gold_positive_items": len(gold_positive),
        "gold_negative_items": len(gold_negative),
        "extracted_items": len(extracted),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "evidence_accuracy": round(evidence_accuracy, 3),
        "critical_items_total": len(critical_gold),
        "critical_items_recalled": len(critical_matched),
        "critical_requirement_recall": round(critical_recall, 3),
        "missed_gold_ids": [g["gold_id"] for g in missed],
        "missed_critical_gold_ids": [g["gold_id"] for g in missed_critical],
        "extracted_categories": [ext["category"] for ext in extracted],
        "extracted_semantics": [
            {
                "category": ext.get("category"), "field": ext.get("field"),
                "operator": ext.get("operator"), "value": ext.get("value"),
                "unit": ext.get("unit"), "mandatory": ext.get("mandatory"),
                "evidence_chunk_ids": sorted({e["chunk_id"] for e in ext.get("evidence", [])}),
            }
            for ext in extracted
        ],
    }
    return report


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "mock-llm-v1"
    window = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    print(f"=== REAL Gold-standard evaluation (model={model}, context_window={window}) ===\n")

    report = evaluate(model=model, context_window=window)
    report["llm_model"] = model
    print(json.dumps(report, indent=2, ensure_ascii=False))

    out_path = ROOT / "validation" / "real_gold_evaluation_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten to {out_path}")

    print(f"\n--- Summary ---")
    print(f"  Gold positive items:     {report['gold_positive_items']}")
    print(f"  Gold negative items:     {report['gold_negative_items']}")
    print(f"  Extracted items:         {report['extracted_items']}")
    print(f"  True positives:          {report['true_positives']}")
    print(f"  False positives:         {report['false_positives']}")
    print(f"  False negatives:         {report['false_negatives']}")
    print(f"  Precision:               {report['precision']}")
    print(f"  Recall:                  {report['recall']}")
    print(f"  F1:                      {report['f1']}")
    print(f"  Evidence accuracy:       {report['evidence_accuracy']}")
    print(f"  Critical recall:         {report['critical_requirement_recall']}")
    print(f"  Missed gold IDs:         {report['missed_gold_ids']}")
    print(f"  Missed critical IDs:     {report['missed_critical_gold_ids']}")
    print(f"  Extracted categories:    {report['extracted_categories']}")

    print("\n=== REAL GOLD EVALUATION COMPLETE ===")

    if model == "mock-llm-v1":
        assert report["evidence_accuracy"] == 1.0, "Evidence accuracy gate failed"
        assert report["critical_requirement_recall"] >= 0.95, "Critical recall gate failed"
        assert report["f1"] >= 0.85, "F1 quality gate failed"


if __name__ == "__main__":
    main()
