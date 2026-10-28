"""Item 1 (closed field vocabulary): persist_requirement must round-trip the
new `concept` label for a NOT_MODELED requirement, not drop it.

Uses an isolated, temporary SQLite DB (not the real project-root immigration.db),
mirroring the fixture pattern in test_requirement_id_length.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "extraction"))

from db import init_schema  # noqa: E402
from requirement_repository import create_extraction_run, persist_requirement  # noqa: E402

PROGRAM_ID = "SKILLED_189"

NOT_MODELED_REQUIREMENT = {
    "category": "VISA_HISTORY",
    "field": "NOT_MODELED",
    "concept": "visa_history_bridging_count",
    "name": "Subclass 040 bridging visa grant limit",
    "description": "The applicant has not previously been granted 2 bridging visas of Subclass 040.",
    "operator": "LT",
    "value": 2,
    "unit": None,
    "mandatory": True,
    "criticality": "CRITICAL",
    "evidence": [
        {"chunk_id": 1, "evidence_text": "under 45 years of age", "evidence_type": "DIRECT_TEXT"}
    ],
    "references": [],
}


def _fresh_conn(tmp_path):
    db_path = tmp_path / "test_immigration.db"
    conn = init_schema(db_path=db_path)
    conn.execute("INSERT INTO countries (country_id, name, iso_code) VALUES ('AU', 'Australia', 'AUS')")
    conn.execute(
        "INSERT INTO immigration_programs (program_id, country_id, visa_category, subclass_code, name) "
        "VALUES (?, 'AU', 'SKILLED', NULL, 'Test Program')",
        (PROGRAM_ID,),
    )
    conn.execute(
        """
        INSERT INTO government_sources
            (source_id, country_id, program_id, visa_category, authority, url,
             source_type, priority, monitoring_method, crawl_frequency, status)
        VALUES ('SRC-TEST', 'AU', ?, 'SKILLED', 'Test Authority', 'https://example.gov.au',
                'HTML', 'PRIMARY', 'WEB', 'WEEKLY', 'ACTIVE')
        """,
        (PROGRAM_ID,),
    )
    conn.execute(
        """
        INSERT INTO government_documents
            (document_id, source_id, country_id, program_id, visa_category, document_type,
             url, retrieved_at, content_hash, current_version, raw_path, status)
        VALUES ('DOC-TEST', 'SRC-TEST', 'AU', ?, 'SKILLED', 'LEGISLATION_TEXT',
                'https://example.gov.au', '2026-01-01T00:00:00+00:00', 'hash1', 1,
                'raw:v1', 'CURRENT')
        """,
        (PROGRAM_ID,),
    )
    conn.execute(
        """
        INSERT INTO document_versions
            (document_id, version_number, content_hash, retrieved_at, raw_path, change_type)
        VALUES ('DOC-TEST', 1, 'hash1', '2026-01-01T00:00:00+00:00', 'raw:v1', 'INITIAL')
        """
    )
    version_id = conn.execute(
        "SELECT version_id FROM document_versions WHERE document_id = 'DOC-TEST' AND version_number = 1"
    ).fetchone()["version_id"]
    conn.execute(
        """
        INSERT INTO document_chunks (chunk_id, document_id, version_id, chunk_type, order_index, raw_text)
        VALUES (1, 'DOC-TEST', ?, 'PARAGRAPH', 0, 'under 45 years of age')
        """,
        (version_id,),
    )
    conn.commit()
    run_id = create_extraction_run(conn, "DOC-TEST", 1, "gpt-4o-mini", "requirement_extraction_v3", dry_run=False)
    return conn, run_id


def test_not_modeled_concept_persists_and_round_trips(tmp_path):
    conn, run_id = _fresh_conn(tmp_path)

    result = persist_requirement(
        conn, program_id=PROGRAM_ID, country_id="AU", requirement=NOT_MODELED_REQUIREMENT,
        document_id="DOC-TEST", document_version=1, extraction_run_id=run_id, dry_run=False,
    )
    assert result["action"] == "CREATED"

    row = conn.execute(
        "SELECT field, concept FROM requirements WHERE requirement_id = ?",
        (result["requirement_id"],),
    ).fetchone()
    assert row["field"] == "NOT_MODELED"
    assert row["concept"] == "visa_history_bridging_count"
    print("PASS: a NOT_MODELED requirement's concept label persists and round-trips, never dropped")


def test_evaluable_field_has_null_concept(tmp_path):
    conn, run_id = _fresh_conn(tmp_path)
    req = dict(NOT_MODELED_REQUIREMENT)
    req["field"] = "applicant.age"
    req["concept"] = None
    req["category"] = "AGE"
    req["name"] = "Age requirement"

    result = persist_requirement(
        conn, program_id=PROGRAM_ID, country_id="AU", requirement=req,
        document_id="DOC-TEST", document_version=1, extraction_run_id=run_id, dry_run=False,
    )
    row = conn.execute(
        "SELECT field, concept FROM requirements WHERE requirement_id = ?",
        (result["requirement_id"],),
    ).fetchone()
    assert row["field"] == "applicant.age"
    assert row["concept"] is None
    print("PASS: an evaluable field has no concept clutter")
