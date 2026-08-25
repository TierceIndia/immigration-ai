"""Phase 3A rule engine.

Evaluates declarative rules against applicant data.
Deterministic. Auditable. No side effects.
"""
from __future__ import annotations

from typing import Any

from phase3.models import Rule, RuleEvaluation, EvaluationStatus, EligibilityResult
from phase3.operators import evaluate_operator


def evaluate_rule(rule: Rule, applicant_data: dict[str, Any]) -> RuleEvaluation:
    """Evaluate a single rule against applicant data.

    Returns RuleEvaluation with status PASS, FAIL, or UNKNOWN.
    """
    actual_value = applicant_data.get(rule.field)

    if rule.operator in ("EXISTS", "NOT_EXISTS"):
        passed, reason = evaluate_operator(rule.operator, actual_value, None)
        status = EvaluationStatus.PASS if passed else EvaluationStatus.FAIL
        return RuleEvaluation(
            rule_id=rule.rule_id,
            status=status,
            field=rule.field,
            actual_value=actual_value,
            expected_value=rule.value,
            operator=rule.operator,
            message=rule.message,
            reason=reason,
        )

    if actual_value is None:
        return RuleEvaluation(
            rule_id=rule.rule_id,
            status=EvaluationStatus.UNKNOWN,
            field=rule.field,
            actual_value=None,
            expected_value=rule.value,
            operator=rule.operator,
            message=rule.message,
            reason=f"Field '{rule.field}' not provided in applicant data",
        )

    passed, reason = evaluate_operator(rule.operator, actual_value, rule.value)
    status = EvaluationStatus.PASS if passed else EvaluationStatus.FAIL

    return RuleEvaluation(
        rule_id=rule.rule_id,
        status=status,
        field=rule.field,
        actual_value=actual_value,
        expected_value=rule.value,
        operator=rule.operator,
        message=rule.message,
        reason=reason,
    )


def evaluate_all_rules(
    rules: list[Rule],
    applicant_data: dict[str, Any],
    program_id: str,
) -> EligibilityResult:
    """Evaluate all rules for a program against applicant data.

    NOT_APPLICABLE rules are filtered out before overall status logic.

    Overall status logic (over applicable rules only):
        - FAIL if any mandatory rule fails
        - UNKNOWN if any mandatory rule is UNKNOWN and none fail
        - PASS if all mandatory rules pass
        - NOT_APPLICABLE if all rules are NOT_APPLICABLE
    """
    evaluations = []
    for rule in rules:
        evaluations.append(evaluate_rule(rule, applicant_data))

    passed = sum(1 for e in evaluations if e.status == EvaluationStatus.PASS)
    failed = sum(1 for e in evaluations if e.status == EvaluationStatus.FAIL)
    unknown = sum(1 for e in evaluations if e.status == EvaluationStatus.UNKNOWN)
    not_applicable = sum(1 for e in evaluations if e.status == EvaluationStatus.NOT_APPLICABLE)
    total = len(evaluations)

    applicable = total - not_applicable

    if applicable == 0:
        overall = EvaluationStatus.NOT_APPLICABLE
    elif failed > 0:
        overall = EvaluationStatus.FAIL
    elif unknown > 0:
        overall = EvaluationStatus.UNKNOWN
    else:
        overall = EvaluationStatus.PASS

    return EligibilityResult(
        program_id=program_id,
        overall_status=overall,
        rule_evaluations=evaluations,
        passed=passed,
        failed=failed,
        unknown=unknown,
        not_applicable=not_applicable,
        total=total,
    )
