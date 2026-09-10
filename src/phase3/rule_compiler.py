"""Phase 3A rule compiler.

Converts approved Phase 2 requirements into declarative Phase 3 rules.
Preserves full provenance. Never generates executable code.
"""
from __future__ import annotations

from phase3.models import Rule, Provenance, RuleStatus
from phase3.operators import SUPPORTED_OPERATORS


def compile_requirement_to_rule(requirement: dict, rule_id: str) -> Rule:
    """Compile an approved Phase 2 requirement into a Phase 3 rule.

    The requirement dict must contain:
        - category, field, operator, value, unit, mandatory
        - evidence list with chunk_id, evidence_text
        - program_id from source context

    Raises ValueError if requirement is missing required fields or has unsupported operator.
    """
    operator = requirement.get("operator", "UNKNOWN")
    if operator not in SUPPORTED_OPERATORS and operator != "UNKNOWN":
        raise ValueError(f"Unsupported operator: {operator}")

    evidence = requirement.get("evidence", [])
    if not evidence:
        raise ValueError("Requirement must have at least one evidence entry")

    first_evidence = evidence[0]

    provenance = Provenance(
        source_id=requirement.get("_source_id", "UNKNOWN"),
        document_id=requirement.get("_document_id", "UNKNOWN"),
        document_version=requirement.get("_document_version", 0),
        chunk_id=first_evidence.get("chunk_id", 0),
        requirement_id=requirement.get("_requirement_id", "UNKNOWN"),
        evidence_text=first_evidence.get("evidence_text", ""),
        requirement_version_id=requirement.get("_requirement_version_id", 0),
    )

    criticality = requirement.get("criticality", "STANDARD")
    critical = criticality == "CRITICAL"

    message = requirement.get("description", requirement.get("name", ""))
    if not message:
        message = f"{requirement.get('category', 'UNKNOWN')} requirement"

    return Rule(
        rule_id=rule_id,
        requirement_id=requirement.get("_requirement_id", "UNKNOWN"),
        program_id=requirement.get("_program_id", "UNKNOWN"),
        category=requirement.get("category", "UNKNOWN"),
        field=requirement.get("field", "UNKNOWN"),
        operator=operator,
        value=requirement.get("value"),
        unit=requirement.get("unit"),
        mandatory=requirement.get("mandatory", True),
        critical=critical,
        message=message,
        provenance=provenance,
        status=RuleStatus.DRAFT,
        version=1,
        condition=requirement.get("condition"),
        exception=requirement.get("exception"),
    )
