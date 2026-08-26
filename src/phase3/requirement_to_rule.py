"""Bridge: approved Phase 2 requirements → DRAFT Phase 3 rules.

Loads approved requirements from DB, compiles them through the rule compiler,
and persists them as DRAFT rules via the enhanced rule repository.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from db.models import Requirement, RequirementEvidence
from phase3.rule_compiler import compile_requirement_to_rule
from phase3.rule_repository import EnhancedRuleRepo
from logging_config import get_logger

logger = get_logger(__name__)


def _load_requirement_dict(session: Session, req_orm: Requirement) -> dict[str, Any]:
    """Convert a DB Requirement ORM object into the dict format expected by
    compile_requirement_to_rule()."""
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
    return {
        "_requirement_id": req_orm.requirement_id,
        "_program_id": req_orm.program_id,
        "_source_id": "",  # filled by caller if available
        "_document_id": "",
        "_document_version": 0,
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
        requirement_id, rule_id, status ("created" | "updated" | "error"), error
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

        if not dry_run:
            rule_repo.upsert_from_compiled(rule_id, compiled, req_orm.requirement_id)
            rule_repo.save_version_snapshot(rule_id, "CREATED", "requirement_bridge")

        results.append({
            "requirement_id": req_orm.requirement_id,
            "rule_id": rule_id,
            "status": "created",
            "error": None,
        })
        logger.info("Requirement compiled to DRAFT rule",
                     extra={"requirement_id": req_orm.requirement_id,
                            "rule_id": rule_id,
                            "program_id": req_orm.program_id})

    return results
