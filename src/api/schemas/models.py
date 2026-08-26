"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Applicant
# ---------------------------------------------------------------------------
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
    created_at: datetime | None = None


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


class RegisterResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None = None
    role: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None


class UserResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None = None
    role: str
