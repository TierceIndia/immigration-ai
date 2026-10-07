"""Bridge: approved Phase 2 requirements → DRAFT Phase 3 rules.

Loads approved requirements from DB, compiles them through the rule compiler,
and persists them as DRAFT rules via the enhanced rule repository.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from db.models import (
    DocumentVersion,
    ExtractionRun,
    GovernmentDocument,
    Requirement,
    RequirementEvidence,
    RequirementVersion,
)
from rules.rule_compiler import compile_requirement_to_rule
from rules.rule_repository import EnhancedRuleRepo
from logging_config import get_logger

logger = get_logger(__name__)

# Must match extraction.requirement_repository.MAX_REQUIREMENT_ID_LENGTH and
# db.models.Rule.rule_id's column width (see migration a3c5e7f9b1d3). Kept as
# its own constant here rather than importing across extraction/rules — the
# extraction module relies on src/extraction/ being separately added to sys.path by
# utils/fetch_document.py before its own flat internal imports resolve, and
# this file must not depend on that import-order side effect to be safe.
MAX_RULE_ID_LENGTH = 96


def _load_requirement_dict(session: Session, req_orm: Requirement) -> dict[str, Any]:
    """Convert a DB Requirement ORM object into the dict format expected by
    compile_requirement_to_rule().

    Provenance is resolved from the real extraction lineage: the requirement's extraction
    run -> its exact immutable document version -> the owning government source, so every
    compiled rule carries genuine source/document/version anchors instead of blanks."""
    evidence_rows = (
        session.query(RequirementEvidence)
        .filter(RequirementEvidence.requirement_id == req_orm.requirement_id)
        .all()
    )
    evidence_list = [
        {
            "chunk_id": e.chunk_id,
            "evidence_text": e.evidence_text,
            "evidence_type": e.evidence_type,
        }
        for e in evidence_rows
    ]

    source_id = ""
    document_id = ""
    document_version = 0
    document_version_id = req_orm.document_version_id
    if document_version_id is None and req_orm.extraction_run_id:
        run = session.get(ExtractionRun, req_orm.extraction_run_id)
        if run is not None and run.document_version_id:
            document_version_id = run.document_version_id
    if document_version_id is not None:
        ver = session.get(DocumentVersion, document_version_id)
        if ver is not None:
            document_id = ver.document_id
            document_version = ver.version_number
            doc = session.get(GovernmentDocument, ver.document_id)
            if doc is not None:
                source_id = doc.source_id

    requirement_version_id = (
        session.query(RequirementVersion.version_id)
        .filter(RequirementVersion.requirement_id == req_orm.requirement_id)
        .order_by(RequirementVersion.version_number.desc())
        .first()
    )
    requirement_version_id = requirement_version_id[0] if requirement_version_id else 0

    return {
        "_requirement_id": req_orm.requirement_id,
        "_program_id": req_orm.program_id,
        "_requirement_version_id": requirement_version_id,
        "_source_id": source_id,
        "_document_id": document_id,
        "_document_version": document_version,
        "category": req_orm.category,
        "name": req_orm.name,
        "description": req_orm.description or "",
        "field": req_orm.field or "",
        "operator": req_orm.operator or "",
        "value": json.loads(req_orm.value_json) if req_orm.value_json else None,
        "unit": req_orm.unit,
        "mandatory": req_orm.mandatory,
        "criticality": req_orm.criticality or "NORMAL",
        "evidence": evidence_list,
        "condition": json.loads(req_orm.condition_json) if req_orm.condition_json else None,
        "exception": json.loads(req_orm.exception_json) if req_orm.exception_json else None,
    }


def compile_approved_requirements(
    session: Session,
    program_id: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Load all APPROVED requirements from DB, compile each into a DRAFT rule.

    Returns a list of result dicts with keys:
        requirement_id, rule_id, status ("created" | "unchanged" | "error"), error
    """
    rule_repo = EnhancedRuleRepo(session)
    results = []

    stmt = session.query(Requirement).filter(Requirement.status == "APPROVED")
    if program_id:
        stmt = stmt.filter(Requirement.program_id == program_id)
    requirements = stmt.all()

    for req_orm in requirements:
        req_dict = _load_requirement_dict(session, req_orm)
        rule_id = f"RULE-{req_orm.requirement_id}"

        try:
            # rule_id is derived from requirement_id (db.models.Rule.rule_id is
            # widened to match requirement_id's width — see migration
            # a3c5e7f9b1d3), but a defensive check here means a too-long id is
            # still a clear, immediate error instead of a silent PostgreSQL
            # truncation — same failure-safe guarantee as
            # requirement_repository._checked_requirement_id, and it's caught by
            # the same except below so one bad requirement never stops the batch.
            if len(rule_id) > MAX_RULE_ID_LENGTH:
                raise ValueError(
                    f"generated rule_id is {len(rule_id)} chars, exceeding "
                    f"MAX_RULE_ID_LENGTH={MAX_RULE_ID_LENGTH}: {rule_id!r}"
                )
            compiled = compile_requirement_to_rule(req_dict, rule_id)
        except Exception as e:
            logger.warning("Failed to compile requirement",
                           extra={"requirement_id": req_orm.requirement_id,
                                  "error": str(e)})
            results.append({
                "requirement_id": req_orm.requirement_id,
                "rule_id": rule_id,
                "status": "error",
                "error": str(e),
            })
            continue

        if dry_run:
            # Preview only - must reflect what upsert_from_compiled would
            # actually report, not assume everything is new. Previously
            # hardcoded True, so a dry-run on an already-synced program
            # claimed every requirement would be "created" even when nothing
            # would really change.
            changed = rule_repo.would_change_from_compiled(
                rule_id, compiled, req_orm.requirement_id
            )
        else:
            _, changed = rule_repo.upsert_from_compiled(rule_id, compiled, req_orm.requirement_id)
            # Only record a version snapshot (and report "created" below) when
            # something actually changed - recompiling a requirement into a
            # byte-for-byte identical, already-existing rule is a real no-op,
            # not a new CREATED event. Found live: resyncing a source whose
            # program already had approved requirements was spamming a fresh
            # "CREATED" rule_versions row every sync even though the rule was
            # untouched (25 version rows for 3 rules that had genuinely
            # changed only twice each).
            if changed:
                rule_repo.save_version_snapshot(rule_id, "CREATED", "requirement_bridge")

        results.append({
            "requirement_id": req_orm.requirement_id,
            "rule_id": rule_id,
            "status": "created" if changed else "unchanged",
            "error": None,
        })
        if changed:
            logger.info("Requirement compiled to DRAFT rule",
                         extra={"requirement_id": req_orm.requirement_id,
                                "rule_id": rule_id,
                                "program_id": req_orm.program_id})

    return results
