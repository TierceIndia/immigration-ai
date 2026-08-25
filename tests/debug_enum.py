"""Test: replicate the exact models.py structure"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class RequirementCategory(str, Enum):
    AGE = "AGE"
    OTHER = "OTHER"

class Operator(str, Enum):
    EQ = "EQ"
    UNKNOWN = "UNKNOWN"

class EvidenceType(str, Enum):
    DIRECT_TEXT = "DIRECT_TEXT"
    TABLE = "TABLE"
    LIST = "LIST"
    CROSS_REFERENCE = "CROSS_REFERENCE"
    MULTI_CHUNK = "MULTI_CHUNK"

class Criticality(str, Enum):
    NORMAL = "NORMAL"

class RequirementStatus(str, Enum):
    DRAFT = "DRAFT"

class AppliesTo(str, Enum):
    APPLICANT = "applicant"

@dataclass
class ConditionExpr:
    field: str
    operator: Operator
    value: Any

@dataclass
class CrossReference:
    reference_type: str
    target: str

@dataclass
class ExtractedRequirement:
    additional_chunk_ids: list[int] = field(default_factory=list)
    cross_references: list[CrossReference] = field(default_factory=list)

@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

print("All OK!")
r = ExtractedRequirement()
print("ExtractedRequirement:", r.additional_chunk_ids)
v = ValidationResult()
print("ValidationResult:", v.errors)
