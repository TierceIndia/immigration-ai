"""Admin rule checker (Phase 4).

A dry-run of the rules against a hypothetical applicant, with no side
effects: no LLM call, no network request, no database write, no audit row.

This is the diagnostic an admin uses *before* activating a rule, to answer
"what would this rule actually do to a real applicant?". The answers are
deterministic - the same rules and the same profile always produce the same
report - so two admins comparing notes are always looking at the same thing.

Every rule gets a status and a plain-English explanation. Rules whose field
is not something any applicant can supply are flagged ``evaluable: false``:
they are real requirements from the source document, but they can never
resolve to PASS or FAIL, so an admin should know that before activating one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from assessment.gap_engine import Gap, GapEngine
from rules.explanations import explain_evaluation
from rules.models import EvaluationStatus, Rule
from rules.rule_engine import evaluate_rule, is_field_evaluable
from rules.ruleset_hash import compute_ruleset_hash


@dataclass
class RuleCheckResult:
    """The outcome of checking one rule against the supplied profile."""
    rule_id: str
    requirement_id: str | None
    status: str
    explanation: str
    field: str | None
    operator: str | None
    expected_value: Any
    actual_value: Any
    reason: str | None
    message: str | None
    mandatory: bool
    critical: bool
    evaluable: bool

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "requirement_id": self.requirement_id,
            "status": self.status,
            "explanation": self.explanation,
            "field": self.field,
            "operator": self.operator,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "reason": self.reason,
            "message": self.message,
            "mandatory": self.mandatory,
            "critical": self.critical,
            "evaluable": self.evaluable,
        }


@dataclass
class RuleCheckReport:
    """The full report for one check run."""
    program_id: str
    overall_status: str
    business_status: str
    total_rules: int
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    ruleset_hash: str
    evaluated_at: str
    results: list[RuleCheckResult] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "program_id": self.program_id,
            "overall_status": self.overall_status,
            "business_status": self.business_status,
            "total_rules": self.total_rules,
            "passed": self.passed,
            "failed": self.failed,
            "unknown": self.unknown,
            "not_applicable": self.not_applicable,
            "ruleset_hash": self.ruleset_hash,
            "evaluated_at": self.evaluated_at,
            "results": [r.to_dict() for r in self.results],
            "gaps": [
                {
                    "field": g.field,
                    "priority": g.priority.value if hasattr(g.priority, "value") else g.priority,
                    "question": g.question,
                    "rule_ids": g.source_rule_ids,
                    "source_evidence": g.source_evidence,
                }
                for g in self.gaps
            ],
        }


def _business_status(overall: EvaluationStatus, failed: int, unknown: int) -> str:
    """Map raw statuses onto the business outcome vocabulary.

    FAIL wins over UNKNOWN: a known failure is decisive even when other
    information is still missing. This mirrors
    ``assessment.assessment._map_to_business_status`` so a dry run and a real
    assessment never disagree about the verdict.
    """
    if overall == EvaluationStatus.NOT_APPLICABLE:
        return "NOT_APPLICABLE"
    if failed:
        return "INELIGIBLE"
    if unknown:
        return "INCOMPLETE"
    return "ELIGIBLE"


def check_rules(
    rules: list[Rule],
    applicant_data: dict[str, Any],
    program_id: str,
) -> RuleCheckReport:
    """Check ``rules`` against ``applicant_data``.

    Deterministic and side-effect free. The ruleset hash fingerprints exactly
    which rule versions produced the report, so a report can be tied to the
    rules it was computed from.
    """
    results: list[RuleCheckResult] = []
    evaluations = []
    rules_by_id: dict[str, Rule] = {}

    for rule in rules:
        evaluation = evaluate_rule(rule, applicant_data)
        evaluations.append(evaluation)
        rules_by_id[rule.rule_id] = rule

        evaluable = is_field_evaluable(rule.field)
        explanation = explain_evaluation(evaluation, rule)
        if not evaluable:
            explanation = (
                f"{explanation} No applicant field supplies '{rule.field}', "
                f"so this rule can never resolve to PASS or FAIL - it stays UNKNOWN "
                f"for every applicant."
            )

        results.append(RuleCheckResult(
            rule_id=rule.rule_id,
            requirement_id=rule.requirement_id,
            status=evaluation.status.value,
            explanation=explanation,
            field=rule.field,
            operator=rule.operator,
            expected_value=rule.value,
            actual_value=evaluation.actual_value,
            reason=evaluation.reason,
            message=rule.message,
            mandatory=bool(rule.mandatory),
            critical=bool(rule.critical),
            evaluable=evaluable,
        ))

    passed = sum(1 for e in evaluations if e.status == EvaluationStatus.PASS)
    failed = sum(1 for e in evaluations if e.status == EvaluationStatus.FAIL)
    unknown = sum(1 for e in evaluations if e.status == EvaluationStatus.UNKNOWN)
    not_applicable = sum(1 for e in evaluations if e.status == EvaluationStatus.NOT_APPLICABLE)

    if passed + failed + unknown == 0:
        overall = EvaluationStatus.NOT_APPLICABLE
    elif failed:
        overall = EvaluationStatus.FAIL
    elif unknown:
        overall = EvaluationStatus.UNKNOWN
    else:
        overall = EvaluationStatus.PASS

    gaps = GapEngine.find_gaps(evaluations, rules_by_id)

    return RuleCheckReport(
        program_id=program_id,
        overall_status=overall.value,
        business_status=_business_status(overall, failed, unknown),
        total_rules=len(rules),
        passed=passed,
        failed=failed,
        unknown=unknown,
        not_applicable=not_applicable,
        ruleset_hash=compute_ruleset_hash(program_id, rules),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        results=results,
        gaps=gaps,
    )
