"""Regression test for requirement_id truncation on PostgreSQL.

Real production failure: for a long program_id like "LEGISLATION_MIGRATION_REGS"
(27 chars), the CONFLICT-path id format "REQ-{program_id}-{sem_key}-{uuid6}" can
exceed the old 48-char column width. PostgreSQL enforces VARCHAR length and
raised StringDataRightTruncation, silently dropping a correctly-extracted
requirement (confirmed in production:
REQ-LEGISLATION_MIGRATION_REGS-35da36400664d712-150ad5 failed to insert).
SQLite does not enforce VARCHAR length at all, so this defect was invisible to
every existing hermetic test.

Fix: db.models widened requirement_id (and everything that stores/FK-references
it) to 96 chars via migration a3c5e7f9b1d3, and
requirement_repository._checked_requirement_id() now raises a clear ValueError
immediately if a generated id would still exceed that width, rather than
relying on the database to catch it (or, on SQLite, not catching it at all).

Uses an isolated, temporary SQLite DB (not the real project-root immigration.db).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "extraction"))

import pytest

from db import init_schema  # noqa: E402
import requirement_repository as reqrepo  # noqa: E402
from requirement_repository import (  # noqa: E402
    MAX_REQUIREMENT_ID_LENGTH,
    _checked_requirement_id,
    create_extraction_run,
    persist_requirement,
)

# The real production program_id that triggered this bug.
LONG_PROGRAM_ID = "LEGISLATION_MIGRATION_REGS"

REQUIREMENT = {
    "category": "ELIGIBILITY",
    "field": "applicant.age",
    "name": "Age requirement",
    "description": "Applicants must be under 45 years of age.",
    "operator": "LT",
    "value": 45,
    "unit": "years",
    "mandatory": True,
    "criticality": "CRITICAL",
    "evidence": [
        {"chunk_id": 1, "evidence_text": "under 45 years of age", "evidence_type": "DIRECT_TEXT"}
    ],
    "references": [],
}


def _fresh_conn(tmp_path, program_id: str):
    db_path = tmp_path / "test_immigration.db"
    conn = init_schema(db_path=db_path)
    conn.execute("INSERT INTO countries (country_id, name, iso_code) VALUES ('AU', 'Australia', 'AUS')")
    conn.execute(
        "INSERT INTO immigration_programs (program_id, country_id, visa_category, subclass_code, name) "
        "VALUES (?, 'AU', 'SKILLED', NULL, 'Test Program')",
        (program_id,),
    )
    conn.execute(
        """
        INSERT INTO government_sources
            (source_id, country_id, program_id, visa_category, authority, url,
             source_type, priority, monitoring_method, crawl_frequency, status)
        VALUES ('SRC-TEST', 'AU', ?, 'SKILLED', 'Test Authority', 'https://example.gov.au',
                'HTML', 'PRIMARY', 'WEB', 'WEEKLY', 'ACTIVE')
        """,
        (program_id,),
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
        (program_id,),
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
        VALUES (1, 'DOC-TEST', ?, 'PARAGRAPH', 0, 'Applicants must be under 45 years of age.')
        """,
        (version_id,),
    )
    conn.commit()
    run_id = create_extraction_run(conn, "DOC-TEST", 1, "gpt-4o-mini", "v2", dry_run=False)
    return conn, run_id


class TestCheckedRequirementIdGuard:
    """Unit tests for the guard function in isolation — no DB needed."""

    def test_accepts_id_within_limit(self):
        ok_id = "REQ-SKILLED_189-abcdef0123456789"
        assert _checked_requirement_id(ok_id) == ok_id

    def test_rejects_id_over_limit(self):
        too_long = "REQ-" + ("X" * (MAX_REQUIREMENT_ID_LENGTH))  # definitely over 96
        with pytest.raises(ValueError, match="exceeding MAX_REQUIREMENT_ID_LENGTH"):
            _checked_requirement_id(too_long)

    def test_rejects_id_exactly_one_over_limit(self):
        exactly_over = "X" * (MAX_REQUIREMENT_ID_LENGTH + 1)
        with pytest.raises(ValueError):
            _checked_requirement_id(exactly_over)

    def test_accepts_id_exactly_at_limit(self):
        exactly_at = "X" * MAX_REQUIREMENT_ID_LENGTH
        assert _checked_requirement_id(exactly_at) == exactly_at


class TestPersistRequirementWithLongProgramId:
    def test_real_world_long_program_id_persists_successfully(self, tmp_path):
        """The actual production scenario: LEGISLATION_MIGRATION_REGS is long,
        but 96 chars gives enough headroom that this must succeed end-to-end,
        proving the widened columns/model actually work together, not just in
        isolation."""
        conn, run_id = _fresh_conn(tmp_path, LONG_PROGRAM_ID)

        # First call: NEW path, no uuid suffix -> shortest form.
        result = persist_requirement(
            conn, program_id=LONG_PROGRAM_ID, country_id="AU", requirement=REQUIREMENT,
            document_id="DOC-TEST", document_version=1, extraction_run_id=run_id,
        )
        assert result["action"] == "CREATED"
        req_id = result["requirement_id"]
        assert req_id.startswith(f"REQ-{LONG_PROGRAM_ID}-")
        assert len(req_id) <= MAX_REQUIREMENT_ID_LENGTH

        row = conn.execute(
            "SELECT requirement_id FROM requirements WHERE requirement_id = ?", (req_id,)
        ).fetchone()
        assert row is not None, "requirement row must actually exist — not silently dropped"
        assert row["requirement_id"] == req_id, "id must be stored intact, not truncated"
        conn.close()

    def test_conflict_path_with_long_program_id_persists_successfully(self, tmp_path, monkeypatch):
        """Forces the CONFLICT path (the exact format from the production log:
        REQ-{program_id}-{sem_key}-{uuid6}) with the real long program_id."""
        conn, run_id = _fresh_conn(tmp_path, LONG_PROGRAM_ID)

        # Seed a conflicting existing requirement with the same semantic key but
        # a different operator/value, so decide() returns CONFLICT.
        from requirement_deduplicator import semantic_key
        sem_key = semantic_key(LONG_PROGRAM_ID, REQUIREMENT["category"],
                                REQUIREMENT["field"], REQUIREMENT["name"])
        existing_id = f"REQ-{LONG_PROGRAM_ID}-{sem_key}"
        conn.execute(
            """
            INSERT INTO requirements
                (requirement_id, semantic_key, program_id, country_id, category, name,
                 operator, value_json, unit, status, current_version, extraction_run_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, 'AU', ?, ?, 'GTE', '99', 'years', 'APPROVED', 1, ?, '2026-01-01', '2026-01-01')
            """,
            (existing_id, sem_key, LONG_PROGRAM_ID, REQUIREMENT["category"],
             REQUIREMENT["name"], run_id),
        )
        conn.commit()

        result = persist_requirement(
            conn, program_id=LONG_PROGRAM_ID, country_id="AU", requirement=REQUIREMENT,
            document_id="DOC-TEST", document_version=1, extraction_run_id=run_id,
        )
        assert result["action"] == "CONFLICT_RECORDED"
        req_id = result["requirement_id"]
        # This is exactly the real production id shape that used to overflow 48 chars.
        assert req_id.startswith(f"REQ-{LONG_PROGRAM_ID}-{sem_key}-")
        assert len(req_id) <= MAX_REQUIREMENT_ID_LENGTH

        row = conn.execute(
            "SELECT requirement_id FROM requirements WHERE requirement_id = ?", (req_id,)
        ).fetchone()
        assert row is not None
        assert row["requirement_id"] == req_id
        conn.close()

    def test_guard_prevents_insert_if_format_ever_grows_past_limit(self, tmp_path, monkeypatch):
        """Simulates a hypothetical future where the id format grows (e.g. a
        longer program_id becomes possible) past MAX_REQUIREMENT_ID_LENGTH —
        persist_requirement must raise before ever touching the database, not
        insert a truncated row."""
        conn, run_id = _fresh_conn(tmp_path, LONG_PROGRAM_ID)

        artificially_long_program_id = "X" * 90  # forces total length past 96
        conn.execute(
            "INSERT INTO immigration_programs (program_id, country_id, visa_category, name) "
            "VALUES (?, 'AU', 'SKILLED', 'Overflow Test')",
            (artificially_long_program_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="exceeding MAX_REQUIREMENT_ID_LENGTH"):
            persist_requirement(
                conn, program_id=artificially_long_program_id, country_id="AU",
                requirement=REQUIREMENT, document_id="DOC-TEST", document_version=1,
                extraction_run_id=run_id,
            )

        # Nothing must have been inserted — fail fast, fail clean, no partial write.
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM requirements WHERE program_id = ?",
            (artificially_long_program_id,),
        ).fetchone()["n"]
        assert count == 0
        conn.close()
