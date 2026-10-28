"""Phase 3A rule data model.

Declarative, serializable, versioned rules derived from approved Phase 2 requirements.
Every rule preserves full provenance back to the government source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RuleStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class EvaluationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Provenance:
    """Traceability back to government source."""
    source_id: str
    document_id: str
    document_version: int
    chunk_id: int
    requirement_id: str
    evidence_text: str
    # canonical audit lineage: the exact immutable requirement_versions row the rule was
    # compiled from (0/None when the rule predates requirement lineage tracking)
    requirement_version_id: int = 0


@dataclass
class Rule:
    """A single declarative rule compiled from an approved requirement.

    Rules are never executed as code. They are evaluated by the rule engine
    which interprets operator + value against applicant data.
    """
    rule_id: str
    requirement_id: str
    program_id: str
    category: str
    field: str
    operator: str
    value: Any
    unit: str | None
    mandatory: bool
    critical: bool
    message: str
    provenance: Provenance
    status: RuleStatus = RuleStatus.DRAFT
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int = 1
    condition: dict | None = None
    exception: dict | None = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "requirement_id": self.requirement_id,
            "program_id": self.program_id,
            "category": self.category,
            "field": self.field,
            "operator": self.operator,
            "value": self.value,
            "unit": self.unit,
            "mandatory": self.mandatory,
            "critical": self.critical,
            "message": self.message,
            "provenance": {
                "source_id": self.provenance.source_id,
                "document_id": self.provenance.document_id,
                "document_version": self.provenance.document_version,
                "chunk_id": self.provenance.chunk_id,
                "requirement_id": self.provenance.requirement_id,
                "evidence_text": self.provenance.evidence_text,
                "requirement_version_id": self.provenance.requirement_version_id,
            },
            "status": self.status.value,
            "created_at": self.created_at,
            "version": self.version,
            "condition": self.condition,
            "exception": self.exception,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Rule:
        prov = d["provenance"]
        return cls(
            rule_id=d["rule_id"],
            requirement_id=d["requirement_id"],
            program_id=d["program_id"],
            category=d["category"],
            field=d["field"],
            operator=d["operator"],
            value=d["value"],
            unit=d.get("unit"),
            mandatory=d["mandatory"],
            critical=d["critical"],
            message=d["message"],
            provenance=Provenance(
                source_id=prov["source_id"],
                document_id=prov["document_id"],
                document_version=prov["document_version"],
                chunk_id=prov["chunk_id"],
                requirement_id=prov["requirement_id"],
                evidence_text=prov["evidence_text"],
                requirement_version_id=prov.get("requirement_version_id", 0),
            ),
            status=RuleStatus(d.get("status", "DRAFT")),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            version=d.get("version", 1),
            condition=d.get("condition"),
            exception=d.get("exception"),
        )


@dataclass(frozen=True)
class RuleEvaluation:
    """Result of evaluating a single rule against applicant data."""
    rule_id: str
    status: EvaluationStatus
    field: str
    actual_value: Any
    expected_value: Any
    operator: str
    message: str
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "field": self.field,
            "actual_value": self.actual_value,
            "expected_value": self.expected_value,
            "operator": self.operator,
            "message": self.message,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EligibilityResult:
    """Aggregate result of evaluating all rules for a program."""
    program_id: str
    overall_status: EvaluationStatus
    rule_evaluations: list[RuleEvaluation]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    total: int

    def to_dict(self) -> dict:
        return {
            "program_id": self.program_id,
            "overall_status": self.overall_status.value,
            "rule_evaluations": [r.to_dict() for r in self.rule_evaluations],
            "passed": self.passed,
            "failed": self.failed,
            "unknown": self.unknown,
            "not_applicable": self.not_applicable,
            "total": self.total,
        }
