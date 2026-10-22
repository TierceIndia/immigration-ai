"""Admin-only, read-only applicant visibility.

Deliberately separate from applicants.py/assessments.py: those routes stay
scoped to the authenticated end-user's OWN applicant records (unchanged by
this file). This router gives an admin a cross-user view instead - it never
touches, loosens, or reuses the existing ownership check.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from api.helpers import parse_uuid_or_404
from api.routes.auth import require_admin
from db.connection import get_session, get_db_engine
from db.models import ApiUser, ApplicantProfile, Assessment
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


class AdminApplicantResponse(BaseModel):
    applicant_id: str
    user_id: str
    owner_email: str
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
    created_at: str | None = None


class AdminApplicantListResponse(BaseModel):
    applicants: list[AdminApplicantResponse]
    total: int
    limit: int
    offset: int


class AdminApplicantAssessmentSummary(BaseModel):
    assessment_id: str
    program_id: str
    business_status: str
    overall_status: str
    ruleset_hash: str | None = None
    evaluated_at: str | None = None


class AdminApplicantDetailResponse(AdminApplicantResponse):
    assessments: list[AdminApplicantAssessmentSummary] = []


def _to_response(applicant: ApplicantProfile, owner_email: str) -> AdminApplicantResponse:
    return AdminApplicantResponse(
        applicant_id=applicant.applicant_id,
        user_id=applicant.user_id,
        owner_email=owner_email,
        first_name=applicant.first_name,
        last_name=applicant.last_name,
        age=applicant.age,
        english_level=applicant.english_level,
        points=applicant.points,
        occupation=applicant.occupation,
        skills_assessment_status=applicant.skills_assessment_status,
        health_status=applicant.health_status,
        character_status=applicant.character_status,
        has_regional_nomination=applicant.has_regional_nomination,
        visa_subclass=applicant.visa_subclass,
        visa_history=applicant.visa_history,
        employer_sponsored=applicant.employer_sponsored,
        work_experience_years=applicant.work_experience_years,
        in_australia_at_application=applicant.in_australia_at_application,
        has_health_insurance=applicant.has_health_insurance,
        genuine_temporary_entrant=applicant.genuine_temporary_entrant,
        created_at=applicant.created_at.isoformat() if applicant.created_at else None,
    )


@router.get("", response_model=AdminApplicantListResponse)
def list_applicants(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(require_admin),
):
    """All applicants across all users, paginated, with each one's owner email."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        total = session.execute(select(func.count()).select_from(ApplicantProfile)).scalar() or 0
        rows = session.execute(
            select(ApplicantProfile, ApiUser.email)
            .join(ApiUser, ApiUser.user_id == ApplicantProfile.user_id)
            .order_by(ApplicantProfile.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return AdminApplicantListResponse(
            applicants=[_to_response(applicant, email) for applicant, email in rows],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/{applicant_id}", response_model=AdminApplicantDetailResponse)
def get_applicant(
    applicant_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """One applicant's profile plus their assessment history (result, ruleset
    hash, date) - no owner check, this is the admin cross-user view."""
    parse_uuid_or_404(applicant_id)
    engine = get_db_engine(request)
    with get_session(engine) as session:
        row = session.execute(
            select(ApplicantProfile, ApiUser.email)
            .join(ApiUser, ApiUser.user_id == ApplicantProfile.user_id)
            .where(ApplicantProfile.applicant_id == applicant_id)
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Applicant not found")
        applicant, owner_email = row

        assessments = session.execute(
            select(Assessment)
            .where(Assessment.applicant_id == applicant_id)
            .order_by(Assessment.evaluated_at.desc())
        ).scalars().all()

        base = _to_response(applicant, owner_email)
        return AdminApplicantDetailResponse(
            **base.model_dump(),
            assessments=[
                AdminApplicantAssessmentSummary(
                    assessment_id=a.assessment_id,
                    program_id=a.program_id,
                    business_status=a.business_status,
                    overall_status=a.overall_status,
                    ruleset_hash=a.ruleset_hash,
                    evaluated_at=a.evaluated_at.isoformat() if a.evaluated_at else None,
                )
                for a in assessments
            ],
        )
