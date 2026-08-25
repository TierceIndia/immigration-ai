"""Offline pipeline test - exercises fetch(fixture)->raw storage->extract->hash->change-detect->persist
without touching the network. Run with: python tests/test_pipeline_offline.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from db import init_schema, get_connection  # noqa: E402
from seed_registry import main as seed_main  # noqa: E402
from pipeline import run_pipeline  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "skilled_189_sample.html"
TEST_SOURCE_ID = "SRC-AU-189-MAIN"


def reset_test_db():
    db_path = ROOT / "immigration.db"
    if db_path.exists():
        db_path.unlink()
    for d in ["raw", "extracted", "validation"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)


def main():
    print("=== Offline pipeline test ===")
    reset_test_db()
    init_schema()
    seed_main()

    print("\n--- Run 1: initial fetch (expect INITIAL) ---")
    report1 = run_pipeline(source_id_filter=TEST_SOURCE_ID, offline_fixture=FIXTURE)
    r1 = report1["results"][0]
    assert r1["status"] == "OK", r1
    assert r1["change_type"] == "INITIAL", r1
    assert r1["chunk_count"] > 0, "expected extracted chunks"
    print(f"PASS: version={r1['version']} chunks={r1['chunk_count']} hash={r1['content_hash'][:12]}...")

    print("\n--- Run 2: same fixture again (expect UNCHANGED) ---")
    report2 = run_pipeline(source_id_filter=TEST_SOURCE_ID, offline_fixture=FIXTURE)
    r2 = report2["results"][0]
    assert r2["change_type"] == "UNCHANGED", r2
    print(f"PASS: change_type={r2['change_type']}")

    print("\n--- Run 3: modified fixture (expect CHANGED) ---")
    modified = FIXTURE.read_text(encoding="utf-8").replace(
        "under 45 years of age", "under 44 years of age"
    )
    tmp_fixture = ROOT / "tests" / "fixtures" / "_tmp_modified.html"
    tmp_fixture.write_text(modified, encoding="utf-8")
    try:
        report3 = run_pipeline(source_id_filter=TEST_SOURCE_ID, offline_fixture=tmp_fixture)
        r3 = report3["results"][0]
        assert r3["change_type"] == "CHANGED", r3
        assert r3["version"] == 2, r3
        print(f"PASS: change_type={r3['change_type']} version={r3['version']}")
    finally:
        tmp_fixture.unlink(missing_ok=True)

    print("\n--- Verifying DB state ---")
    conn = get_connection()
    doc = conn.execute(
        "SELECT * FROM government_documents WHERE document_id = ?", ("DOC-AU-189-MAIN",)
    ).fetchone()
    assert doc is not None
    assert doc["current_version"] == 2
    versions = conn.execute(
        "SELECT * FROM document_versions WHERE document_id = ? ORDER BY version_number",
        ("DOC-AU-189-MAIN",),
    ).fetchall()
    assert len(versions) == 2, f"expected 2 version rows, got {len(versions)}"
    chunks = conn.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? AND version_id = ?",
        ("DOC-AU-189-MAIN", versions[-1]["version_id"]),
    ).fetchall()
    assert len(chunks) > 0
    heading_chunks = [c for c in chunks if c["chunk_type"] == "HEADING"]
    assert any("Eligibility" in c["heading_path"] for c in heading_chunks)
    table_chunks = [c for c in chunks if c["chunk_type"] == "TABLE"]
    assert len(table_chunks) == 1
    list_chunks = [c for c in chunks if c["chunk_type"] == "LIST"]
    assert len(list_chunks) == 1
    fetch_log_rows = conn.execute(
        "SELECT * FROM fetch_log WHERE source_id = ? ORDER BY log_id", (TEST_SOURCE_ID,)
    ).fetchall()
    assert len(fetch_log_rows) == 3, f"expected 3 fetch_log rows, got {len(fetch_log_rows)}"
    conn.close()

    print(f"PASS: document_versions={len(versions)} chunks_in_v2={len(chunks)} "
          f"(headings={len(heading_chunks)}, tables={len(table_chunks)}, lists={len(list_chunks)}) "
          f"fetch_log_rows={len(fetch_log_rows)}")

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    main()
