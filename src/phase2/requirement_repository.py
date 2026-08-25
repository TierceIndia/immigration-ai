"""Persistence layer for Phase 2.

Idempotency contract: running extraction twice against the same (document_id, document_version)
must not create uncontrolled duplicate requirement rows. This is enforced via `semantic_key`
(requirement_deduplicator.semantic_key) - EXACT_DUPLICATE candidates only add a new evidence row
(and, if evidence set changed, a requirement_versions row of change_type=UNCHANGED/MODIFIED); they
never insert a second `requirements` row for the same semantic key + program.

Each call to `run_extraction.py` creates one `extraction_runs` row so every requirement can be
traced to exactly which run (model, prompt version, timestamp) produced or confirmed it.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from requirement_deduplicator import decide, semantic_key


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    return f"RUN-{uuid.uuid4().hex[:12]}"


def create_extraction_run(conn, document_id: str, document_version: int, model: str,
                            prompt_version: str, dry_run: bool) -> str:
    run_id = new_run_id()
    conn.execute(
        """
        INSERT INTO extraction_runs
            (run_id, document_id, document_version, started_at, model, prompt_version,
             status, dry_run)
        VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?)
        """,
        (run_id, document_id, document_version, _now_iso(), model, prompt_version, 1 if dry_run else 0),
    )
    conn.commit()
    return run_id


def complete_extraction_run(conn, run_id: str, counters: dict, status: str = "COMPLETED",
                              error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE extraction_runs SET
            completed_at = ?, chunks_seen = ?, candidate_chunks = ?, llm_calls = ?,
            requirements_extracted = ?, validated = ?, needs_review = ?, rejected = ?,
            evidence_failures = ?, schema_failures = ?, status = ?, error = ?
        WHERE run_id = ?
        """,
        (
            _now_iso(), counters.get("chunks_seen", 0), counters.get("candidate_chunks", 0),
            counters.get("llm_calls", 0), counters.get("requirements_extracted", 0),
            counters.get("validated", 0), counters.get("needs_review", 0),
            counters.get("rejected", 0), counters.get("evidence_failures", 0),
            counters.get("schema_failures", 0), status, error, run_id,
        ),
    )
    conn.commit()


def _fetch_existing_by_semantic_key(conn, program_id: str, sem_key: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM requirements
        WHERE program_id = ? AND semantic_key = ? AND status != 'REJECTED'
        ORDER BY updated_at DESC
        """,
        (program_id, sem_key),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "requirement_id": r["requirement_id"],
            "operator": r["operator"],
            "value": json.loads(r["value_json"]) if r["value_json"] else None,
            "unit": r["unit"],
        })
    return out


def persist_requirement(conn, *, program_id: str, country_id: str, requirement: dict,
                          document_id: str, document_version: int, extraction_run_id: str,
                          dry_run: bool = False) -> dict:
    """Persist one validated+normalized requirement dict (schema-shaped, already evidence-verified).
    Returns a dict describing what happened: {"action": ..., "requirement_id": ...}.
    In dry_run mode, computes and returns the decision WITHOUT writing to the database.
    """
    category = requirement["category"]
    field = requirement.get("field")
    name = requirement["name"]
    sem_key = semantic_key(program_id, category, field, name)

    existing_matches = _fetch_existing_by_semantic_key(conn, program_id, sem_key)
    candidate_for_dedup = {
        "operator": requirement["operator"],
        "value": requirement.get("value"),
        "unit": requirement.get("unit"),
    }
    decision = decide(candidate_for_dedup, existing_matches)

    if dry_run:
        return {"action": f"WOULD_{decision.relationship}", "semantic_key": sem_key,
                "matched_requirement_id": decision.matched_requirement_id, "reason": decision.reason}

    now = _now_iso()

    if decision.relationship == "NEW":
        requirement_id = f"REQ-{program_id}-{sem_key}"
        conn.execute(
            """
            INSERT INTO requirements
                (requirement_id, semantic_key, program_id, country_id, category, name, description,
                 field, operator, value_json, unit, mandatory, condition_json, exception_json,
                 applies_to, content_kind, confidence, criticality, status, current_version,
                 extraction_run_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', 1, ?, ?, ?)
            """,
            (
                requirement_id, sem_key, program_id, country_id, category, name,
                requirement.get("description"), field, requirement["operator"],
                json.dumps(requirement.get("value")), requirement.get("unit"),
                requirement.get("mandatory"),
                json.dumps(requirement.get("condition")) if requirement.get("condition") else None,
                json.dumps(requirement.get("exception")) if requirement.get("exception") else None,
                requirement.get("applies_to"), requirement.get("content_kind", "REQUIREMENT"),
                requirement.get("confidence"), requirement.get("criticality", "NORMAL"),
                extraction_run_id, now, now,
            ),
        )
        _insert_evidence(conn, requirement_id, requirement["evidence"], document_id, document_version, now)
        _insert_references(conn, requirement_id, requirement.get("references", []))
        _insert_version(conn, requirement_id, 1, requirement, document_version, "INITIAL", now)
        conn.commit()
        return {"action": "CREATED", "requirement_id": requirement_id, "semantic_key": sem_key}

    if decision.relationship == "EXACT_DUPLICATE":
        requirement_id = decision.matched_requirement_id
        _insert_evidence(conn, requirement_id, requirement["evidence"], document_id, document_version, now)
        conn.execute("UPDATE requirements SET updated_at = ? WHERE requirement_id = ?", (now, requirement_id))
        conn.commit()
        return {"action": "EVIDENCE_ADDED", "requirement_id": requirement_id, "semantic_key": sem_key}

    if decision.relationship == "COMPLEMENTARY":
        requirement_id = decision.matched_requirement_id
        _insert_evidence(conn, requirement_id, requirement["evidence"], document_id, document_version, now)
        conn.execute("UPDATE requirements SET updated_at = ? WHERE requirement_id = ?", (now, requirement_id))
        conn.commit()
        return {"action": "COMPLEMENTARY_EVIDENCE_ADDED", "requirement_id": requirement_id,
                "semantic_key": sem_key, "reason": decision.reason}

    # CONFLICT
    requirement_id = f"REQ-{program_id}-{sem_key}-{uuid.uuid4().hex[:6]}"
    conn.execute(
        """
        INSERT INTO requirements
            (requirement_id, semantic_key, program_id, country_id, category, name, description,
             field, operator, value_json, unit, mandatory, condition_json, exception_json,
             applies_to, content_kind, confidence, criticality, status, current_version,
             extraction_run_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFLICT', 1, ?, ?, ?)
        """,
        (
            requirement_id, sem_key, program_id, country_id, category, name,
            requirement.get("description"), field, requirement["operator"],
            json.dumps(requirement.get("value")), requirement.get("unit"),
            requirement.get("mandatory"),
            json.dumps(requirement.get("condition")) if requirement.get("condition") else None,
            json.dumps(requirement.get("exception")) if requirement.get("exception") else None,
            requirement.get("applies_to"), requirement.get("content_kind", "REQUIREMENT"),
            requirement.get("confidence"), requirement.get("criticality", "NORMAL"),
            extraction_run_id, now, now,
        ),
    )
    # also flip the pre-existing matched requirement to CONFLICT so a reviewer sees both sides
    conn.execute(
        "UPDATE requirements SET status = 'CONFLICT', updated_at = ? WHERE requirement_id = ?",
        (now, decision.matched_requirement_id),
    )
    _insert_evidence(conn, requirement_id, requirement["evidence"], document_id, document_version, now)
    _insert_version(conn, requirement_id, 1, requirement, document_version, "CONFLICTING", now)
    conn.commit()
    return {"action": "CONFLICT_RECORDED", "requirement_id": requirement_id,
            "conflicts_with": decision.matched_requirement_id, "semantic_key": sem_key,
            "reason": decision.reason}


def _insert_evidence(conn, requirement_id, evidence_list, document_id, document_version, now):
    for ev in evidence_list:
        existing = conn.execute(
            """
            SELECT 1 FROM requirement_evidence
            WHERE requirement_id = ? AND chunk_id = ? AND evidence_text = ? AND document_version = ?
            """,
            (requirement_id, ev["chunk_id"], ev["evidence_text"], document_version),
        ).fetchone()
        if existing:
            continue  # identical evidence already recorded for this requirement+chunk+version - no-op (idempotency)
        conn.execute(
            """
            INSERT INTO requirement_evidence
                (requirement_id, document_id, document_version, chunk_id, evidence_text,
                 evidence_type, verified, verification_note, extraction_method, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 'verified: exact substring match', 'LLM_EXTRACTION', ?)
            """,
            (requirement_id, document_id, document_version, ev["chunk_id"], ev["evidence_text"],
             ev["evidence_type"], now),
        )


def _insert_references(conn, requirement_id, references):
    for ref in references:
        conn.execute(
            """
            INSERT INTO requirement_references (requirement_id, reference_type, target_text, status)
            VALUES (?, ?, ?, ?)
            """,
            (requirement_id, ref.get("reference_type", "LEGAL_CROSS_REFERENCE"),
             ref["target_text"], ref.get("status", "UNRESOLVED")),
        )


def _insert_version(conn, requirement_id, version_number, requirement, document_version, change_type, now):
    conn.execute(
        """
        INSERT INTO requirement_versions
            (requirement_id, version_number, snapshot_json, document_version, change_type,
             created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, 'system')
        """,
        (requirement_id, version_number, json.dumps(requirement), document_version, change_type, now),
    )
