"""Eligibility service — wraps Phase 3 engine for API use."""
from __future__ import annotations

from typing import Any

from phase3.models import Rule, EvaluationStatus
from phase3.expressions import ExpressionNode, evaluate_expression
from phase3c.applicant import ApplicantProfile, VisaSelection
from phase3c.assessment import assess_eligibility as _assess, EligibilityOutcome
from phase3c.gap_engine import GapEngine, Gap
from phase3c.questions import QuestionBank
from logging_config import get_logger

logger = get_logger(__name__)


def run_eligibility(
    rules: list[Rule],
    expression: ExpressionNode,
    profile_data: dict[str, Any],
    program_id: str,
) -> dict:
    """Run eligibility assessment and return serializable result."""
    visa = VisaSelection(program_id=program_id)
    applicant = ApplicantProfile.from_dict({"visa": visa.to_dict(), **profile_data})

    rules_by_id = {r.rule_id: r for r in rules}
    outcome: EligibilityOutcome = _assess(expression, rules_by_id, applicant)

    result = {
        "business_status": outcome.business_status,
        "overall_status": outcome.overall_status.value,
        "total_rules": outcome.total_count,
        "passed_count": outcome.passed_count,
        "failed_count": outcome.failed_count,
        "unknown_count": outcome.unknown_count,
        "not_applicable_count": outcome.not_applicable_count,
        "rule_evaluations": [e.to_dict() for e in outcome.evaluations],
    }

    logger.info(
        "Eligibility assessed",
        extra={
            "program_id": program_id,
            "business_status": outcome.business_status,
            "total_rules": outcome.total_count,
        },
    )
    return result


def find_gaps(
    rules: list[Rule],
    expression: ExpressionNode,
    profile_data: dict[str, Any],
) -> list[dict]:
    """Find information gaps for an applicant."""
    visa = VisaSelection(program_id="SKILLED_189")
    applicant = ApplicantProfile.from_dict({"visa": visa.to_dict(), **profile_data})
    rules_by_id = {r.rule_id: r for r in rules}
    _, evaluations = evaluate_expression(expression, rules_by_id, applicant.to_rule_data())

    engine = GapEngine()
    gaps: list[Gap] = engine.find_gaps(evaluations, rules_by_id)
    return [
        {
            "field": g.field,
            "priority": g.priority.value if hasattr(g.priority, "value") else str(g.priority),
            "message": g.message,
            "rule_id": g.rule_id,
        }
        for g in gaps
    ]


def get_questions_for_gaps(gaps: list[dict]) -> list[dict]:
    """Generate deterministic questions for gaps."""
    bank = QuestionBank()
    questions = []
    for gap in gaps:
        q = bank.get_question(gap["field"])
        if q:
            questions.append({
                "field": q.field,
                "question": q.question,
                "field_type": q.field_type,
                "options": q.options,
                "priority": gap["priority"],
            })
    return questions
