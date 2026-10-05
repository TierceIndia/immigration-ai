"""Phase 3C-1: Applicant Profile data model.

Canonical applicant profile for eligibility assessment.

Key principle: missing fields remain None/null, NOT converted to FAIL.
The rule engine distinguishes "field missing" (UNKNOWN) from "field present and failing" (FAIL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VisaSelection:
    """Visa program selection — required for every assessment."""
    program_id: str  # e.g. "SKILLED_189", "SKILLED_190", "SKILLED_491"

    def to_dict(self) -> dict:
        return {"program_id": self.program_id}

    @classmethod
    def from_dict(cls, d: dict) -> VisaSelection:
        return cls(program_id=d["program_id"])


@dataclass
class ApplicantProfile:
    """Canonical applicant profile.

    Missing fields are None, NOT default values.
    The rule engine will return UNKNOWN for any rule referencing a None field.
    """
    visa: VisaSelection
    age: int | None = None
    english_level: str | None = None  # none|vocational|competent|proficient|superior
    points: int | None = None
    occupation: str | None = None  # ANZSCO code
    skills_assessment_status: str | None = None  # NOT_APPLICABLE|PENDING|VALID|INVALID|EXEMPT
    health_status: str | None = None  # UNKNOWN|PASSED|FAILED
    character_status: str | None = None  # UNKNOWN|PASSED|FAILED
    has_regional_nomination: bool | None = None
    visa_subclass: str | None = None  # e.g. "189", "190", "476"

    def to_dict(self) -> dict:
        """Full serialization including None fields."""
        return {
            "visa": self.visa.to_dict(),
            "age": self.age,
            "english_level": self.english_level,
            "points": self.points,
            "occupation": self.occupation,
            "skills_assessment_status": self.skills_assessment_status,
            "health_status": self.health_status,
            "character_status": self.character_status,
            "has_regional_nomination": self.has_regional_nomination,
            "visa_subclass": self.visa_subclass,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ApplicantProfile:
        """Construct from dictionary. Visa is required."""
        visa_raw = d.get("visa")
        if visa_raw is None:
            raise ValueError("visa is required")
        if isinstance(visa_raw, dict):
            visa = VisaSelection.from_dict(visa_raw)
        else:
            visa = visa_raw
        return cls(
            visa=visa,
            age=d.get("age"),
            english_level=d.get("english_level"),
            points=d.get("points"),
            occupation=d.get("occupation"),
            skills_assessment_status=d.get("skills_assessment_status"),
            health_status=d.get("health_status"),
            character_status=d.get("character_status"),
            has_regional_nomination=d.get("has_regional_nomination"),
            visa_subclass=d.get("visa_subclass"),
        )

    def to_rule_data(self) -> dict[str, Any]:
        """Convert to flat dict for rule engine evaluation.

        Only non-None fields are included.
        This means the rule engine will return UNKNOWN for any rule
        referencing a field that's not in this dict.

        Field mapping:
          - "program_id"  → the visa program (e.g. "SKILLED_189")
          - "visa_subclass" → the actual subclass code (e.g. "189")
        """
        data: dict[str, Any] = {}
        data["program_id"] = self.visa.program_id
        if self.age is not None:
            data["age"] = self.age
        if self.english_level is not None:
            data["english_level"] = self.english_level
        if self.points is not None:
            data["points"] = self.points
        if self.occupation is not None:
            data["occupation"] = self.occupation
        if self.skills_assessment_status is not None:
            data["skills_assessment_status"] = self.skills_assessment_status
        if self.health_status is not None:
            data["health_status"] = self.health_status
        if self.character_status is not None:
            data["character_status"] = self.character_status
        if self.has_regional_nomination is not None:
            data["has_regional_nomination"] = self.has_regional_nomination
        if self.visa_subclass is not None:
            data["visa_subclass"] = self.visa_subclass
        return data
