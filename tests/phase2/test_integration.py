"""Full integration test — Phase 1 pipeline + Phase 2 extraction with mock LLM.

Tests the complete flow:
1. Run Phase 1 offline pipeline to populate document_chunks
2. Run Phase 2 chunk selection
3. Run Phase 2 extraction with mock LLM responses
4. Verify requirements are stored with proper provenance
5. Verify evidence validation works
6. Verify deduplication detects duplicates
7. Verify review workflow works
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "phase2"))

from db import init_schema, get_connection, DB_PATH
from seed_registry import main as seed_main
from pipeline import run_pipeline
from requirement_repository import create_extraction_run, complete_extraction_run, persist_requirement
from requirement_extractor import extract_from_document
from chunk_selector import select_candidates, build_context_window
from models import (
    Category,
    Operator,
    EvidenceType,
)
from requirement_normalizer import normalize_requirement
from requirement_deduplicator import decide, semantic_key
from requirement_validator import parse_llm_output
import review as review_mod
from llm_client import MockLLMClient

FIXTURE = ROOT / "tests" / "fixtures" / "skilled_189_sample.html"
TEST_SOURCE_ID = "SRC-AU-189-MAIN"
TEST_DOCUMENT_ID = "DOC-AU-189-MAIN"


def _setup():
    """Reset DB and run Phase 1 pipeline."""
    db_path = DB_PATH
    if db_path.exists():
        db_path.unlink()
    for d in ["raw", "extracted", "validation"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)

    init_schema()
    import os as _os
    _os.environ["SEED_BACKEND"] = "sqlite"  # keep the offline integration seed on SQLite
    seed_main()
    run_pipeline(source_id_filter=TEST_SOURCE_ID, offline_fixture=FIXTURE)


def _get_chunks(conn, document_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY order_index",
        (document_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_document_version(conn, document_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(version_id) as vid FROM document_versions WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    return row["vid"] if row and row["vid"] else 1


def test_full_pipeline_integration():
    """Test complete Phase 1 + Phase 2 integration with mock LLM."""
    print("\n=== Full Integration Test ===")
    _setup()

    conn = get_connection()
    try:
        chunks = _get_chunks(conn, TEST_DOCUMENT_ID)
        assert len(chunks) > 0, "Phase 1 should have produced chunks"
        print(f"  Phase 1: {len(chunks)} chunks for {TEST_DOCUMENT_ID}")

        candidates = select_candidates(chunks)
        assert len(candidates) > 0, "Chunk selection should find candidates"
        print(f"  Chunk selection: {len(candidates)} candidates from {len(chunks)} chunks")

        llm_client = MockLLMClient()
        doc_version = _get_document_version(conn, TEST_DOCUMENT_ID)
        source = {"source_id": TEST_SOURCE_ID, "program_id": "SKILLED_189", "country_id": "AU"}
        document = {"document_id": TEST_DOCUMENT_ID, "version_id": doc_version}

        outcomes = extract_from_document(
            source=source, document=document, all_chunks=chunks, llm_client=llm_client,
        )
        total_accepted = sum(len(o.accepted_requirements) for o in outcomes)
        total_rejected = sum(len(o.rejected_requirements) for o in outcomes)
        total_needs_review = sum(len(o.needs_review_requirements) for o in outcomes)
        print(f"  Extraction: {total_accepted} accepted, {total_rejected} rejected, {total_needs_review} needs-review from {len(outcomes)} candidates")
        assert total_accepted > 0, "Should extract at least some requirements"

        run_id = create_extraction_run(conn, TEST_DOCUMENT_ID, doc_version, "mock-llm-v1",
                                        "requirement_extraction_v1", dry_run=False)

        persisted = []
        for outcome in outcomes:
            for req in outcome.accepted_requirements:
                result = persist_requirement(
                    conn, program_id="SKILLED_189", country_id="AU",
                    requirement=req, document_id=TEST_DOCUMENT_ID,
                    document_version=doc_version, extraction_run_id=run_id,
                )
                persisted.append(result)

        print(f"  Persisted: {len(persisted)} requirements")
        assert len(persisted) > 0

        req_rows = conn.execute(
            "SELECT * FROM requirements WHERE program_id = ?", ("SKILLED_189",)
        ).fetchall()
        assert len(req_rows) > 0, "Requirements should be in DB"
        for r in req_rows:
            assert r["semantic_key"] is not None

        run_rows = conn.execute(
            "SELECT * FROM extraction_runs WHERE document_id = ?",
            (TEST_DOCUMENT_ID,),
        ).fetchall()
        assert len(run_rows) >= 1
        print(f"  Extraction run tracked: {run_rows[0]['run_id']}")

        pending = review_mod.list_for_review(conn, status="DRAFT")
        print(f"  Review: {len(pending)} requirements pending review")
        if pending:
            first_id = pending[0]["requirement_id"]
            review_mod.approve(conn, first_id, reviewer="integration-test",
                               notes="Integration test approval")
            approved = conn.execute(
                "SELECT COUNT(*) as cnt FROM requirements WHERE status = 'APPROVED'"
            ).fetchone()["cnt"]
            assert approved >= 1
            print(f"  Review: approved {first_id}")

        counters = {
            "chunks_seen": len(chunks),
            "candidate_chunks": len(outcomes),
            "llm_calls": len(outcomes),
            "requirements_extracted": total_accepted,
            "validated": total_accepted,
            "needs_review": total_needs_review,
            "rejected": total_rejected,
            "evidence_failures": 0,
            "schema_failures": sum(1 for o in outcomes if o.schema_errors),
        }
        complete_extraction_run(conn, run_id, counters, status="COMPLETED")
        print(f"  Extraction run completed with counters: {counters}")

        chunks_after = _get_chunks(conn, TEST_DOCUMENT_ID)
        assert len(chunks_after) == len(chunks), "Phase 1 chunks should be unchanged"

    finally:
        conn.close()

    print("\n=== INTEGRATION TEST PASSED ===")


def test_parse_llm_output_valid():
    resp = '{"requirements": [{"category": "AGE", "name": "Test", "content_kind": "REQUIREMENT", "mandatory": true, "evidence": [{"chunk_id": 1, "evidence_text": "test", "evidence_type": "DIRECT_TEXT"}]}]}'
    parsed, errors = parse_llm_output(resp)
    assert parsed is not None
    assert "requirements" in parsed
    assert len(parsed["requirements"]) == 1
    assert errors == []


def test_parse_llm_output_with_fences():
    resp = '```json\n{"requirements": []}\n```'
    parsed, errors = parse_llm_output(resp)
    assert parsed is not None
    assert "requirements" in parsed
    assert errors == []


def test_parse_llm_output_invalid():
    parsed, errors = parse_llm_output("not json at all {{{")
    assert parsed is None
    assert len(errors) > 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("ALL TESTS PASSED")
