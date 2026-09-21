"""Assessment service — persist + retrieve assessments.

Loads ACTIVE rules from DB, builds an AND expression tree, runs Phase 3C
eligibility assessment, and persists results (assessment, rule results, gaps).

Pipeline:
    1. Load applicant profile from DB
    2. Load ACTIVE rules for the program from DB
    3. Build AND expression tree from all active rules
    4. Run Phase 3C assessment (applicability → expression engine → eligibility)
    5. Find gaps if INCOMPLETE
    6. Persist assessment + rule results + gaps
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from db.repositories import (
    AssessmentRepo,
    AssessmentRuleResultRepo,
    GapRepo,
    ApplicantRepo,
    RulesetRepo,
)
from phase3.expressions import ANDNode, RuleLeaf
from phase3.models import Rule, RuleStatus, EvaluationStatus
from phase3c.applicant import ApplicantProfile as Phase3CProfile, VisaSelection
from phase3c.assessment import assess_eligibility as _assess, EligibilityOutcome
from phase3c.gap_engine import GapEngine, Gap
from phase3.rule_repository import EnhancedRuleRepo, orm_to_dataclass
from db.models import RuleVersion
from logging_config import get_logger
from sqlalchemy import select

logger = get_logger(__name__)


class AssessmentService:
    def __init__(self, session):
        self._repo = AssessmentRepo(session)
        self._result_repo = AssessmentRuleResultRepo(session)
        self._gap_repo = GapRepo(session)
        self._applicant_repo = ApplicantRepo(session)
        self._rule_repo = EnhancedRuleRepo(session)

    # ------------------------------------------------------------------
    # Core evaluation logic (shared by run_assessment + evaluate_inline)
    # ------------------------------------------------------------------
    def _evaluate(
        self, applicant_id: str, program_id: str,
    ) -> tuple[Phase3CProfile, list[Rule], EligibilityOutcome]:
        """Run the full evaluation pipeline without persisting.

        Returns (applicant_profile, active_rules, outcome).
        Raises ValueError if applicant or rules not found.
        """
        # 1. Load applicant
        applicant_orm = self._applicant_repo.get(applicant_id)
        if not applicant_orm:
            raise ValueError(f"Applicant {applicant_id} not found")

        # 2. Load ACTIVE rules for program
        active_rules_orm = self._rule_repo.list_active(program_id)
        active_rules: list[Rule] = [orm_to_dataclass(r) for r in active_rules_orm]

        if not active_rules:
            raise ValueError(f"No ACTIVE rules found for program {program_id}")

        # 3. Build AND expression from all active rule IDs
        expression = ANDNode(children=[RuleLeaf(rule_id=r.rule_id) for r in active_rules])

        # 4. Build applicant profile for Phase 3C
        visa = VisaSelection(program_id=program_id)
        profile_data = _build_profile_data(applicant_orm)
        applicant = Phase3CProfile.from_dict({"visa": visa.to_dict(), **profile_data})

        # 5. Run Phase 3C assessment
        rules_by_id = {r.rule_id: r for r in active_rules}
        outcome: EligibilityOutcome = _assess(expression, rules_by_id, applicant)

        return applicant, active_rules, outcome

    # ------------------------------------------------------------------
    # Real assessment: evaluate → persist
    # ------------------------------------------------------------------
    def run_assessment(
        self,
        applicant_id: str,
        program_id: str,
        request_id: str | None = None,
    ) -> dict:
        """Run a full eligibility assessment and persist the result."""
        # Capture applicant snapshot before evaluation (frozen profile at assessment time)
        applicant_orm = self._applicant_repo.get(applicant_id)
        applicant_snapshot = _build_profile_data(applicant_orm)

        applicant, active_rules, outcome = self._evaluate(applicant_id, program_id)

        # Resolve each ACTIVE rule to its newest immutable rule_version row. If every rule
        # has one, the assessment snapshots a released ruleset (immutable, idempotent by
        # program+content_hash); otherwise it degrades to a ruleset-free assessment rather
        # than failing (NULL ruleset/rule_version anchors).
        resolved = _resolve_rule_versions(self._repo._s, active_rules)
        all_resolved = resolved is not None and all(v is not None for v in resolved.values())
        ruleset_id = None
        if all_resolved:
            ruleset_id, ruleset_hash = _acquire_ruleset(
                self._repo._s, program_id, resolved
            )
        else:
            ruleset_hash = None

        # 6. Find gaps
        rules_by_id = {r.rule_id: r for r in active_rules}
        gaps: list[Gap] = []
        if outcome.business_status == "INCOMPLETE":
            engine = GapEngine()
            gaps = engine.find_gaps(outcome.evaluations, rules_by_id)

        # 7. Build eligibility result dict for persistence
        eligibility_result = {
            "business_status": outcome.business_status,
            "overall_status": outcome.overall_status.value,
            "total_rules": outcome.total_count,
            "passed_count": outcome.passed_count,
            "failed_count": outcome.failed_count,
            "unknown_count": outcome.unknown_count,
            "not_applicable_count": outcome.not_applicable_count,
            "rule_evaluations": [e.to_dict() for e in outcome.evaluations],
        }

        gap_dicts = [
            {
                "field": g.field,
                "priority": g.priority.value,
                "message": g.question,
                "rule_id": g.source_rule_ids[0] if g.source_rule_ids else None,
                "evidence_text": g.source_evidence,
            }
            for g in gaps
        ]

        # 8. Persist
        result = self.create(
            applicant_id=applicant_id,
            program_id=program_id,
            eligibility_result=eligibility_result,
            active_rules=active_rules,
            gaps=gap_dicts,
            request_id=request_id,
            applicant_snapshot=applicant_snapshot,
            ruleset_hash=ruleset_hash,
            ruleset_id=ruleset_id,
            rule_version_ids=resolved,
        )

        logger.info(
            "Assessment completed",
            extra={
                "assessment_id": result["assessment_id"],
                "applicant_id": applicant_id,
                "program_id": program_id,
                "business_status": result["business_status"],
                "total_rules": result["total_rules"],
            },
        )
        return result

    # ------------------------------------------------------------------
    # Inline evaluation (no persistence)
    # ------------------------------------------------------------------
    def evaluate_inline(self, applicant_id: str, program_id: str) -> dict:
        """Run evaluation without persisting. Used by POST /assessments/evaluate."""
        _, _, outcome = self._evaluate(applicant_id, program_id)

        return {
            "business_status": outcome.business_status,
            "overall_status": outcome.overall_status.value,
            "total_rules": outcome.total_count,
            "passed_count": outcome.passed_count,
            "failed_count": outcome.failed_count,
            "unknown_count": outcome.unknown_count,
            "not_applicable_count": outcome.not_applicable_count,
            "rule_evaluations": [e.to_dict() for e in outcome.evaluations],
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def create(
        self,
        applicant_id: str,
        program_id: str,
        eligibility_result: dict,
        active_rules: list[Rule] | None = None,
        gaps: list[dict] | None = None,
        request_id: str | None = None,
        applicant_snapshot: dict | None = None,
        ruleset_hash: str | None = None,
        ruleset_id: str | None = None,
        rule_version_ids: dict[str, tuple[int, int] | None] | None = None,
    ) -> dict:
        # Build a lookup for rule versions
        rule_version_map = {r.rule_id: r.version for r in (active_rules or [])}

        assessment = self._repo.create(
            applicant_id=applicant_id,
            program_id=program_id,
            business_status=eligibility_result["business_status"],
            overall_status=eligibility_result["overall_status"],
            total_rules=eligibility_result["total_rules"],
            passed_count=eligibility_result["passed_count"],
            failed_count=eligibility_result["failed_count"],
            unknown_count=eligibility_result["unknown_count"],
            not_applicable_count=eligibility_result["not_applicable_count"],
            request_id=request_id,
            applicant_snapshot=applicant_snapshot,
            ruleset_hash=ruleset_hash,
            ruleset_id=ruleset_id,
        )

        rule_version_ids = rule_version_ids or {}

        # Persist individual rule results
        for eval_dict in eligibility_result.get("rule_evaluations", []):
            rid = eval_dict["rule_id"]
            resolved = rule_version_ids.get(rid)
            resolved_id = resolved[0] if resolved else None
            resolved_version = resolved[1] if resolved else None
            self._result_repo.create(
                assessment_id=assessment.assessment_id,
                rule_id=rid,
                rule_version=resolved_version or rule_version_map.get(rid, 1),
                rule_version_id=resolved_id,
                status=eval_dict["status"],
                field=eval_dict.get("field"),
                actual_value=json.dumps(eval_dict.get("actual_value")),
                expected_value=json.dumps(eval_dict.get("expected_value")),
                operator=eval_dict.get("operator"),
                message=eval_dict.get("message"),
                reason=eval_dict.get("reason"),
            )

        # Persist gaps
        for gap in gaps or []:
            self._gap_repo.create(
                assessment_id=assessment.assessment_id,
                field=gap["field"],
                priority=gap["priority"],
                message=gap["message"],
                rule_id=gap.get("rule_id"),
                evidence_text=gap.get("evidence_text"),
            )

        # Flush all pending inserts so get() can query them
        self._repo._s.flush()

        logger.info(
            "Assessment created",
            extra={
                "assessment_id": assessment.assessment_id,
                "business_status": assessment.business_status,
            },
        )
        return self.get(assessment.assessment_id)

    def get(self, assessment_id: str) -> dict | None:
        assessment = self._repo.get(assessment_id)
        if not assessment:
            return None
        results = self._result_repo.list_for_assessment(assessment_id)
        gaps = self._gap_repo.list_for_assessment(assessment_id)
        return {
            "assessment_id": assessment.assessment_id,
            "applicant_id": assessment.applicant_id,
            "program_id": assessment.program_id,
            "business_status": assessment.business_status,
            "overall_status": assessment.overall_status,
            "total_rules": assessment.total_rules,
            "passed_count": assessment.passed_count,
            "failed_count": assessment.failed_count,
            "unknown_count": assessment.unknown_count,
            "not_applicable_count": assessment.not_applicable_count,
            "evaluated_at": assessment.evaluated_at,
            "applicant_snapshot": assessment.applicant_snapshot,
            "ruleset_hash": assessment.ruleset_hash,
            "ruleset_id": assessment.ruleset_id,
            "rule_results": [
                {
                    "result_id": r.result_id,
                    "rule_id": r.rule_id,
                    "rule_version": r.rule_version,
                    "rule_version_id": r.rule_version_id,
                    "status": r.status,
                    "field": r.field,
                    "actual_value": r.actual_value,
                    "expected_value": r.expected_value,
                    "operator": r.operator,
                    "message": r.message,
                    "reason": r.reason,
                }
                for r in results
            ],
            "gaps": [
                {
                    "gap_id": g.gap_id,
                    "field": g.field,
                    "priority": g.priority,
                    "message": g.message,
                    "rule_id": g.rule_id,
                }
                for g in gaps
            ],
        }

    def list_for_applicant(self, applicant_id: str, limit: int = 20) -> list[dict]:
        assessments = self._repo.list_for_applicant(applicant_id, limit=limit)
        return [
            {
                "assessment_id": a.assessment_id,
                "applicant_id": a.applicant_id,
                "program_id": a.program_id,
                "business_status": a.business_status,
                "overall_status": a.overall_status,
                "total_rules": a.total_rules,
                "passed_count": a.passed_count,
                "failed_count": a.failed_count,
                "unknown_count": a.unknown_count,
                "not_applicable_count": a.not_applicable_count,
                "evaluated_at": a.evaluated_at,
            }
            for a in assessments
        ]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _build_profile_data(applicant_orm) -> dict:
    """Convert DB ApplicantProfile ORM into a dict for Phase 3C ApplicantProfile."""
    data = {}
    for field_name in (
        "age", "english_level", "points", "occupation",
        "skills_assessment_status", "health_status", "character_status",
        "has_regional_nomination", "visa_subclass",
    ):
        val = getattr(applicant_orm, field_name, None)
        if val is not None:
            data[field_name] = val
    return data


def _compute_ruleset_hash(program_id: str, rules) -> str:
    """Compute a deterministic hash of the exact rule-version set used in an assessment.

    Accepts a list of ``(rule_id, version_number)`` tuples OR a list of ``phase3.models.Rule``
    dataclasses (both are used by callers/tests). If any rule version changes, the hash changes.
    """
    def _fingerprints(rules):
        for rule in rules:
            if isinstance(rule, (tuple, list)):
                rid, ver = rule
            else:
                rid, ver = rule.rule_id, rule.version
            yield f"{rid}:v{ver}"

    rule_fingerprints = sorted(_fingerprints(rules))
    payload = json.dumps({
        "program_id": program_id,
        "rules": rule_fingerprints,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _resolve_rule_versions(session, rules: list[Rule]) -> dict[str, tuple[int, int] | None]:
    """Map each rule_id to its newest immutable ``rule_versions`` row ``(version_id,
    version_number)``. Rules without any version row (e.g. ACTIVE rules seeded directly in
    tests/legacy data) map to ``None`` so assessments degrade gracefully."""
    resolved: dict[str, tuple[int, int] | None] = {}
    for rule in rules:
        row = session.execute(
            select(RuleVersion.version_id, RuleVersion.version_number)
            .where(RuleVersion.rule_id == rule.rule_id)
            .order_by(RuleVersion.version_number.desc())
            .limit(1)
        ).first()
        resolved[rule.rule_id] = (row[0], row[1]) if row is not None else None
    return resolved


def _acquire_ruleset(session, program_id: str,
                     resolved: dict[str, tuple[int, int] | None]) -> tuple[str | None, str | None]:
    """Create-or-reuse the immutable RELEASED ruleset for the resolved rule-version set.
    Idempotent by (program_id, content_hash). Returns ``(ruleset_id, content_hash)`` or
    ``(None, None)`` when not every rule resolves to a version row."""
    if any(v is None for v in resolved.values()):
        return None, None
    pairs = [(rule_id, resolved[rule_id][1]) for rule_id in resolved]
    content_hash = _compute_ruleset_hash(program_id, pairs)

    repo = RulesetRepo(session)
    existing = repo.get_by_hash(program_id, content_hash)
    if existing is not None:
        return existing.ruleset_id, content_hash

    ruleset = repo.create(program_id, content_hash, rule_count=len(pairs))
    for position, rule_id in enumerate(sorted(rule_id for rule_id, _ in pairs)):
        repo.add_rule(ruleset.ruleset_id, resolved[rule_id][0], position)
    session.flush()
    return ruleset.ruleset_id, content_hash
