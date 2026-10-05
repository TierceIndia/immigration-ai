"""Deduplication / conflict detection.

Creates a stable "semantic key" per requirement (program + category + field, normalized) so the
same real-world requirement extracted from different pages (overview page, points table,
legislation) can be recognized as "the same requirement" without deleting any of the evidence.

Duplicates are NEVER auto-merged or auto-deleted. This module only classifies the relationship
between a new candidate and any existing requirement(s) sharing its semantic key:

  - EXACT_DUPLICATE   - same field/operator/value/unit -> just add the new evidence, don't
                         create a second requirement row.
  - COMPLEMENTARY      - same field, compatible values (e.g. one source states the number, another
                         only states "there is an age requirement" with no number) -> keep both,
                         link via semantic_key, prefer the more specific one, no conflict.
  - CONFLICT           - same field, incompatible operator/value from two PRIMARY sources -> keep
                         both rows, mark status=CONFLICT, force NEEDS_REVIEW. Authority order
                         (legislation > program page > guidance) informs the reviewer's default
                         but never auto-resolves the conflict.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


def semantic_key(program_id: str, category: str, field: str | None, name: str) -> str:
    basis = f"{program_id}|{category}|{(field or '').lower()}|{re.sub(r'[^a-z0-9]+', '_', name.lower())}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class DedupDecision:
    relationship: str  # 'NEW' | 'EXACT_DUPLICATE' | 'COMPLEMENTARY' | 'CONFLICT'
    matched_requirement_id: str | None
    reason: str


AUTHORITY_RANK = {
    "LEGISLATION_MIGRATION_REGS": 0,
    # skilled program pages/guidance are treated equally at rank 1 in Phase 2 - Phase 1's
    # per-source `priority` (PRIMARY/SECONDARY) is the finer-grained signal a reviewer sees.
}


def _values_conflict(op1, val1, unit1, op2, val2, unit2) -> bool:
    if op1 == "UNKNOWN" or op2 == "UNKNOWN":
        return False
    if op1 != op2:
        return True
    if unit1 and unit2 and unit1 != unit2:
        return True
    return val1 != val2


def decide(candidate: dict, existing_matches: list[dict]) -> DedupDecision:
    """candidate / existing_matches: dicts with keys operator, value, unit (already normalized).
    existing_matches: rows already in `requirements` sharing the same semantic_key, most recent first.
    """
    if not existing_matches:
        return DedupDecision("NEW", None, "no existing requirement with this semantic key")

    for existing in existing_matches:
        if (
            candidate["operator"] == existing["operator"]
            and candidate.get("value") == existing.get("value")
            and candidate.get("unit") == existing.get("unit")
        ):
            return DedupDecision(
                "EXACT_DUPLICATE", existing["requirement_id"],
                "identical operator/value/unit to an existing requirement - adding evidence only",
            )

    for existing in existing_matches:
        if candidate.get("operator") == "UNKNOWN" or existing.get("operator") == "UNKNOWN":
            return DedupDecision(
                "COMPLEMENTARY", existing["requirement_id"],
                "one side is UNKNOWN (unspecified value) - not a conflict, both kept",
            )

    for existing in existing_matches:
        if _values_conflict(
            candidate["operator"], candidate.get("value"), candidate.get("unit"),
            existing["operator"], existing.get("value"), existing.get("unit"),
        ):
            return DedupDecision(
                "CONFLICT", existing["requirement_id"],
                f"incompatible values: new={candidate['operator']} {candidate.get('value')} "
                f"{candidate.get('unit')} vs existing={existing['operator']} {existing.get('value')} "
                f"{existing.get('unit')} - human review required, both evidence sets preserved",
            )

    return DedupDecision(
        "COMPLEMENTARY", existing_matches[0]["requirement_id"],
        "same semantic key, compatible values - treated as corroborating evidence",
    )
