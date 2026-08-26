"""Assessment routes — run + retrieve eligibility assessments."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.routes.auth import get_current_user_id
from api.schemas.models import (
    AssessRequest,
    AssessmentResponse,
    AssessmentListResponse,
    EvaluateRequest,
    EvaluateResponse,
)
from db.connection import get_session, get_db_engine
from logging_config import get_logger
from services.assessment_service import AssessmentService

router = APIRouter()
logger = get_logger(__name__)


@router.post("", response_model=AssessmentResponse, status_code=201)
def create_assessment(
    body: AssessRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """Run eligibility assessment and persist result.

    Full pipeline: Load ACTIVE rules → build expression tree → Phase 3C
    assessment → gap engine → persist assessment + rule results + gaps.
    """
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AssessmentService(session)
        try:
            result = svc.run_assessment(
                applicant_id=body.applicant_id,
                program_id=body.program_id,
                request_id=request.headers.get("X-Request-ID"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return result


@router.get("", response_model=AssessmentListResponse)
def list_assessments(request: Request, applicant_id: str, limit: int = 20):
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AssessmentService(session)
        results = svc.list_for_applicant(applicant_id, limit=limit)
        return AssessmentListResponse(assessments=results, total=len(results))


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(assessment_id: str, request: Request):
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AssessmentService(session)
        result = svc.get(assessment_id)
        if not result:
            raise HTTPException(status_code=404, detail="Assessment not found")
        return result


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate_inline(body: EvaluateRequest, request: Request):
    """In-memory eligibility check (no persistence).

    Runs the full pipeline — load ACTIVE rules, build expression tree,
    Phase 3C assessment — but does not save results to DB.
    """
    engine = get_db_engine(request)
    with get_session(engine) as session:
        svc = AssessmentService(session)
        try:
            return svc.evaluate_inline(
                applicant_id=body.applicant_id,
                program_id=body.program_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
