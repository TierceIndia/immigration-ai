"""Pydantic request/response schemas for the API."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Applicant
# ---------------------------------------------------------------------------
class VisaIntake(BaseModel):
    """The visa program an applicant is applying for."""
    program_id: str = Field(..., examples=["SKILLED_189", "SKILLED_190", "SKILLED_491"])


# The closed value vocabulary for the applicant's prior Australian visa
# history. Mirrors schema/applicant_intake.schema.json.
VisaHistory = Literal[
    "NONE", "PRIOR_GRANTED", "PRIOR_REFUSED", "PRIOR_CANCELLED", "PRIOR_REMOVED"
]


class ApplicantCreate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: datetime | None = None
    passport_nationality: str | None = None
    current_visa_subclass: str | None = None
    age: int | None = None
    english_level: str | None = None
    points: int | None = None
    occupation: str | None = None
    skills_assessment_status: str | None = None
    health_status: str | None = None
    character_status: str | None = None
    has_regional_nomination: bool | None = None
    visa_subclass: str | None = None
    # First batch of newly modelled applicant concepts (Phase 4).
    visa_history: VisaHistory | None = None
    employer_sponsored: bool | None = None
    work_experience_years: int | None = None
    in_australia_at_application: bool | None = None
    has_health_insurance: bool | None = None
    genuine_temporary_entrant: bool | None = None


class ApplicantUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    age: int | None = None
    english_level: str | None = None
    points: int | None = None
    occupation: str | None = None
    skills_assessment_status: str | None = None
    health_status: str | None = None
    character_status: str | None = None
    has_regional_nomination: bool | None = None
    visa_subclass: str | None = None
    # First batch of newly modelled applicant concepts (Phase 4).
    visa_history: VisaHistory | None = None
    employer_sponsored: bool | None = None
    work_experience_years: int | None = None
    in_australia_at_application: bool | None = None
    has_health_insurance: bool | None = None
    genuine_temporary_entrant: bool | None = None


class ApplicantResponse(BaseModel):
    applicant_id: str
    user_id: str
    first_name: str | None = None
    last_name: str | None = None
    age: int | None = None
    english_level: str | None = None
    points: int | None = None
    occupation: str | None = None
    skills_assessment_status: str | None = None
    health_status: str | None = None
    character_status: str | None = None
    has_regional_nomination: bool | None = None
    visa_subclass: str | None = None
    # First batch of newly modelled applicant concepts (Phase 4).
    visa_history: str | None = None
    employer_sponsored: bool | None = None
    work_experience_years: int | None = None
    in_australia_at_application: bool | None = None
    has_health_insurance: bool | None = None
    genuine_temporary_entrant: bool | None = None
    created_at: datetime | None = None


class ApplicantIntake(BaseModel):
    """The full intake payload for an eligibility assessment.

    Mirrors schema/applicant_intake.schema.json. Every fact except ``visa``
    is optional: an omitted fact stays *missing* (the rule engine answers
    UNKNOWN for it), which is deliberately different from a fact that was
    supplied and does not meet a threshold (FAIL).
    """
    visa: VisaIntake
    age: int | None = Field(default=None, ge=0, le=120)
    english_level: str | None = None
    points: int | None = Field(default=None, ge=0, le=200)
    occupation: str | None = None
    skills_assessment_status: str | None = None
    health_status: str | None = None
    character_status: str | None = None
    has_regional_nomination: bool | None = None
    visa_subclass: str | None = None
    visa_history: VisaHistory | None = None
    employer_sponsored: bool | None = None
    work_experience_years: int | None = Field(default=None, ge=0, le=80)
    in_australia_at_application: bool | None = None
    has_health_insurance: bool | None = None
    genuine_temporary_entrant: bool | None = None


# ---------------------------------------------------------------------------
# Visa Selection
# ---------------------------------------------------------------------------
class VisaSelectionCreate(BaseModel):
    program_id: str = Field(..., examples=["SKILLED_189", "SKILLED_190", "SKILLED_491"])


class VisaSelectionResponse(BaseModel):
    selection_id: str
    applicant_id: str
    program_id: str


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------
class AssessRequest(BaseModel):
    applicant_id: str
    program_id: str


class RuleResultResponse(BaseModel):
    result_id: str
    rule_id: str
    rule_version: int
    rule_version_id: int | None = None
    status: str
    field: str | None = None
    actual_value: str | None = None
    expected_value: str | None = None
    operator: str | None = None
    message: str | None = None
    reason: str | None = None


class GapResponse(BaseModel):
    gap_id: str
    field: str
    priority: str
    message: str
    rule_id: str | None = None
    evidence_text: str | None = None
    answer: str | None = None
    source_chunk_ids: list[int] = []


class AssessmentResponse(BaseModel):
    assessment_id: str
    applicant_id: str
    program_id: str
    business_status: str
    overall_status: str
    total_rules: int
    passed_count: int
    failed_count: int
    unknown_count: int
    not_applicable_count: int
    evaluated_at: datetime | None = None
    applicant_snapshot: dict[str, Any] | None = None
    ruleset_hash: str | None = None
    ruleset_id: str | None = None
    rule_results: list[RuleResultResponse] = []
    gaps: list[GapResponse] = []


class AssessmentListResponse(BaseModel):
    assessments: list[AssessmentResponse]
    total: int


# ---------------------------------------------------------------------------
# Evaluate (inline, no persistence)
# ---------------------------------------------------------------------------
class EvaluateRequest(BaseModel):
    applicant_id: str
    program_id: str


class EvaluateResponse(BaseModel):
    business_status: str
    overall_status: str
    total_rules: int
    passed_count: int
    failed_count: int
    unknown_count: int
    not_applicable_count: int
    rule_evaluations: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Gaps & Questions
# ---------------------------------------------------------------------------
class AnswerRequest(BaseModel):
    field: str
    value: Any


class QuestionsResponse(BaseModel):
    questions: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


class RegisterResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None = None
    role: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PASSWORD_MAX_BYTES = 72  # bcrypt's effective input limit


def password_min_length() -> int:
    try:
        value = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))
    except ValueError:
        value = 8
    return max(1, min(value, _PASSWORD_MAX_BYTES))


def normalise_email(value: str) -> str:
    email = (value or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValueError("email must be a valid address")
    return email


class LoginRequest(BaseModel):
    """Login deliberately has no password-strength rule.

    A wrong short password must reach authentication and produce the same 401 as
    any other wrong password; rejecting it at schema level would leak policy and
    make login behaviour inconsistent with existing accounts.
    """

    email: str
    password: str

    _normalise_login_email = field_validator("email")(normalise_email)


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None

    _normalise_register_email = field_validator("email")(normalise_email)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("password is required")
        if len(value.encode("utf-8")) > _PASSWORD_MAX_BYTES:
            raise ValueError(
                f"password must be at most {_PASSWORD_MAX_BYTES} bytes"
            )
        minimum = password_min_length()
        if len(value) < minimum:
            raise ValueError(f"password must be at least {minimum} characters")
        if value.strip() != value:
            raise ValueError("password must not have leading/trailing whitespace")
        return value

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 128:
            raise ValueError("full_name must be at most 128 characters")
        return value or None


class UserResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None = None
    role: str
