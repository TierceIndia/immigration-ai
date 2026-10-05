"""Gold-standard evaluation harness.

Runs the real extraction pipeline (chunk selection -> mock LLM -> schema validation -> evidence
validation -> normalization) against the manually-curated gold dataset
(data/gold/requirements_gold.json) and computes precision, recall, F1, evidence accuracy, and
critical-requirement recall - the metrics the Phase 2 spec requires before trusting extraction
quality. This is NOT a pass/fail unit test in the strict sense (see the honesty note below); it
prints a scorecard and asserts only on the specific properties that must always hold (no
fabricated evidence ever accepted; all CRITICAL gold items must be found).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "extraction"))

import db as dbmod  # noqa: E402
from seed_registry import main as seed_main  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from llm_client import MockLLMClient  # noqa: E402
from requirement_extractor import extract_from_document  # noqa: E402

GOLD_PATH = ROOT / "data" / "gold" / "requirements_gold.json"
FIXTURE = ROOT / "tests" / "fixtures" / "skilled_189_gold_source.html"
SOURCE_ID = "SRC-AU-189-MAIN"
DOCUMENT_ID = "DOC-AU-189-MAIN"


def _run_extraction():
    db_path = ROOT / "immigration.db"
    if db_path.exists():
        db_path.unlink()
    for d in ["raw", "extracted"]:
        p = ROOT / d
        if p.exists():
            import shutil
            shutil.rmtree(p)
    conn = dbmod.init_schema()
    os.environ["SEED_BACKEND"] = "sqlite"  # keep the standalone workflow on SQLite
    seed_main()
    run_pipeline(source_id_filter=SOURCE_ID, offline_fixture=FIXTURE)

    doc = conn.execute("SELECT * FROM government_documents WHERE document_id = ?", (DOCUMENT_ID,)).fetchone()
    version_row = conn.execute(
        "SELECT version_id FROM document_versions WHERE document_id = ? AND version_number = ?",
        (DOCUMENT_ID, doc["current_version"]),
    ).fetchone()
    chunks = [dict(c) for c in conn.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? AND version_id = ? ORDER BY order_index",
        (DOCUMENT_ID, version_row["version_id"]),
    ).fetchall()]

    client = MockLLMClient()
    outcomes = extract_from_document(
        source={"source_id": SOURCE_ID, "authority": "Department of Home Affairs", "program_id": "SKILLED_189"},
        document={"document_id": DOCUMENT_ID, "version": doc["current_version"], "title": doc["title"]},
        all_chunks=chunks, llm_client=client,
    )
    conn.close()
    return outcomes


def _all_accepted(outcomes):
    accepted = []
    for o in outcomes:
        accepted.extend(o.accepted_requirements)
        for item in o.needs_review_requirements:
            accepted.append(item["requirement"])
    return accepted


def evaluate():
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold_items = gold["items"]
    gold_positive = [g for g in gold_items if g["expected"] is not None]
    gold_negative = [g for g in gold_items if g["expected"] is None]

    outcomes = _run_extraction()
    extracted = _all_accepted(outcomes)

    # Match extracted requirements to gold items by (chunk_id present in evidence + category)
    matched_gold_ids = set()
    matched_extracted_idx = set()
    for g in gold_positive:
        for i, ext in enumerate(extracted):
            if i in matched_extracted_idx:
                continue
            evidence_chunk_ids = {e["chunk_id"] for e in ext.get("evidence", [])}
            if g["chunk_id"] in evidence_chunk_ids and ext["category"] == g["category"]:
                matched_gold_ids.add(g["gold_id"])
                matched_extracted_idx.add(i)
                break

    true_positives = len(matched_gold_ids)
    false_negatives = len(gold_positive) - true_positives

    # False positives: extracted items whose evidence chunk_id corresponds to a NEGATIVE gold
    # item (a chunk that should never yield a requirement), plus any unmatched extracted item
    # that isn't accounted for by a positive gold item at all.
    negative_chunk_ids = {g["chunk_id"] for g in gold_negative}
    false_positives = 0
    for i, ext in enumerate(extracted):
        if i in matched_extracted_idx:
            continue
        evidence_chunk_ids = {e["chunk_id"] for e in ext.get("evidence", [])}
        if evidence_chunk_ids & negative_chunk_ids:
            false_positives += 1
        # else: extra items not covered by the gold set at all (e.g. points-table sub-rows) are
        # not penalized as false positives here - the gold set doesn't claim to be exhaustive
        # over every table row, only over the curated items above.

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) else 1.0
    recall = true_positives / len(gold_positive) if gold_positive else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    critical_gold = [g for g in gold_positive if g["criticality"] == "CRITICAL"]
    critical_matched = [g for g in critical_gold if g["gold_id"] in matched_gold_ids]
    critical_recall = len(critical_matched) / len(critical_gold) if critical_gold else 1.0

    # Evidence accuracy: of everything actually extracted (accepted), what fraction has evidence
    # that is a real substring of its cited chunk. Since evidence_validator already enforces this
    # before anything reaches `accepted_requirements`, this should always be 1.0 - this check
    # exists to catch a regression if that guarantee is ever broken.
    all_chunks_by_id = {}
    for o in outcomes:
        pass  # chunk text isn't directly available here; re-derive from DB for the check below
    conn = dbmod.get_connection()
    chunk_rows = conn.execute("SELECT chunk_id, raw_text FROM document_chunks").fetchall()
    conn.close()
    chunk_text_by_id = {r["chunk_id"]: r["raw_text"] for r in chunk_rows}
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
    }
    return report


def main():
    print("=== Gold-standard evaluation ===")
    print(f"NOTE: {json.loads(GOLD_PATH.read_text())['_meta']['important_caveat'][:150]}...\n")

    report = evaluate()
    print(json.dumps(report, indent=2))

    out_path = ROOT / "validation" / "gold_evaluation_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWritten to {out_path}")

    # Hard invariants that must always hold, regardless of the mock extractor's coverage:
    assert report["evidence_accuracy"] == 1.0, "evidence_validator should guarantee 100% evidence accuracy for anything accepted"
    assert report["false_positives"] == 0, f"extractor produced requirements from negative/informational chunks: check evidence"
    assert report["missed_critical_gold_ids"] == [], (
        f"CRITICAL gold items were missed - unacceptable per spec "
        f"('a system that gets 95% overall recall but misses a critical eligibility condition "
        f"is not acceptable'): {report['missed_critical_gold_ids']}"
    )
    print("\nPASS: evidence_accuracy=1.0, zero false positives, zero missed CRITICAL items")
    print("=== GOLD EVALUATION COMPLETE ===")


if __name__ == "__main__":
    main()
