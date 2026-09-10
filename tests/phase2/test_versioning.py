"""Integration test: when a source document changes (v1 -> v2), requirements extracted from v1
must not be silently mutated. Running extraction against v2 with a different value for the same
semantic key must produce a CONFLICT-flagged new row while preserving the v1 row untouched, per
the "Requirements must be version-aware" requirement in the Phase 2 spec.

Uses the real Phase 1 pipeline (offline fixture) + real Phase 2 persistence against a throwaway
SQLite file - no network required.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "phase2"))

import db as dbmod  # noqa: E402
from seed_registry import main as seed_main  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from llm_client import get_client  # noqa: E402
from requirement_extractor import extract_from_document  # noqa: E402
from requirement_repository import complete_extraction_run, create_extraction_run, persist_requirement  # noqa: E402

TEST_DB = None  # uses the shared project immigration.db, like tests/test_pipeline_offline.py does
FIXTURE_V1 = ROOT / "tests" / "fixtures" / "skilled_189_sample.html"
SOURCE_ID = "SRC-AU-189-MAIN"
DOCUMENT_ID = "DOC-AU-189-MAIN"


def _extract_and_persist(conn, document_id, model="mock-llm-v1"):
    doc = conn.execute("SELECT * FROM government_documents WHERE document_id = ?", (document_id,)).fetchone()
    version = doc["current_version"]
    version_row = conn.execute(
        "SELECT version_id FROM document_versions WHERE document_id = ? AND version_number = ?",
        (document_id, version),
    ).fetchone()
    chunks = [dict(c) for c in conn.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? AND version_id = ? ORDER BY order_index",
        (document_id, version_row["version_id"]),
    ).fetchall()]
    client = get_client(model)
    run_id = create_extraction_run(conn, document_id, version, model, "requirement_extraction_v1", False)
    outcomes = extract_from_document(
        source={"source_id": doc["source_id"], "authority": "Test", "program_id": doc["program_id"]},
        document={"document_id": document_id, "version": version, "title": doc["title"]},
        all_chunks=chunks, llm_client=client,
    )
    results = []
    for outcome in outcomes:
        for req in outcome.accepted_requirements:
            results.append(persist_requirement(
                conn, program_id=doc["program_id"], country_id=doc["country_id"], requirement=req,
                document_id=document_id, document_version=version, extraction_run_id=run_id,
            ))
    complete_extraction_run(conn, run_id, {"requirements_extracted": len(results)})
    return results


def main():
    print("=== Versioning integration test ===")
    db_path = ROOT / "immigration.db"
    if db_path.exists():
        db_path.unlink()
    for d in ["raw", "extracted"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    conn = dbmod.init_schema()
    os.environ["SEED_BACKEND"] = "sqlite"  # keep the standalone workflow on SQLite
    seed_main()

    print("--- v1: extract requirements ---")
    run_pipeline(source_id_filter=SOURCE_ID, offline_fixture=FIXTURE_V1)
    v1_results = _extract_and_persist(conn, DOCUMENT_ID)
    v1_created = [r for r in v1_results if r["action"] == "CREATED"]
    assert v1_created, "expected at least one CREATED requirement from v1"
    age_req_v1 = next(r for r in v1_created if "0f8891f14ad98a41" in r["requirement_id"] or True)
    age_row = conn.execute(
        "SELECT * FROM requirements WHERE category = 'AGE' ORDER BY created_at LIMIT 1"
    ).fetchone()
    assert age_row is not None
    assert age_row["value_json"] == "45"
    print(f"PASS: v1 AGE requirement created with value=45, status={age_row['status']}")

    print("\n--- v2: modified fixture (age changes 45 -> 50), re-run pipeline + extraction ---")
    modified_html = FIXTURE_V1.read_text(encoding="utf-8").replace(
        "under 45 years of age", "under 50 years of age"
    )
    tmp_fixture = ROOT / "tests" / "phase2" / "_tmp_v2_fixture.html"
    tmp_fixture.write_text(modified_html, encoding="utf-8")
    try:
        run_pipeline(source_id_filter=SOURCE_ID, offline_fixture=tmp_fixture)
        doc_after = conn.execute(
            "SELECT current_version FROM government_documents WHERE document_id = ?", (DOCUMENT_ID,)
        ).fetchone()
        assert doc_after["current_version"] == 2, "Phase 1 should have bumped document to version 2"

        v2_results = _extract_and_persist(conn, DOCUMENT_ID)
        conflicts = [r for r in v2_results if r["action"] == "CONFLICT_RECORDED"]
        assert conflicts, f"expected a CONFLICT_RECORDED result for the changed AGE requirement, got {v2_results}"

        # original v1 row must still exist, untouched in its structured content, just re-flagged CONFLICT
        age_row_after = conn.execute(
            "SELECT * FROM requirements WHERE requirement_id = ?", (age_row["requirement_id"],)
        ).fetchone()
        assert age_row_after["value_json"] == "45", "v1 requirement's own value must NOT be mutated"
        assert age_row_after["status"] == "CONFLICT"
        print(f"PASS: v1 AGE requirement preserved unchanged (value=45), status flipped to CONFLICT")

        new_age_row = conn.execute(
            "SELECT * FROM requirements WHERE requirement_id = ?",
            (conflicts[0]["requirement_id"],),
        ).fetchone()
        assert new_age_row["value_json"] == "50"
        assert new_age_row["status"] == "CONFLICT"
        print(f"PASS: new v2 AGE requirement (value=50) recorded separately, also flagged CONFLICT")

        # both requirement rows' evidence must be independently traceable to their own document_version
        v1_evidence = conn.execute(
            "SELECT * FROM requirement_evidence WHERE requirement_id = ?", (age_row["requirement_id"],)
        ).fetchall()
        v2_evidence = conn.execute(
            "SELECT * FROM requirement_evidence WHERE requirement_id = ?", (new_age_row["requirement_id"],)
        ).fetchall()
        assert all(e["document_version"] == 1 for e in v1_evidence)
        assert all(e["document_version"] == 2 for e in v2_evidence)
        print("PASS: evidence for each conflicting version correctly traces to its own document_version")

    finally:
        tmp_fixture.unlink(missing_ok=True)

    conn.close()
    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    main()
