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


# The applicant facts this profile carries. Every entry is something an
# applicant can supply (or explicitly leave empty), which is what makes a
# rule that references one resolvable to PASS/FAIL instead of always UNKNOWN.
#
# This tuple is the single source of truth for the fact list: to_dict(),
# from_dict(), to_rule_data() and the rule engine's evaluability check all
# read it, so a fact cannot be added to one place and forgotten in another.
APPLICANT_FACT_FIELDS: tuple[str, ...] = (
    "age",
    "english_level",
    "points",
    "occupation",
    "skills_assessment_status",
    "health_status",
    "character_status",
    "has_regional_nomination",
    "visa_subclass",
    # --- first batch of newly modelled applicant concepts (Phase 4) ---
    "visa_history",
    "employer_sponsored",
    "work_experience_years",
    "in_australia_at_application",
    "has_health_insurance",
    "genuine_temporary_entrant",
)


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
    # --- first batch of newly modelled applicant concepts (Phase 4) ---
    # Each one is a fact the applicant can supply, so a rule that references
    # it can resolve to PASS/FAIL instead of always UNKNOWN.
    visa_history: str | None = None  # NONE|PRIOR_GRANTED|PRIOR_REFUSED|PRIOR_CANCELLED|PRIOR_REMOVED
    employer_sponsored: bool | None = None
    work_experience_years: int | None = None
    in_australia_at_application: bool | None = None
    has_health_insurance: bool | None = None
    genuine_temporary_entrant: bool | None = None

    def to_dict(self) -> dict:
        """Full serialization including None fields."""
        data: dict[str, Any] = {"visa": self.visa.to_dict()}
        for name in APPLICANT_FACT_FIELDS:
            data[name] = getattr(self, name)
        return data

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
        return cls(visa=visa, **{name: d.get(name) for name in APPLICANT_FACT_FIELDS})

    def to_rule_data(self) -> dict[str, Any]:
        """Convert to flat dict for rule engine evaluation.

        Only non-None fields are included.
        This means the rule engine will return UNKNOWN for any rule
        referencing a field that's not in this dict.

        Field mapping:
          - "program_id"  → the visa program (e.g. "SKILLED_189")
          - "visa_subclass" → the actual subclass code (e.g. "189")
        """
        data: dict[str, Any] = {"program_id": self.visa.program_id}
        for name in APPLICANT_FACT_FIELDS:
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data
