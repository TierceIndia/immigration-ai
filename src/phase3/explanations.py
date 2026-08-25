"""Phase 3A rule explanations.

Generates human-readable explanations of rule evaluations.
Auditable. Traceable to source evidence.
"""
from __future__ import annotations

from phase3.models import RuleEvaluation, EligibilityResult, EvaluationStatus


def explain_evaluation(evaluation: RuleEvaluation, rule=None) -> str:
    """Generate a human-readable explanation of a single rule evaluation."""
    if evaluation.status == EvaluationStatus.PASS:
        return (
            f"PASS: {evaluation.message}. "
            f"Field '{evaluation.field}' value {evaluation.actual_value!r} "
            f"satisfies {evaluation.operator} {evaluation.expected_value!r}."
        )
    if evaluation.status == EvaluationStatus.FAIL:
        return (
            f"FAIL: {evaluation.message}. "
            f"Field '{evaluation.field}' value {evaluation.actual_value!r} "
            f"does not satisfy {evaluation.operator} {evaluation.expected_value!r}. "
            f"{evaluation.reason}."
        )
    if evaluation.status == EvaluationStatus.UNKNOWN:
        return (
            f"UNKNOWN: {evaluation.message}. "
            f"Field '{evaluation.field}' was not provided. "
            f"Cannot determine if {evaluation.operator} {evaluation.expected_value!r} is satisfied."
        )
    return f"ERROR: {evaluation.message}. {evaluation.reason}"


def explain_eligibility(result: EligibilityResult) -> str:
    """Generate a human-readable summary of an eligibility assessment."""
    lines = [f"Eligibility for {result.program_id}: {result.overall_status.value}"]
    lines.append(f"  Passed: {result.passed}/{result.total}")
    lines.append(f"  Failed: {result.failed}/{result.total}")
    lines.append(f"  Unknown: {result.unknown}/{result.total}")
    lines.append("")

    for evaluation in result.rule_evaluations:
        lines.append(f"  {explain_evaluation(evaluation)}")

    return "\n".join(lines)
