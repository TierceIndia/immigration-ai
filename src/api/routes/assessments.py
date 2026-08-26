"""Assessment routes — run + retrieve eligibility assessments."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.schemas.models import (
    AssessRequest,
    AssessmentResponse,
    AssessmentListResponse,
    EvaluateRequest,
    EvaluateResponse,
)
from db.connection import get_session
from logging_config import get_logger
from services.assessment_service import AssessmentService

router = APIRouter()
logger = get_logger(__name__)


@router.post("", response_model=AssessmentResponse, status_code=201)
def create_assessment(body: AssessRequest, request: Request):
    """Run eligibility assessment and persist result."""
    # TODO: load rules + expression tree from DB (Sprint P3 full impl)
    # For now, return a stub
    with get_session(request.app.state.db_engine) as session:
        svc = AssessmentService(session)
        # Placeholder — real impl loads rules from DB
        raise HTTPException(
            status_code=501,
            detail="Rule loading from DB not yet implemented. Use /evaluate for in-memory assessment.",
        )


@router.get("", response_model=AssessmentListResponse)
def list_assessments(request: Request, applicant_id: str, limit: int = 20):
    with get_session(request.app.state.db_engine) as session:
        svc = AssessmentService(session)
        results = svc.list_for_applicant(applicant_id, limit=limit)
        return AssessmentListResponse(assessments=results, total=len(results))


@router.get("/{assessment_id}", response_model=AssessmentResponse)
def get_assessment(assessment_id: str, request: Request):
    with get_session(request.app.state.db_engine) as session:
        svc = AssessmentService(session)
        result = svc.get(assessment_id)
        if not result:
            raise HTTPException(status_code=404, detail="Assessment not found")
        return result


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate_inline(body: EvaluateRequest, request: Request):
    """In-memory eligibility check (no persistence)."""
    # TODO: load rules + expression from DB
    raise HTTPException(
        status_code=501,
        detail="Rule loading from DB not yet implemented.",
    )
