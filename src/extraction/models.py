"""Shared enums/dataclasses for Phase 2. Mirrors the CHECK constraints in
schema/phase2_requirements.sql and the enums in schema/requirements.schema.json - keep these
three in sync if the taxonomy changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import Enum


class Category(str, Enum):
    AGE = "AGE"
    OCCUPATION = "OCCUPATION"
    SKILLS_ASSESSMENT = "SKILLS_ASSESSMENT"
    EDUCATION = "EDUCATION"
    WORK_EXPERIENCE = "WORK_EXPERIENCE"
    ENGLISH_LANGUAGE = "ENGLISH_LANGUAGE"
    POINTS = "POINTS"
    INVITATION = "INVITATION"
    NOMINATION = "NOMINATION"
    STATE_REQUIREMENT = "STATE_REQUIREMENT"
    REGIONAL_REQUIREMENT = "REGIONAL_REQUIREMENT"
    HEALTH = "HEALTH"
    CHARACTER = "CHARACTER"
    IDENTITY = "IDENTITY"
    VISA_STATUS = "VISA_STATUS"
    DOCUMENT = "DOCUMENT"
    FAMILY = "FAMILY"
    FINANCIAL = "FINANCIAL"
    APPLICATION = "APPLICATION"
    OTHER = "OTHER"
    DEBT = "DEBT"
    VALUES_STATEMENT = "VALUES_STATEMENT"
    VISA_HISTORY = "VISA_HISTORY"


class Operator(str, Enum):
    EQ = "EQ"
    NEQ = "NEQ"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    BETWEEN = "BETWEEN"
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


class ContentKind(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    FACT = "FACT"
    INFORMATIONAL = "INFORMATIONAL"
    PROCEDURE = "PROCEDURE"
    EXCEPTION = "EXCEPTION"
    CROSS_REFERENCE = "CROSS_REFERENCE"


class Criticality(str, Enum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    NORMAL = "NORMAL"
    INFORMATIONAL = "INFORMATIONAL"


class RequirementStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    CONFLICT = "CONFLICT"


class EvidenceType(str, Enum):
    DIRECT_TEXT = "DIRECT_TEXT"
    TABLE = "TABLE"
    LIST = "LIST"
    CROSS_REFERENCE = "CROSS_REFERENCE"
    MULTI_CHUNK = "MULTI_CHUNK"


class ReviewAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    EDIT = "EDIT"
    MARK_NEEDS_REVIEW = "MARK_NEEDS_REVIEW"
    COMMENT = "COMMENT"


VALID_UNITS = {
    "years", "months", "weeks", "days", "points", "AUD", "percent", None,
}


@dataclass
class EvidenceRef:
    chunk_id: int
    evidence_text: str
    evidence_type: str


@dataclass
class Reference:
    reference_type: str
    target_text: str
    status: str = "UNRESOLVED"


@dataclass
class CandidateRequirement:
    """One item as returned by the LLM, before validation."""
    category: str
    name: str
    content_kind: str
    operator: str
    mandatory: bool | None
    criticality: str
    evidence: list[EvidenceRef]
    description: str | None = None
    field: str | None = None
    value: object = None
    unit: str | None = None
    condition: dict | None = None
    exception: dict | None = None
    applies_to: str | None = None
    confidence: float | None = None
    references: list[Reference] = dataclass_field(default_factory=list)


@dataclass
class ValidationIssue:
    stage: str          # 'SCHEMA' | 'PROVENANCE' | 'EVIDENCE' | 'LOGIC' | 'APPLICABILITY'
    severity: str        # 'REJECT' | 'NEEDS_REVIEW'
    message: str
