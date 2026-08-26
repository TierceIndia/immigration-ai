"""Assessment service — persist + retrieve assessments."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from db.repositories import (
    AssessmentRepo,
    AssessmentRuleResultRepo,
    GapRepo,
)
from logging_config import get_logger

logger = get_logger(__name__)


class AssessmentService:
    def __init__(self, session):
        self._repo = AssessmentRepo(session)
        self._result_repo = AssessmentRuleResultRepo(session)
        self._gap_repo = GapRepo(session)

    def create(
        self,
        applicant_id: str,
        program_id: str,
        eligibility_result: dict,
        gaps: list[dict] | None = None,
        request_id: str | None = None,
    ) -> dict:
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
        )

        # Persist individual rule results
        for eval_dict in eligibility_result.get("rule_evaluations", []):
            self._result_repo.create(
                assessment_id=assessment.assessment_id,
                rule_id=eval_dict["rule_id"],
                rule_version=1,
                status=eval_dict["status"],
                field=eval_dict.get("field"),
                actual_value=str(eval_dict.get("actual_value", "")),
                expected_value=str(eval_dict.get("expected_value", "")),
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
            )

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
            "rule_results": [
                {
                    "result_id": r.result_id,
                    "rule_id": r.rule_id,
                    "rule_version": r.rule_version,
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
