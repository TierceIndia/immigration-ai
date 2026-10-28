"""Phase 3C-5: Deterministic Questions — field → question → validation → answer normalization.

Questions are deterministic templates, not LLM-generated.
Answer validation ensures only accepted values pass through.
Normalizer converts user input to canonical form.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class QuestionDefinition:
    """A deterministic question for a missing field."""
    field: str
    text: str
    accepted_values: list[str] | None = None  # None = free-form
    value_type: str = "string"  # "string", "integer", "boolean"
    min_val: int | None = None
    max_val: int | None = None
    required: bool = True

    def validate(self, answer: Any) -> bool:
        """Validate a user answer against this question's constraints."""
        if answer is None:
            return False

        if self.value_type == "integer":
            try:
                val = int(answer)
            except (ValueError, TypeError):
                return False
            if self.min_val is not None and val < self.min_val:
                return False
            if self.max_val is not None and val > self.max_val:
                return False
            return True

        if self.value_type == "boolean":
            return isinstance(answer, bool)

        # String type
        answer_str = str(answer).strip()
        if self.accepted_values is not None:
            return answer_str.lower() in [v.lower() for v in self.accepted_values]
        return len(answer_str) > 0


# Normalization aliases
_ENGLISH_ALIASES = {
    "ielts 5": "vocational",
    "ielts 6": "competent",
    "ielts 7": "competent",
    "ielts 8": "proficient",
    "pte 50": "vocational",
    "pte 65": "competent",
    "pte 79": "proficient",
}


class QuestionBank:
    """Registry of deterministic questions for all user-facing fields."""

    _QUESTIONS: dict[str, QuestionDefinition] = {
        "age": QuestionDefinition(
            field="age",
            text="What is your age?",
            value_type="integer",
            min_val=0,
            max_val=120,
        ),
        "english_level": QuestionDefinition(
            field="english_level",
            text="What is your current English proficiency level?",
            accepted_values=["none", "vocational", "competent", "proficient", "superior"],
            value_type="string",
        ),
        "points": QuestionDefinition(
            field="points",
            text="How many points have you calculated on the Points Test?",
            value_type="integer",
            min_val=0,
            max_val=200,
        ),
        "occupation": QuestionDefinition(
            field="occupation",
            text="What is your occupation ANZSCO code?",
            value_type="string",
        ),
        "skills_assessment_status": QuestionDefinition(
            field="skills_assessment_status",
            text="What is the status of your skills assessment?",
            accepted_values=["NOT_APPLICABLE", "PENDING", "VALID", "INVALID", "EXEMPT"],
            value_type="string",
        ),
        "health_status": QuestionDefinition(
            field="health_status",
            text="Have you completed your health examination?",
            accepted_values=["UNKNOWN", "PASSED", "FAILED"],
            value_type="string",
        ),
        "character_status": QuestionDefinition(
            field="character_status",
            text="Have you completed your character assessment?",
            accepted_values=["UNKNOWN", "PASSED", "FAILED"],
            value_type="string",
        ),
        "has_regional_nomination": QuestionDefinition(
            field="has_regional_nomination",
            text="Do you have a regional nomination?",
            value_type="boolean",
        ),
        "visa_subclass_detail": QuestionDefinition(
            field="visa_subclass_detail",
            text="What visa subclass are you applying for?",
            accepted_values=["189", "190", "491", "476"],
            value_type="string",
        ),
        # First batch of newly modelled applicant concepts (Phase 4).
        "visa_history": QuestionDefinition(
            field="visa_history",
            text="What is your Australian visa history?",
            accepted_values=[
                "NONE", "PRIOR_GRANTED", "PRIOR_REFUSED",
                "PRIOR_CANCELLED", "PRIOR_REMOVED",
            ],
            value_type="string",
        ),
        "employer_sponsored": QuestionDefinition(
            field="employer_sponsored",
            text="Are you sponsored by an employer?",
            value_type="boolean",
        ),
        "work_experience_years": QuestionDefinition(
            field="work_experience_years",
            text="How many years of relevant work experience do you have?",
            value_type="integer",
            min_val=0,
            max_val=80,
        ),
        "in_australia_at_application": QuestionDefinition(
            field="in_australia_at_application",
            text="Will you be in Australia when you lodge your application?",
            value_type="boolean",
        ),
        "has_health_insurance": QuestionDefinition(
            field="has_health_insurance",
            text="Do you hold health insurance that covers you in Australia?",
            value_type="boolean",
        ),
        "genuine_temporary_entrant": QuestionDefinition(
            field="genuine_temporary_entrant",
            text="Do you meet the genuine temporary entrant test?",
            value_type="boolean",
        ),
    }

    @classmethod
    def get_question(cls, field: str) -> QuestionDefinition | None:
        return cls._QUESTIONS.get(field)

    @classmethod
    def all_fields(cls) -> list[str]:
        return list(cls._QUESTIONS.keys())


class AnswerNormalizer:
    """Normalize user answers to canonical values."""

    @staticmethod
    def normalize(field: str, raw_answer: Any) -> Any:
        """Normalize a raw answer based on the field type."""
        if field == "age":
            try:
                return int(raw_answer)
            except (ValueError, TypeError):
                return raw_answer

        if field == "points":
            try:
                return int(raw_answer)
            except (ValueError, TypeError):
                return raw_answer

        if field == "english_level":
            normalized = str(raw_answer).strip().lower()
            return _ENGLISH_ALIASES.get(normalized, normalized)

        if field in ("skills_assessment_status", "health_status", "character_status"):
            return str(raw_answer).strip().upper()

        if field == "has_regional_nomination":
            if isinstance(raw_answer, bool):
                return raw_answer
            return str(raw_answer).strip().lower() in ("yes", "true", "1")

        if field == "visa_history":
            return str(raw_answer).strip().upper()

        if field in ("employer_sponsored", "in_australia_at_application",
                     "has_health_insurance", "genuine_temporary_entrant"):
            if isinstance(raw_answer, bool):
                return raw_answer
            return str(raw_answer).strip().lower() in ("yes", "true", "1")

        if field == "work_experience_years":
            try:
                return int(raw_answer)
            except (ValueError, TypeError):
                return raw_answer

        return raw_answer
