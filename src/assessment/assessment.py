"""Phase 3C-3: Eligibility Assessment — ELIGIBLE / INELIGIBLE / INCOMPLETE.

Three business outcomes:
- ELIGIBLE: All applicable mandatory rules PASS
- INELIGIBLE: Any mandatory/critical rule FAIL
- INCOMPLETE: No FAIL, but some required information UNKNOWN

Internal statuses remain PASS/FAIL/UNKNOWN/NOT_APPLICABLE.
Business outcome maps these to ELIGIBLE/INELIGIBLE/INCOMPLETE.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rules.models import Rule, EvaluationStatus, RuleEvaluation
from rules.expressions import ExpressionNode, evaluate_expression
from assessment.applicant import ApplicantProfile


@dataclass
class EligibilityOutcome:
    """Result of an eligibility assessment."""
    business_status: str  # ELIGIBLE | INELIGIBLE | INCOMPLETE
    overall_status: EvaluationStatus  # raw engine status
    evaluations: list[RuleEvaluation]
    passed_rules: list[str]
    failed_rules: list[str]
    unknown_rules: list[str]
    not_applicable_rules: list[str]
    passed_count: int
    failed_count: int
    unknown_count: int
    not_applicable_count: int
    total_count: int


def assess_eligibility(
    expression: ExpressionNode,
    rules_by_id: dict[str, Rule],
    applicant: ApplicantProfile,
) -> EligibilityOutcome:
    """Evaluate an expression tree against an applicant profile.

    1. Convert profile to rule data (None fields excluded → UNKNOWN)
    2. Evaluate expression tree
    3. Map raw statuses to business outcome
    """
    rule_data = applicant.to_rule_data()
    overall_status, evaluations = evaluate_expression(expression, rules_by_id, rule_data)

    passed = [e.rule_id for e in evaluations if e.status == EvaluationStatus.PASS]
    failed = [e.rule_id for e in evaluations if e.status == EvaluationStatus.FAIL]
    unknown = [e.rule_id for e in evaluations if e.status == EvaluationStatus.UNKNOWN]
    not_app = [e.rule_id for e in evaluations if e.status == EvaluationStatus.NOT_APPLICABLE]

    business = _map_to_business_status(overall_status, failed, unknown)

    return EligibilityOutcome(
        business_status=business,
        overall_status=overall_status,
        evaluations=evaluations,
        passed_rules=passed,
        failed_rules=failed,
        unknown_rules=unknown,
        not_applicable_rules=not_app,
        passed_count=len(passed),
        failed_count=len(failed),
        unknown_count=len(unknown),
        not_applicable_count=len(not_app),
        total_count=len(evaluations),
    )


def _map_to_business_status(
    overall: EvaluationStatus,
    failed: list[str],
    unknown: list[str],
) -> str:
    """Map raw evaluation status to business outcome.

    FAIL → INELIGIBLE (takes precedence)
    UNKNOWN only → INCOMPLETE
    PASS only → ELIGIBLE
    """
    if failed:
        return "INELIGIBLE"
    if unknown:
        return "INCOMPLETE"
    return "ELIGIBLE"
