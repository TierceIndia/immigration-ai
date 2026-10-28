"""Phase 3C-2: Rule Applicability — determine which rules apply to an applicant.

Filtering pipeline:
1. Program filter: rule.program_id == applicant.visa.program_id
2. Status filter: rule.status == ACTIVE
3. Version selection: for each unique rule_id, select highest ACTIVE version
"""
from __future__ import annotations

from typing import Sequence

from rules.models import Rule, RuleStatus
from assessment.applicant import ApplicantProfile


def filter_applicable_rules(
    rules: Sequence[Rule],
    applicant: ApplicantProfile,
) -> list[Rule]:
    """Filter rules to only those applicable to this applicant.

    Pipeline:
    1. Match program_id to applicant's visa program
    2. Filter to ACTIVE status only
    """
    program = applicant.visa.program_id
    return [
        r for r in rules
        if r.program_id == program and r.status == RuleStatus.ACTIVE
    ]


def select_rule_version(
    rules: Sequence[Rule],
    rule_id: str,
) -> Rule | None:
    """Select the highest ACTIVE version of a specific rule.

    Returns None if no ACTIVE version exists.
    """
    candidates = [
        r for r in rules
        if r.rule_id == rule_id and r.status == RuleStatus.ACTIVE
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.version)
