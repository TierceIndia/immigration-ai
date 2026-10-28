"""Phase 3A rule engine.

Evaluates declarative rules against applicant data.
Deterministic. Auditable. No side effects.
"""
from __future__ import annotations

from typing import Any

from assessment.applicant import APPLICANT_FACT_FIELDS
from rules.models import Rule, RuleEvaluation, EvaluationStatus, EligibilityResult
from rules.operators import evaluate_operator

# Phase 2 extraction (prompts/requirement_extraction_v2.md) documents rule.field
# using dotted names like "applicant.points_score"; ApplicantProfile.to_rule_data()
# (Phase 3C) exposes flat canonical names like "points". A handful of these don't
# match even after stripping the "applicant." prefix — this is the only place that
# reconciles the two conventions, so neither phase needs to change its own contract.
_FIELD_ALIASES: dict[str, str] = {
    "points_score": "points",
    "required_points": "points",
    "skills_assessment": "skills_assessment_status",
    "health_check": "health_status",
    "character_check": "character_status",
}

# The facts an applicant can supply, plus the program the assessment is for.
# A rule whose field resolves to none of these is documentation, not a check:
# no applicant has the fact, so the rule can never reach PASS or FAIL.
MODELLED_APPLICANT_FIELDS: frozenset[str] = frozenset(APPLICANT_FACT_FIELDS) | {
    "program_id",
}


def resolve_field_name(rule_field: str | None) -> str | None:
    """Canonical applicant fact a rule's field refers to.

    Returns None when the field refers to nothing the applicant can supply:
    a blank field, the literal ``NOT_MODELED`` placeholder, or a name outside
    the modelled fact set. The alias table is applied here, so
    ``applicant.points_score`` and ``applicant.required_points`` both resolve
    to ``points``.
    """
    if not rule_field:
        return None
    if rule_field == "NOT_MODELED":
        return None
    normalized = rule_field[len("applicant."):] if rule_field.startswith("applicant.") else rule_field
    normalized = _FIELD_ALIASES.get(normalized, normalized)
    return normalized if normalized in MODELLED_APPLICANT_FIELDS else None


def is_field_evaluable(rule_field: str | None) -> bool:
    """Whether a rule's field can ever be answered by an applicant.

    This is a property of the rule alone, not of any particular applicant:
    it says "somebody could supply this", not "this applicant supplied it".
    A rule that is not evaluable always evaluates to UNKNOWN in practice, so
    the admin UI badges it and requires an explicit confirmation to activate.
    """
    return resolve_field_name(rule_field) is not None


def _resolve_field_value(rule_field: str, applicant_data: dict[str, Any]) -> Any:
    """Look up a rule's field in the applicant-data dict.

    Tries the exact field name first (covers every rule/test using a bare name
    like "age" that already matches ApplicantProfile.to_rule_data() directly),
    then the "applicant."-stripped + aliased name. A field with no mapping to a
    modeled applicant fact (e.g. a program-level fact like invitation validity)
    correctly stays unresolved -> UNKNOWN, not a bug.
    """
    if rule_field in applicant_data:
        return applicant_data[rule_field]
    normalized = rule_field[len("applicant."):] if rule_field.startswith("applicant.") else rule_field
    normalized = _FIELD_ALIASES.get(normalized, normalized)
    return applicant_data.get(normalized)


def evaluate_rule(rule: Rule, applicant_data: dict[str, Any]) -> RuleEvaluation:
    """Evaluate a single rule against applicant data.

    Returns RuleEvaluation with status PASS, FAIL, or UNKNOWN.
    """
    actual_value = _resolve_field_value(rule.field, applicant_data)

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
